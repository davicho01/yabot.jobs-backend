# Yabot Jobs Backend

FastAPI backend for submitting job posting URLs, scanning/extracting structured
job data from them (schema.org JSON-LD, regex fallback, and optionally an
LLM), tracking applications, and — bring-your-own-key — reviewing/scoring
resumes and generating tailored, ATS-friendly versions of them per job.

## Prerequisites

- Python 3.13+
- Docker (for Postgres and, for local dev, the Pub/Sub emulator and MinIO)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the environment variables below into a `.env` file at the repo root,
then start the database and run migrations:

```bash
docker compose up -d postgres
alembic upgrade head
```

## Environment variables

Set these in `.env` (loaded automatically by `app/core/config.py`).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | No | `postgresql+psycopg://postgres:postgres@localhost:5432/yabot_jobs` | Points at the `postgres` service in `docker-compose.yml`, which maps host port `5433` → container `5432`, e.g. `postgresql+psycopg://postgres:postgres@localhost:5433/yabot_jobs`. |
| `API_KEY_ENCRYPTION_KEY` | **Yes** | — | Fernet key used to encrypt stored per-user LLM API keys at rest. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `BROWSER_FETCH_SERVICE_URL` | No | unset | URL of the headless-Chromium `yabot.jobs-browser` service (deployed separately, see `deploy/gcloud-deploy.sh`). Used as a fallback when a direct fetch of a job posting URL is blocked (403, bot-detection challenge, etc) or needs JS rendering. Scanning still works without it — it just fails on sites that block plain HTTP clients. |
| `SYSTEM_LLM_PROVIDER` | No | unset | LLM provider used for job-posting extraction on every scan (user-submitted or crawler-discovered) — e.g. `anthropic`, `openai`, `deepseek`, `google`, `mistral`, `other`. Extraction is skipped (heuristic-only) when unset. |
| `SYSTEM_LLM_MODEL` | No | unset | Model id, e.g. `claude-opus-5`. Optional only for `anthropic` (defaults to `claude-opus-5`); required for every other provider. |
| `SYSTEM_LLM_API_KEY` | No | unset | API key for `SYSTEM_LLM_PROVIDER`. |
| `SYSTEM_LLM_BASE_URL` | No | unset | Only needed when `SYSTEM_LLM_PROVIDER=other` (a custom OpenAI-compatible endpoint). |
| `GCP_PROJECT_ID` | **Yes** | — | GCP project used for Pub/Sub queueing. Any string works against the local emulator (e.g. `local-dev`); use your real project id when pointing at real GCP. |
| `PUBSUB_TOPIC_ID` | No | `job-scan-requests` | Pub/Sub topic name for individual job scans. |
| `PUBSUB_SUBSCRIPTION_ID` | No | `job-scan-requests-worker` | Pub/Sub pull-subscription name, consumed by `worker.py`. |
| `PUBSUB_CRAWL_TOPIC_ID` | No | `crawl-source-requests` | Separate Pub/Sub topic for crawl-source dispatch (see Discovery crawler below), consumed by `crawl_worker.py`. |
| `PUBSUB_CRAWL_SUBSCRIPTION_ID` | No | `crawl-source-requests-worker` | Pull-subscription name for the crawl topic. |
| `PUBSUB_EMULATOR_HOST` | No | unset | Read directly by the `google-cloud-pubsub` SDK (not a `Settings` field). Set to `localhost:8085` to use the local emulator instead of real GCP. |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | unset | Read directly by the SDK. Path to a service-account JSON key with Pub/Sub publisher + subscriber roles. Only needed when *not* using the emulator (i.e. `PUBSUB_EMULATOR_HOST` is unset) and not running on GCP compute with ambient credentials. |
| `MAGIC_LINK_TTL_MINUTES` | No | `15` | How long an emailed magic sign-in link is valid. |
| `SESSION_TTL_DAYS` | No | `30` | How long a session cookie stays valid. |
| `FRONTEND_BASE_URL` | No | `http://localhost:3000` | Where the emailed magic link points the browser (a frontend route that reads `?token=...` and POSTs it to `/auth/verify`). Also used for CORS `allow_origins`. |
| `SESSION_COOKIE_NAME` | No | `session_token` | |
| `SESSION_COOKIE_SECURE` | No | `false` | Set `true` behind HTTPS in real deployments. |
| `ADMIN_EMAILS` | No | unset | Comma-separated emails auto-promoted to the `admin` role on login/creation. There's no admin UI to grant the role — this is the only way to create one. |
| `RESUME_STORAGE_BUCKET` | No | `yabot-resumes` | S3(-compatible) bucket for uploaded resumes and generated tailored-resume files. |
| `RESUME_STORAGE_ENDPOINT_URL` | No | unset | Set to `http://localhost:9000` to use the local MinIO (see below) instead of real AWS S3. |
| `RESUME_STORAGE_REGION` | No | `us-east-1` | |
| `RESUME_STORAGE_ACCESS_KEY_ID` | **Yes** | — | `minioadmin` for local MinIO; a real AWS access key otherwise. |
| `RESUME_STORAGE_SECRET_ACCESS_KEY` | **Yes** | — | `minioadmin` for local MinIO; a real AWS secret otherwise. |

