# Implementing a pending crawl source

A runbook for resolving one `CrawlSource` row with `status: "pending"` —
either by implementing a new ATS adapter for it, recognizing it as a
platform we already support, marking it `"rejected"`, or flagging it
`"delete"` if the row itself is a stale duplicate. Written so any
LLM (or human)
with repo access, shell access, and a web-fetching tool can follow it
end-to-end without additional guidance. Read `app/services/adapters/base.py`'s
`AtsAdapter` docstring and two or three existing adapters (`ashby.py` for a
guess-and-verify + browser-fallback example, `jazzhr.py` for regex-scraping a
server-rendered page, `clinch.py` for a sitemap-based, verbatim-URL
white-label platform) before starting — this doc tells you *what* to do and
*in what order*; the existing adapters show you *how the code should look*.

## 0. Background

`CrawlSource` rows are the discovery crawler's worklist (see
`app/models/crawl_source.py`'s docstring and `README.md`'s "Discovery
crawler" section for the full picture). A `pending` row means a job URL was
submitted from a domain that didn't match any adapter in
`app/services/adapters/__init__.py`'s `ADAPTERS` registry — `name` is just
the domain, `ats_type` is `null`, `board_url` is `https://{domain}`. Your job
is to turn that into either:

- an `active` row with the right `ats_type`, a corrected `board_url`, and the
  real company name, backed by a working adapter,
- a `rejected` row, if the platform genuinely can't be crawled, or
- a `delete` row, if the row itself turns out to be wrong (almost
  always a stale duplicate of a board that's already `active` under a
  different, canonical `board_url` — see step 6b).

## 0.5 Point the playbook at a target

Every `/admin/...` call below runs against one backend — either your local
dev server or the real production API — controlled by two env vars:

```bash
export CRAWL_ADMIN_BASE_URL=http://localhost:8000     # or https://api.yabot.jobs
export CRAWL_ADMIN_TOKEN=...                           # a personal access token, see below
```

Get a token once per target (it's tied to whichever backend issued it — a
localhost token doesn't work against prod and vice versa) by logging in
normally in the browser against that target, then:

```bash
curl -X POST "$CRAWL_ADMIN_BASE_URL/auth/tokens" \
  -H "Content-Type: application/json" -H "Cookie: session_token=<from the browser>" \
  -d '{"label": "adapter-playbook"}'
```

Save the returned `token` as `CRAWL_ADMIN_TOKEN` — it's shown once. Every
request after that uses it as a bearer token:

```bash
curl -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources"
```

**Which target to use, and when:** the real pending queue — the one worth
working — lives in production (`https://api.yabot.jobs`), since that's where
real user submissions land; a fresh local dev DB usually has none. Write and
verify the adapter locally (step 4 talks straight to the target ATS site over
the network, not to our own backend, so it works identically regardless of
which `CRAWL_ADMIN_BASE_URL` you have set). Only the two DB-touching calls —
listing/reading the pending row and the final `PATCH` in steps 5/6 — need
`CRAWL_ADMIN_BASE_URL` pointed at prod, and only *after* the adapter code
itself has been deployed there (see step 7 — the 422 check in step 5 runs
against whatever code the target server is currently running).

## 1. Pick a source and investigate

```bash
curl -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources" \
  | jq '[.[] | select(.status == "pending")]'
```

Open `board_url` (and, if you have one, the original job URL that triggered
this row — check `git log`/logs, or just browse the site's careers page) and
figure out what's actually there:

1. **Fetch the page plainly first** (`httpx.get`, or `curl`). Read the raw
   HTML. Look for:
   - A recognizable ATS signature even though `detect_ats_source` missed it —
     an iframe `src`, a script tag, a `<link>`/meta generator tag, or a
     network call pointing at a *known* ATS host (`boards-api.greenhouse.io`,
     `api.ashbyhq.com`, `jobs.lever.co`, `*.myworkdayjobs.com`,
     `recruiting.adp.com`, `*.bamboohr.com`, `*.personio.de`,
     `*.applytojob.com`, `*.workable.com`, `*.breezy.hr`,
     `*.recruitee.com`, `*.oraclecloud.com`, `clinchtalent.com`,
     `eightfold.ai`, ...). **If you find one of these, this is not a new
     adapter** — it's a white-label embed of a platform we already support
     that our static-URL `match()` just can't see. Skip to step 4.
   - A genuinely new/unrecognized platform (iCIMS, Jibe, SmartRecruiters,
     Taleo, SuccessFactors, Jobvite, Phenom, Avature, UKG, Paycor, an
     in-house system, ...).
2. **If the plain HTML already contains the job list** (server-rendered
   anchors, or a JSON blob embedded in a `<script>` tag, or a sitemap.xml),
   you have everything you need with no browser — go to step 3 using regex /
   `ElementTree` / `json.loads`, same as `jazzhr.py` / `apple.py` /
   `clinch.py`.
3. **If the plain HTML is an empty SPA shell** (a `<div id="root">`, JS
   bundle `<script src>` tags, no job content), don't reach for the browser
   yet either — **check whether the SPA is itself backed by a plain JSON
   API** first. Use a browser's network tab (or the `claude-in-chrome` tool's
   `read_network_requests`) to watch what XHR/fetch calls the page makes
   while loading, and try hitting that same endpoint directly with `httpx`.
   Most "JS-rendered-looking" career sites are still backed by an
   unauthenticated public JSON API underneath (this *is* how Eightfold,
   Ashby, Greenhouse, etc. were all done) — finding it means the adapter
   never needs the browser fallback at all, which is faster, cheaper, and
   more reliable in production.
4. **Only if there is truly no discoverable API and no static HTML content**
   (the job data is only ever visible after JS runs client-side, e.g. reading
   an iframe `src` an embed script builds — see `ashby.py`'s
   `_detect_embedded`'s final tier) do you fall back to
   `app.services.browser_fetch.fetch_rendered_html(url, wait_for_selector=...)`.
   That calls the standalone `yabot.jobs-browser` service (headless
   Chromium/Playwright; see its `README.md`) — it returns fully-hydrated
   HTML you can then regex/parse exactly like a plain fetch. It's `None` on
   any failure (service unreachable, `BROWSER_FETCH_SERVICE_URL` unset,
   render timeout) — treat that as "still couldn't get the data," not an
   error to raise.

If, after exhausting steps 1–4, there's still no way to enumerate job URLs
(auth-walled board, no listing page at all, robots.txt/ToS explicitly
disallows automated access, CAPTCHA-gated), this platform gets `rejected` —
skip to step 6.

If instead the platform *is* crawlable but you find this exact company
already has a working `active` row under a different `board_url` (this
`pending` row is just a stale duplicate) — skip to step 6b instead.

## 2. Get the real company name

Don't guess the name from the domain or slug — `_company_name()` in
`app/services/crawl_sources.py` is explicitly "cosmetic only," a placeholder
until a human/LLM fixes it, which is what you're doing now. Read the actual
site: the `<title>`, `og:site_name` meta tag, or the careers page's own
heading/footer copyright line. Use the real, correctly-capitalized company
name (e.g. "BambooHR", not "bamboohr" or "Bamboohr").

## 3. Implement the adapter (skip if step 1 found an existing platform)

Create `app/services/adapters/<platform>.py` exporting a single `ADAPTER =
AtsAdapter(...)`, matching the existing style exactly:

- Read `base.py`'s `AtsAdapter` docstring for what each field means
  (`match`, `board_key`, `fetch_jobs`, `to_board_url`, `embedded_match`).
- Use `httpx` with `base.TIMEOUT`, not a hand-rolled timeout.
- Comments only where they explain a non-obvious *why* (a quirk you
  verified against the live site) — see every existing adapter for the
  tone/density to match. No docstrings restating what the code already
  says.
- **`to_board_url` — canonicalize only if there's a real shared ATS host to
  canonicalize to** (Greenhouse's `boards.greenhouse.io/{key}`, JazzHR's
  `{key}.applytojob.com/apply/jobs`, ...). For a white-label platform where
  the board lives on the company's own domain (Oracle Fusion/Clinch-style),
  leave `to_board_url` unset (`None`) and store the board URL **verbatim, as
  submitted/discovered** — do not template or reconstruct it. This has bitten
  us before; see `oracle_fusion.py`/`clinch.py` for the pattern.
- Add a value to `AtsType` in `app/models/enums.py` if this is a genuinely
  new platform (it's a plain `String(20)` column, not a Postgres enum — no
  migration needed for a new value).
- Register the adapter in `app/services/adapters/__init__.py`: import the
  module and add `<module>.ADAPTER` to the `ADAPTERS` list. Order roughly
  follows how common/generic each platform is; add yours near platforms of a
  similar kind (multi-tenant ATS vs. single-company site vs. white-label).

## 4. Verify against the live board before touching the DB row

Don't trust the code until you've run it against the real board:

```bash
python -c "
from app.services.ats_adapters import detect_ats_source, list_job_urls
ats_type, key = detect_ats_source('<the corrected board_url>')
urls = list_job_urls(ats_type, '<the corrected board_url>')
print(ats_type, len(urls))
print(urls[:5])
"
```

Sanity-check the count and a couple of the returned URLs against what you
see by eye on the site (open 1–2 of the returned URLs, confirm they're real,
current postings — not stale/dead links or unrelated pages). Zero results is
only fine if the board genuinely has zero open roles right now, not because
the adapter silently failed — check `last_error`-style exceptions bubble up
loudly during this manual run, don't swallow them.

Also confirm the app still imports cleanly after adding the new module/enum
value:

```bash
python -c "import main"
```

## 5. Update the CrawlSource record

```bash
curl -X PATCH "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources/{source_id}" \
  -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "<real company name from step 2>",
    "board_url": "<canonical board_url from board_url_for_key, or the verbatim verified URL>",
    "status": "active"
  }'
```

`ats_type` is re-derived from `board_url` server-side — you don't (and
shouldn't) send it directly. The endpoint 422s if `board_url` doesn't
resolve under a supported adapter, which is your final confirmation the
adapter is actually wired in correctly — and, if `CRAWL_ADMIN_BASE_URL` is
prod, confirmation the deploy in step 7 actually landed.

## 6. Or: reject it

If step 1 concluded the platform can't be crawled at all:

```bash
curl -X PATCH "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources/{source_id}" \
  -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "rejected"}'
```

Leave `name`/`board_url` as-is (the domain placeholder) — `rejected` means
"investigated, not worth revisiting," not "cleaned up." Once rejected, this
domain won't be re-flagged by future job submissions from the same site (see
`register_discovered_board`'s `_upsert` dedup-by-`board_url` behavior).

## 6b. Or: flag it for deletion

Sometimes the platform is perfectly crawlable and the row is still wrong —
the company already has a working `active` row under a different, canonical
`board_url` (a domain-placeholder row alongside a real one, most often: some
earlier job submission carried enough to resolve via `detect_ats_source`/
`detect_embedded_ats_source` and got auto-activated by
`register_discovered_board`, while this `pending` row is a leftover from a
different submission — e.g. the bare marketing-site URL — that didn't). The
tell is usually a `409 "A crawl source for this board_url already exists"`
when you try step 5's PATCH with the canonical `board_url`. Before
concluding that, check for the duplicate explicitly:

```bash
curl -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources" \
  | jq '[.[] | select(.name | test("<company>"; "i"))]'
```

If you find another row for the same company already `active` (ideally
already with a recent `last_crawled_at`), this `pending` row
is redundant — flag it rather than trying to force it to `active` too:

```bash
curl -X PATCH "$CRAWL_ADMIN_BASE_URL/admin/crawl-sources/{source_id}" \
  -H "Authorization: Bearer $CRAWL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "delete"}'
```

`delete` is a human-review marker, not an actual deletion — **never call
`DELETE /admin/crawl-sources/{id}` yourself**, even here. Flag it, tell the
user which row and why (which other row it duplicates), and let them decide
whether to actually delete it via that endpoint or the DB directly.

## 7. Ship it

A new adapter is just an app code change — no migration required (`ats_type`
is a free-text column). But every container (`api`, `crawl-worker`, `worker`)
**runs a baked image that does not pick up new code until rebuilt** —
`docker compose up -d` alone reuses the stale image, and prod needs an actual
deploy, not just a local rebuild.

**Ordering matters if `CRAWL_ADMIN_BASE_URL` is prod:** step 5's `PATCH`
only succeeds once the `api` service serving `$CRAWL_ADMIN_BASE_URL` is
running code that includes the new adapter — so deploy first, PATCH second.
Deploying also covers `crawl-worker`, which needs the same code to actually
crawl the board once it's `active`. Don't commit or push on your own
initiative — hand the diff back for review first, same as any other change.