**Pub/Sub setup — pick one:**

- **Local emulator (recommended for local dev):**
  ```
  GCP_PROJECT_ID=local-dev
  PUBSUB_EMULATOR_HOST=localhost:8085
  ```
  No real GCP project or credentials needed. Start it with:
  ```bash
  docker compose up -d pubsub-emulator
  ```
- **Real GCP project** (local or in the cloud): set `GCP_PROJECT_ID` to your
  actual project id, don't set `PUBSUB_EMULATOR_HOST`, and set
  `GOOGLE_APPLICATION_CREDENTIALS` to a service-account key (skip the
  credentials var entirely if running on GCP compute with an attached
  identity). Same code path either way — the SDK switches automatically
  based on whether `PUBSUB_EMULATOR_HOST` is set.

**LLM extraction uses one system-wide key (`SYSTEM_LLM_*` above), not a
per-user one**, for every job scan — that's the free part the app offers
everyone. The per-user LLM key mechanism (`/api-keys` endpoints, encrypted
at rest with `API_KEY_ENCRYPTION_KEY`, a key markable `is_default`) is
"bring your own key" instead: it's what the [resume features](#resumes)
below actually run on, so the operator never pays for a user's resume
review/scoring/generation calls, only for job crawling/scanning.

**Resume storage setup — pick one:**

- **Local MinIO (recommended for local dev):**
  ```
  RESUME_STORAGE_ENDPOINT_URL=http://localhost:9000
  RESUME_STORAGE_ACCESS_KEY_ID=minioadmin
  RESUME_STORAGE_SECRET_ACCESS_KEY=minioadmin
  ```
  No real AWS account needed. Start it with:
  ```bash
  docker compose up -d minio
  ```
  The bucket is created automatically on API startup (`ensure_bucket_exists()`).
- **Real AWS S3**: remove `RESUME_STORAGE_ENDPOINT_URL` and set the access
  key/secret to real AWS credentials with access to `RESUME_STORAGE_BUCKET`.
  Same code path either way.

## Running locally

**Everything in Docker (simplest — one command, no manual terminals):**
```bash
docker compose up -d
```
This builds one image (`Dockerfile`) and runs `postgres`, `pubsub-emulator`,
a one-shot `migrate` service (`alembic upgrade head`, then exits), `api`
(`uvicorn`, port `8000`), `worker` (scan worker), and `crawl-worker`
(discovery-crawl worker) — all wired together on the compose network
(`DATABASE_URL`/`PUBSUB_EMULATOR_HOST` point at the `postgres`/
`pubsub-emulator` service names, not `localhost`; `.env` still supplies
secrets like `API_KEY_ENCRYPTION_KEY` via `env_file`). `api`/`worker`/
`crawl-worker` wait for `migrate` to finish successfully before starting.

Scale either worker to parallelize a backlog (same competing-consumer
Pub/Sub pattern either way):
```bash
docker compose up -d --scale worker=3 --scale crawl-worker=2
```
`docker compose logs -f worker` (or `api`/`crawl-worker`) to follow logs;
`docker compose down` to stop everything (data persists in the
`yabot_jobs_pgdata` volume; add `-v` to also wipe it).

**Or run it directly on your machine** (useful for iterating on code without
rebuilding an image each time) — three processes, each in its **own
terminal window/tab**, since `uvicorn` and `python worker.py` both block and
run forever:

**Terminal 1 — infra (detached, returns immediately):**
```bash
docker compose up -d postgres pubsub-emulator
```

**Terminal 2 — API:**
```bash
uvicorn main:app --reload
```

**Terminal 3 — scan worker:**
```bash
python worker.py
```

`POST /jobs` returns `202` with the URL queued for scanning; poll
`GET /jobs/{url_id}` until `scan_status` is `success` or `failed`.

## Discovery crawler

Besides user-submitted URLs, the app can automatically discover new postings
from a company's career board across every ATS platform it recognizes. Each
platform's adapter lives in its own file under `app/services/adapters/` (see
`app/services/adapters/__init__.py` for the full registry) — currently
Greenhouse, Lever, Ashby, BambooHR, Personio, Workday, JazzHR, Recruitee,
Breezy HR, Workable, and ADP (multi-tenant ATS platforms used by many
companies); Amazon, Google, and Apple (single-company, in-house career sites
— see note below); and Oracle Fusion, Clinch, and Eightfold (white-label
platforms — the board lives on the company's own domain rather than a
shared ATS host). Most use a real public API (no scraping, no
bot-detection risk); JazzHR, Google, and Apple have no public API, so their
discovery scrapes structured data out of the page instead — more fragile
than the others (see notes below). Either way this only *discovers* URLs;
each one is handed to the same scan pipeline as a manually-submitted URL, so
extraction logic lives in exactly one place.

**Add a board to watch — just paste a careers/job URL:**
```bash
curl -X POST http://localhost:8000/admin/crawl-sources \
  -H "Content-Type: application/json" -H "Cookie: session_token=..." \
  -d '{"name": "BambooHR", "board_url": "https://job-boards.greenhouse.io/bamboohr17/jobs/6004765004"}'
```
`ats_type` (and whatever internal identifier that platform's adapter needs)
is auto-detected from the URL's shape — any job URL or the board's own
listing URL both work, including a specific Workday job URL (all three of
its identifier parts are still present in the path). If the URL doesn't
match a supported platform (e.g. an iCIMS/Jibe board like GitHub's — see
below), you'll get a `422` telling you so.

**JazzHR has no public API, unlike most of the others.** Its `/apply/jobs`
listing page is scraped for job IDs via regex — deterministic today, but
unlike a real API there's no contract, so a JazzHR page redesign could
silently stop finding new postings until someone notices and updates the
adapter (`app/services/adapters/jazzhr.py`).

**Workday is different from the rest in two ways.** It needs three pieces of
information, not one — company slug, Workday instance number (e.g. `wd12`,
not visible in the careers URL; find it by opening the company's careers
page and checking the URL/network requests), and the career site name, all
three recovered straight out of the URL. And rather than returning every
open role, it only returns postings whose `postedOn` is literally `"Posted
Today"` (Workday's own field), since some companies have 1000+ open roles
and re-discovering all of them every day would mean dozens of paginated
requests for no benefit — already-known URLs are deduped either way, so
daily runs only need what's new. A 200-posting safety cap still applies in
case one company posts an unusually large batch in a single day.

**ADP, Oracle Fusion, Clinch, and Eightfold each have their own quirk.** ADP
needs two identifiers, not one — `cid` and `ccId`, both only visible in the
careers URL's query string. Oracle Fusion and Clinch are white-label — the
board lives on the company's own domain rather than a shared ATS host, so
their `CrawlSource.board_url` is stored exactly as submitted instead of
being reconstructed into a canonical form (see
`app/services/adapters/oracle_fusion.py`/`clinch.py`). Eightfold is
white-label too, but with no static URL shape to detect it by at all — it's
only ever recognized by fetching the page and checking for a
platform-specific signature, then guess-and-verifying the tenant's internal
"domain" identifier against its own API (`app/services/adapters/eightfold.py`).

**Amazon, Google, and Apple are single-company, in-house career sites, not
platforms other companies use** — there's no board-specific identifier at
all, `ats_type` alone identifies the whole board. None have a real public
API:

- **Amazon** (`amazon.jobs/en/search.json`) is at least a clean,
  unauthenticated JSON endpoint (verified: 10,000+ live postings) — same
  "today only" `posted_date`-based cutoff as Workday, capped at 200/day.
- **Google** (`google.com/about/careers/.../jobs/results`) has no API at
  all — the search-results page server-renders real job links directly into
  static HTML, scraped via regex. There's no posting-date field anywhere
  (listing or detail page) to filter by, so instead of an exact "today only"
  cutoff this just takes the first 10 pages (200 jobs) under `sort_by=date`
  as a "recent enough" window — already-known URLs are deduped either way.
- **Apple** (`jobs.apple.com/en-us/search`) also has no API — job data is
  server-rendered into a `window.__staticRouterHydrationData =
  JSON.parse("...")` blob (a React Router hydration payload). More fragile
  than a real endpoint since it depends on that internal blob's shape
  rather than a documented contract, but the data itself is clean JSON, not
  raw HTML — and it does have a real per-job date field (`postingDate`), so
  the same "today only" cutoff as Workday/Amazon applies, capped at 200/day.

**Boards are also auto-registered from single job submissions.** Every
`POST /jobs` call runs the URL through the same `detect_ats_source()` used
above. A URL on a platform we already support silently adds (or reuses) an
`"active"` `CrawlSource` for that company — so simply submitting one job
from a new company is enough for the daily crawler to start picking up all
of that company's future postings, no separate `POST /admin/crawl-sources` call
needed. A URL on a platform we don't recognize is instead recorded as a
`"pending"` source keyed by domain (one row per unrecognized site, however
many people submit from it) — a queue of platforms worth investigating. This
never blocks or fails the job submission itself; it's best-effort
bookkeeping only.

`GET /admin/crawl-sources` lists every board with `last_crawled_at`/
`last_error`, plus `status`:

| `status` | Meaning |
|---|---|
| `pending` | Detected platform isn't implemented yet (`ats_type` is null; `board_url` is just the site's domain root) — a candidate for a future adapter. |
| `active` | Platform is implemented; crawled on every dispatch. |
| `rejected` | Investigated and found unsupportable (no viable public API) — won't be re-flagged by future submissions from the same domain. |

`PATCH /admin/crawl-sources/{id}` moves a board along this lifecycle: once an
adapter for a `pending` platform is verified and implemented (a new file
under `app/services/adapters/` exporting an `AtsAdapter`, registered in
`app/services/adapters/__init__.py`), set `board_url` to a URL that
resolves under the new adapter and `status: "active"` in the same request —
`ats_type` is re-derived from `board_url` automatically, and the request is
rejected with `422` if it still doesn't resolve to a supported platform. If
a platform turns out unsupportable instead, `PATCH` with just `status:
"rejected"`. `DELETE` removes a source entirely.

`POST /admin/crawl-sources/{id}/crawl` publishes a one-off crawl request for
a single `active` source — the same message the daily `crawl_dispatcher.py`
fan-out sends, picked up and processed by `crawl_worker.py` exactly like a
scheduled run. Useful for verifying one company's adapter (or re-crawling
after fixing it) without waiting for the next scheduled dispatch or
triggering every other active source too.

**Terminal 4 — crawl worker** (alongside the three processes above; already
running as its own service if you used `docker compose up -d` instead):
```bash
python crawl_worker.py
```

**Trigger a crawl pass** (reads active sources, fans out one Pub/Sub message
per source to `crawl_worker.py`, then exits):
```bash
python crawl_dispatcher.py
```
Running this via Docker instead: `docker compose run --rm api python crawl_dispatcher.py`.

Running a crawl pass is idempotent — already-known job URLs are silently
skipped (same dedup as manual submission), so scheduling it is safe however
often you like. For daily crawling, point any scheduler at
`crawl_dispatcher.py` — a plain cron entry works locally/self-hosted:

```cron
0 6 * * * cd /path/to/yabot.jobs-backend && /path/to/.venv/bin/python crawl_dispatcher.py
```

In the cloud, a GCP Cloud Scheduler job triggering a Cloud Run Job (or any
equivalent scheduled-task mechanism) works the same way — the script doesn't
care how it's invoked. Running multiple `crawl_worker.py` processes crawls
multiple companies in parallel (same competing-consumer Pub/Sub pattern as
`worker.py`), so this scales to many boards by just running more workers.

## Resumes

Upload a resume, get it reviewed, score it against a job, and generate a
tailored, ATS-friendly version of it — all built on the user's **own** LLM
key (`POST /api-keys`, `is_default: true`), never the system-wide one used
for job scanning. Every endpoint below 422s with a clear message if the
user hasn't added a default key yet.

A user can upload several resumes, but exactly one is flagged `is_main` at
a time (their first upload is auto-flagged; `PATCH` another to switch) —
the review/score/tailor endpoints always operate on whichever one is
currently main, so there's no `resume_id` to pass for those:

```bash
# Upload (PDF or DOCX, 5MB max) — first upload becomes the main resume automatically
curl -X POST http://localhost:8000/resumes \
  -H "Cookie: session_token=..." -F "file=@resume.pdf"

# List / switch main / delete
curl http://localhost:8000/resumes -H "Cookie: session_token=..."
curl -X PATCH http://localhost:8000/resumes/{id} -H "Content-Type: application/json" \
  -H "Cookie: session_token=..." -d '{"is_main": true}'
curl -X DELETE http://localhost:8000/resumes/{id} -H "Cookie: session_token=..."

# General feedback on the main resume (not tied to any job)
curl -X POST http://localhost:8000/resumes/main/review -H "Cookie: session_token=..."

# How well the main resume matches a specific job posting, 0-100 + keyword gaps
curl -X POST "http://localhost:8000/resumes/main/score?job_posting_id=..." -H "Cookie: session_token=..."

# Generate a resume tailored to that job, then download the rendered .docx
curl -X POST "http://localhost:8000/resumes/main/tailored?job_posting_id=..." -H "Cookie: session_token=..."
curl "http://localhost:8000/resumes/tailored/{id}/download" -H "Cookie: session_token=..." -o tailored.docx
```

Review/score/tailored calls run **synchronously** (a single request, no
Pub/Sub queue) — expect roughly 5-30s per call depending on the provider,
since this is a single BYOK-gated action per user rather than the bulk,
system-key-funded crawling job scanning does. Tailored output is
deliberately plain (standard headings, no tables/columns/graphics) since
that's what most ATS parsers actually handle reliably, not a designed PDF.

## Database migrations

```bash
alembic upgrade head                  # apply
alembic revision -m "description"     # create a new migration
```
