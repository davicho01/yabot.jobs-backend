#!/usr/bin/env bash
# End-to-end GCP deploy for yabot.jobs-backend.
#
# Layout:
#   - migrate         Cloud Run Job    (one-shot: alembic upgrade head)
#   - api             Cloud Run service (main.py, HTTP)
#   - worker          Cloud Function 2nd gen (worker.py:handle_scan_request)
#                     + Pub/Sub trigger on job-scan-requests
#   - crawl-worker    Cloud Function 2nd gen (crawl_worker.py:handle_crawl_request)
#                     + Pub/Sub trigger on crawl-source-requests
#   - crawl-dispatcher  Cloud Run Job (crawl_dispatcher.py)
#                       + Cloud Scheduler cron trigger (fire-and-forget via
#                       the Run Admin API — see that section for why)
#   - browser-scaler    Cloud Function 2nd gen (browser_scaler.py:dispatch)
#                       + a normally-paused Cloud Scheduler job that
#                       crawl-dispatcher resumes on each run and this
#                       function pauses again once crawl-worker/worker go
#                       quiet — matches yabot-jobs-browser's min-instances
#                       to real demand instead of a flat guess
#   - saved-search-alerts  Cloud Function 2nd gen (saved_search_alerts.py:dispatch)
#                       + Cloud Scheduler cron trigger
#   - follow-up-reminders  Cloud Function 2nd gen (follow_up_reminders.py:dispatch)
#                       + Cloud Scheduler cron trigger
#   - retry-failed-scans  Cloud Function 2nd gen (retry_failed_scans.py:dispatch)
#                       + Cloud Scheduler cron trigger
#   - generate-static-job-pages  Cloud Function 2nd gen (generate_static_job_pages.py:dispatch)
#                       + Cloud Scheduler cron trigger, every 30 minutes — publishes
#                       the crawlable "jobs by sector, by day" pages straight into the
#                       *frontend's* S3 bucket/CloudFront (SEO_PAGES_BUCKET below), not
#                       this backend's own storage.
#   - browser-fetch   already deployed separately; see BROWSER_FETCH_SERVICE_URL below
#
# Prereqs this script assumes already exist (create once, not here):
#   - `gcloud auth login` + billing enabled on $PROJECT_ID
#
# IMPORTANT: Cloud SQL lives in us-east1, NOT us-central1 (SQL_REGION below,
# separate from REGION for Cloud Run). Two freshly-created db-f1-micro
# instances in us-central1 for this project were completely unreachable on
# the Cloud SQL proxy port (3307) from every path tested — Cloud Run jobs,
# a local machine, and manual `gcloud sql connect` — with
# "dial tcp <instance-ip>:3307: i/o timeout" / "SFEClient is nil" every
# time, regardless of restarts or authorized-networks changes. The same
# image/job configuration connected immediately to a same-project instance
# in us-east1, isolating this to something broken about Cloud SQL
# specifically in us-central1 for this project (not IAM, not VPC-SC — this
# account has no GCP organization at all — and not the instance config).
# Before moving this back to us-central1, re-test with a fresh instance
# there first.
#
# Run section by section, not all at once — read the comments first.

set -euo pipefail

PROJECT_ID="yabotjobs"
REGION="us-central1"
SQL_REGION="us-east1"
REPO="yabot-jobs"                       # Artifact Registry repo name
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend"
SQL_INSTANCE="yabot-jobs-db"
CLOUDSQL_INSTANCE_CONNECTION="${PROJECT_ID}:${SQL_REGION}:${SQL_INSTANCE}"
BROWSER_FETCH_SERVICE_URL="https://yabot-jobs-browser-487584214286.us-central1.run.app"

# Same bucket/distribution yabot.jobs-frontend's own deploy uses (its GitHub
# Actions vars S3_BUCKET / CLOUDFRONT_DISTRIBUTION_ID — not committed to any
# repo, see that repo's .github/workflows/deploy.yml). generate-static-job-pages
# writes real HTML objects straight into this same bucket/distribution so
# they're served at yabot.jobs/jobs/... alongside the SPA — this is
# intentionally the frontend's infra, not a new bucket of ours.
SEO_PAGES_BUCKET="yabot.jobs-frontend"
SEO_PAGES_CLOUDFRONT_DISTRIBUTION_ID="E2JQUGZZTUJPZ6"

# ---------------------------------------------------------------------------
# 0. One-time project setup
# ---------------------------------------------------------------------------

gcloud config set project "$PROJECT_ID"

gcloud services enable \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  sqladmin.googleapis.com \
  pubsub.googleapis.com

gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="yabot.jobs-backend images"

# The default compute service account (used by migrate/api/worker/crawl-worker
# unless you pass --service-account) needs both of these or every Cloud SQL
# connection attempt fails with "server closed the connection unexpectedly"
# (cloudsql.client) and every --set-secrets deploy fails with permission
# denied (secretAccessor).
DEFAULT_COMPUTE_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/cloudsql.client" --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/secretmanager.secretAccessor" --condition=None

# yabot-jobs-browser (see BROWSER_FETCH_SERVICE_URL above) is a private
# Cloud Run service deployed separately — api/worker/crawl-worker call it
# with a Google-signed ID token (app/services/browser_fetch.py), which needs
# run.invoker on that specific service, not just a valid token. Without this
# every call 403s at the Cloud Run layer regardless of authentication.
gcloud run services add-iam-policy-binding yabot-jobs-browser \
  --region="$REGION" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/run.invoker"

# ---------------------------------------------------------------------------
# 0b. Cloud SQL for Postgres — db-f1-micro, single zone (no HA). Generates a
#     fresh app-user password and writes DATABASE_URL straight into
#     deploy/.env.production so it flows into Secret Manager below.
# ---------------------------------------------------------------------------

gcloud sql instances create "$SQL_INSTANCE" \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region="$SQL_REGION" \
  --availability-type=ZONAL \
  --storage-size=10GB \
  --storage-auto-increase

gcloud sql databases create yabot_jobs --instance="$SQL_INSTANCE"

DB_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
gcloud sql users create appuser --instance="$SQL_INSTANCE" --password="$DB_PASSWORD"

{
  echo "DATABASE_URL=postgresql+psycopg://appuser:${DB_PASSWORD}@/yabot_jobs?host=/cloudsql/${CLOUDSQL_INSTANCE_CONNECTION}"
} >> deploy/.env.production

# ---------------------------------------------------------------------------
# 1. Secrets (run once; update with `gcloud secrets versions add` later)
#    Values are read from your local .env — nothing is hardcoded here.
# ---------------------------------------------------------------------------

create_secret_from_env() {
  local secret_name="$1" env_var="$2" env_file="${3:-.env}"
  local value
  value="$(grep -E "^${env_var}=" "$env_file" | head -1 | cut -d= -f2-)"
  printf '%s' "$value" | gcloud secrets create "$secret_name" --data-file=- \
    || printf '%s' "$value" | gcloud secrets versions add "$secret_name" --data-file=-
}

create_secret_from_env database-url               DATABASE_URL                      deploy/.env.production
create_secret_from_env api-key-encryption-key      API_KEY_ENCRYPTION_KEY
create_secret_from_env system-llm-api-key          SYSTEM_LLM_API_KEY               deploy/.env.production
# Real AWS creds for the yabot-jobs-backend IAM user (scoped to just the
# yabot.jobs-files bucket) — kept out of the local dev .env, which stays
# pointed at MinIO. See deploy/.env.production.
create_secret_from_env resume-storage-access-key   RESUME_STORAGE_ACCESS_KEY_ID     deploy/.env.production
create_secret_from_env resume-storage-secret-key   RESUME_STORAGE_SECRET_ACCESS_KEY deploy/.env.production

# Non-secret, shared across api/worker/crawl-worker:
COMMON_ENV="GCP_PROJECT_ID=${PROJECT_ID},BROWSER_FETCH_SERVICE_URL=${BROWSER_FETCH_SERVICE_URL},FRONTEND_BASE_URL=https://yabot.jobs,SESSION_COOKIE_SECURE=true,SYSTEM_LLM_PROVIDER=deepseek,SYSTEM_LLM_MODEL=deepseek-v4-flash,RESUME_STORAGE_BUCKET=yabot.jobs-files,RESUME_STORAGE_REGION=us-east-1,SEO_PAGES_BUCKET=${SEO_PAGES_BUCKET},SEO_PAGES_CLOUDFRONT_DISTRIBUTION_ID=${SEO_PAGES_CLOUDFRONT_DISTRIBUTION_ID}"

# EMAIL_SENDER_* map to the same underlying secrets as RESUME_STORAGE_* —
# both are the yabot-jobs-backend IAM user's credentials (S3 + SES policies
# attached to one user), not separate secrets.
COMMON_SECRETS="DATABASE_URL=database-url:latest,API_KEY_ENCRYPTION_KEY=api-key-encryption-key:latest,SYSTEM_LLM_API_KEY=system-llm-api-key:latest,RESUME_STORAGE_ACCESS_KEY_ID=resume-storage-access-key:latest,RESUME_STORAGE_SECRET_ACCESS_KEY=resume-storage-secret-key:latest,EMAIL_SENDER_ACCESS_KEY_ID=resume-storage-access-key:latest,EMAIL_SENDER_SECRET_ACCESS_KEY=resume-storage-secret-key:latest"

# ---------------------------------------------------------------------------
# 2. Build & push the shared image (api/worker/crawl-worker/migrate all use it)
# ---------------------------------------------------------------------------

gcloud builds submit --tag "${IMAGE}:$(git rev-parse --short HEAD)" .
IMAGE_TAG="${IMAGE}:$(git rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
# 3. migrate — Cloud Run Job, run once per deploy before touching the others
# ---------------------------------------------------------------------------

gcloud run jobs deploy migrate \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=alembic --args=upgrade,head \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --labels=function=migrate

gcloud run jobs execute migrate --region="$REGION" --wait

# ---------------------------------------------------------------------------
# 3b. backfill-country — Cloud Run Job, one-off (not run on every deploy,
#     unlike migrate above). Same image/secrets/env, command
#     `python -m one_off.backfill_country` — computes JobPosting.country for
#     every existing row via app.services.geo.resolve_country_for_locations,
#     needed once so rows scanned before that column existed aren't invisible
#     to the /jobs/us/... filter (see one_off/backfill_country.py's own
#     docstring). Deploy once, then execute by hand — --dry-run first to
#     sanity-check the country breakdown before writing:
#       gcloud run jobs execute backfill-country --region="$REGION" --args=--dry-run --wait
#       gcloud run jobs execute backfill-country --region="$REGION" --wait
# ---------------------------------------------------------------------------

gcloud run jobs deploy backfill-country \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=-m,one_off.backfill_country \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --task-timeout=3600 \
  --labels=function=backfill-country

# ---------------------------------------------------------------------------
# 3c. generate-job-pages-backfill — Cloud Run Job, one-off: renders every
#     per-job SEO page (/job/{url_id}, app.services.static_job_pages) at
#     once. The twice-daily generate-static-job-pages function (§10) keeps
#     them up to date incrementally afterwards, but a first full render of
#     every live job won't fit in its 540s timeout. Also the way to force a
#     re-render after a template change, though bumping
#     static_job_pages.PAGE_VERSION does that on the next scheduled run
#     anyway. Deploy once, then execute by hand — --dry-run first for the
#     counts:
#       gcloud run jobs execute generate-job-pages-backfill --region="$REGION" --args=generate_static_job_pages.py,--full,--dry-run --wait
#       gcloud run jobs execute generate-job-pages-backfill --region="$REGION" --wait
# ---------------------------------------------------------------------------

gcloud run jobs deploy generate-job-pages-backfill \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=generate_static_job_pages.py,--full \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --task-timeout=3600 \
  --labels=function=generate-job-pages-backfill

# ---------------------------------------------------------------------------
# 4. api — Cloud Run service
# ---------------------------------------------------------------------------

gcloud run deploy api \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --platform=managed \
  --command=uvicorn --args=main:app,--host,0.0.0.0,--port,8080 \
  --port=8080 \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --min-instances=0 \
  --max-instances=10 \
  --allow-unauthenticated \
  --update-labels=function=api

# ---------------------------------------------------------------------------
# 5. worker / crawl-worker — Cloud Functions (2nd gen), Pub/Sub-triggered
#
#    Used to be Cloud Run worker pools (persistent pull loops), but worker
#    pools have no scale-to-zero — 1 instance runs 24/7 per pool regardless
#    of load, ~$30/month each for what's a low-volume queue. A Pub/Sub
#    trigger invokes the function once per message and scales to zero
#    between messages instead. Same image/logic either way: worker.py's/
#    crawl_worker.py's handle_scan_request()/handle_crawl_request() reuse
#    the exact processing functions main()'s pull loop calls (still used
#    for local dev against the Pub/Sub emulator — see docker-compose.yml).
#
#    --trigger-topic auto-creates the topic if missing and manages its own
#    Eventarc subscription, so no manual topic/subscription provisioning is
#    needed here.
#
#    `gcloud functions deploy` has NO --set-cloudsql-instances flag (2nd gen
#    functions are Cloud Run services under the hood, but the functions CLI
#    doesn't expose this) — same as crawl-dispatcher below, attach Cloud SQL
#    to the underlying Cloud Run service afterward.
# ---------------------------------------------------------------------------

gcloud functions deploy worker \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=handle_scan_request \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=worker.py \
  --trigger-topic=job-scan-requests \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s \
  --max-instances=30 \
  --update-labels=function=worker

gcloud run services update worker \
  --region="$REGION" \
  --max=30 \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

# Instance caps: worker 30, crawl-worker 10 (since 2026-10-02; were 55/35 by
# hand, 60/20 here). Every instance holds a Cloud SQL connection and the
# instance (db-custom-1-3840) has max_connections=100. At 55/35, every
# twice-daily crawl burst (crawl-dispatch, 1pm/9pm ET) ran it out of
# connections (1,838 refused on 2026-10-01's 1pm burst); 30 + 10 is ~50 at
# full burst, leaving the API, MCP and scheduled functions room.
# --max-instances caps each *revision*: a deploy runs two for a few minutes
# (the old one draining in-flight requests while the new one scales up),
# which is how 55-instance caps hit 101-111 active instances. Avoid
# deploying during a burst, where that overlap is ~100 connections again.
# The service-level --max (shared by every revision receiving traffic) is
# set too. The same values are pinned in .github/workflows/deploy.yml.
# Check DB num_backends before raising either.
gcloud functions deploy crawl-worker \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=handle_crawl_request \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=crawl_worker.py \
  --trigger-topic=crawl-source-requests \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s \
  --max-instances=10 \
  --update-labels=function=crawl-worker

gcloud run services update crawl-worker \
  --region="$REGION" \
  --max=10 \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

# ---------------------------------------------------------------------------
# 6. crawl-dispatcher — Cloud Run Job, triggered by Scheduler via a
#    fire-and-forget call to the Run Admin API (NOT an HTTP Cloud Function
#    Scheduler waits on for a response).
#
#    This has to walk every active, currently-unclaimed CrawlSource (~2,800+
#    and growing) one at a time — a claim commit plus a blocking Pub/Sub
#    publish per source, deliberately paced that way (see enqueue_crawl's
#    own docstring: an all-at-once burst overloaded crawl-worker and the
#    shared browser-rendering service once already, see one_off/
#    recrawl_sources.py's DEFAULT_BATCH_SIZE comment) — which reliably takes
#    well past both Cloud Scheduler's attemptDeadline (max 30min) and a
#    Cloud Function/Run service's own request timeout. Verified live
#    2026-09-28: every crawl-dispatch-hourly run was failing
#    DEADLINE_EXCEEDED while the dispatch itself kept working in the
#    background regardless, un-tracked, until it got killed mid-sweep.
#    A Cloud Run Job has no such deadline riding on an HTTP round-trip —
#    Scheduler just tells it to start and walks away, same as the
#    `migrate`/`backfill-country` jobs above. Same image, same pattern.
# ---------------------------------------------------------------------------

gcloud run jobs deploy crawl-dispatcher \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=crawl_dispatcher.py \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --task-timeout=7200 \
  --max-retries=0 \
  --labels=function=crawl-dispatcher

gcloud run jobs add-iam-policy-binding crawl-dispatcher \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com" \
  --role="roles/run.invoker"

# 2x/day, 1pm and 9pm America/New_York: postings trickle in through business
# hours (~9am-6pm local, so ~9am-9pm ET once PT is folded in), so a morning
# run would mostly just re-serve the prior night's crawl. 1pm catches the
# ET/CT morning wave; 9pm catches the rest of the day including PT (whose
# posting activity has already tailed off by 9pm ET = 6pm PT).
# --oauth-service-account-email (not --oidc-...) because the target is the
# Run Admin REST API, not the job's own service URL.

gcloud scheduler jobs create http crawl-dispatch-hourly \
  --location="$REGION" \
  --schedule="0 13,21 * * *" \
  --time-zone="America/New_York" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/crawl-dispatcher:run" \
  --http-method=POST \
  --oauth-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com"

# ---------------------------------------------------------------------------
# 6b. browser-scaler — Cloud Function (2nd gen) + a *normally-paused* Cloud
#     Scheduler job, browser-scaler-tick. Matches yabot-jobs-browser's warm
#     capacity to real crawl-worker/worker demand instead of a flat,
#     always-on min-instances guess — see browser_scaler.py's own docstring.
#
#     crawl_dispatcher.py resumes browser-scaler-tick at the top of its own
#     main() (whether that run came from the 3x/day schedule above or a
#     manual `gcloud run jobs execute crawl-dispatcher`); browser_scaler.py
#     pauses it again itself once it observes crawl-worker and worker have
#     both been idle for a full lookback window — crawl_dispatcher.py
#     finishing isn't the same signal, see that module's own docstring for
#     why. Deploy this section before crawl-dispatcher's next redeploy:
#     crawl_dispatcher.py's startup calls assume this Cloud Function and the
#     (paused) Scheduler job already exist.
#
#     No --set-secrets here, deliberately: browser_scaler.py never touches
#     the database or an LLM, only GCP_PROJECT_ID from COMMON_ENV (see
#     app.services.gcp_admin's own docstring on why it reads that straight
#     from the environment instead of importing the full Settings object).
#
#     --max-instances=1: browser-scaler-tick is the only trigger, firing
#     once every 2 minutes — never more than one execution in flight, and
#     the gen2 default of 100 offered no benefit, only the risk of two
#     concurrent ticks racing on the same min-instances decision.
# ---------------------------------------------------------------------------

gcloud functions deploy browser-scaler \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=browser_scaler.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --memory=256Mi \
  --timeout=60s \
  --max-instances=1

BROWSER_SCALER_FUNCTION_URL="$(gcloud functions describe browser-scaler --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding browser-scaler \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http browser-scaler-tick \
  --location="$REGION" \
  --schedule="*/2 * * * *" \
  --uri="$BROWSER_SCALER_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$BROWSER_SCALER_FUNCTION_URL"

# Starts off — only crawl_dispatcher.py's main() turns it on (see above).
gcloud scheduler jobs pause browser-scaler-tick --location="$REGION"

# browser_scaler.py and crawl_dispatcher.py both run as DEFAULT_COMPUTE_SA
# (see the account's own comment near the top of this file) and both need
# these to talk to Cloud Monitoring / Cloud Run Admin / Cloud Scheduler
# Admin:
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/monitoring.viewer" --condition=None

gcloud run services add-iam-policy-binding yabot-jobs-browser \
  --region="$REGION" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/run.developer"

# Cloud Scheduler has no per-job IAM bindings, so this has to be
# project-scoped — same tradeoff as the cloudsql.client/secretAccessor
# bindings near the top of this file.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEFAULT_COMPUTE_SA}" \
  --role="roles/cloudscheduler.admin" --condition=None

# ---------------------------------------------------------------------------
# 7. saved-search-alerts — Cloud Function (2nd gen), triggered by Scheduler.
#    Deploys from source (this repo), entry point is dispatch() in
#    saved_search_alerts.py — same shape as crawl-dispatcher above in every
#    respect (HTTP-triggered, no-allow-unauthenticated, OIDC-invoked by
#    Scheduler, Cloud SQL attached to the underlying Cloud Run service
#    afterward since `gcloud functions deploy` has no --set-cloudsql-instances
#    flag). 2x/day, an hour after each crawl-dispatch run (2pm/10pm
#    America/New_York, one hour after the 1pm/9pm dispatch above) rather
#    than hourly: new postings only actually show up in bursts right after a
#    dispatch now that crawl-dispatch itself only runs 2x/day (see that
#    section above), so an hourly sweep was mostly finding nothing new.
#    Verified live 2026-09-28 (back when dispatch ran at 9am): new rows
#    stopped appearing ~25 minutes after dispatch, so the 1-hour buffer
#    before this alerts run has margin to spare.
# ---------------------------------------------------------------------------

gcloud functions deploy saved-search-alerts \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=saved_search_alerts.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s \
  --update-labels=function=saved-search-alerts

gcloud run services update saved-search-alerts \
  --region="$REGION" \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

SAVED_SEARCH_ALERTS_FUNCTION_URL="$(gcloud functions describe saved-search-alerts --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding saved-search-alerts \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http saved-search-alerts-hourly \
  --location="$REGION" \
  --schedule="0 14,22 * * *" \
  --time-zone="America/New_York" \
  --uri="$SAVED_SEARCH_ALERTS_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$SAVED_SEARCH_ALERTS_FUNCTION_URL"

# ---------------------------------------------------------------------------
# 8. follow-up-reminders — Cloud Function (2nd gen), triggered daily by
#    Scheduler. Deploys from source (this repo), entry point is dispatch()
#    in follow_up_reminders.py — same shape as saved-search-alerts above in
#    every respect except cadence: daily, not hourly, since a follow-up
#    date is a day, not a moment (see follow_up_reminders.py's own
#    docstring) — checking more often than once a day couldn't send
#    anything sooner. 13:00 UTC ≈ 8am ET / 5am PT, a US-morning send time
#    for this product's currently US-centric user base.
# ---------------------------------------------------------------------------

gcloud functions deploy follow-up-reminders \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=follow_up_reminders.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s \
  --update-labels=function=follow-up-reminders

gcloud run services update follow-up-reminders \
  --region="$REGION" \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

FOLLOW_UP_REMINDERS_FUNCTION_URL="$(gcloud functions describe follow-up-reminders --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding follow-up-reminders \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http follow-up-reminders-daily \
  --location="$REGION" \
  --schedule="0 13 * * *" \
  --uri="$FOLLOW_UP_REMINDERS_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$FOLLOW_UP_REMINDERS_FUNCTION_URL"

# ---------------------------------------------------------------------------
# 9. retry-failed-scans — Cloud Function (2nd gen), triggered hourly by
#    Scheduler. Deploys from source (this repo), entry point is dispatch()
#    in retry_failed_scans.py — same shape as saved-search-alerts above in
#    every respect (HTTP-triggered, no-allow-unauthenticated, OIDC-invoked
#    by Scheduler, Cloud SQL attached to the underlying Cloud Run service
#    afterward since `gcloud functions deploy` has no --set-cloudsql-instances
#    flag). Hourly matches the shortest backoff window a FAILED row can have
#    (scan_retry_base_seconds — see app.core.config) — checking more often
#    couldn't retry anything sooner, and a sweep that finds nothing due is a
#    no-op (see wake_retryable_failed_scans).
# ---------------------------------------------------------------------------

gcloud functions deploy retry-failed-scans \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=retry_failed_scans.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s \
  --update-labels=function=retry-failed-scans

gcloud run services update retry-failed-scans \
  --region="$REGION" \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

RETRY_FAILED_SCANS_FUNCTION_URL="$(gcloud functions describe retry-failed-scans --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding retry-failed-scans \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http retry-failed-scans-hourly \
  --location="$REGION" \
  --schedule="0 * * * *" \
  --uri="$RETRY_FAILED_SCANS_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$RETRY_FAILED_SCANS_FUNCTION_URL"

# ---------------------------------------------------------------------------
# 10. generate-static-job-pages — Cloud Function (2nd gen), triggered by
#     Scheduler. Deploys from source (this repo), entry point is dispatch()
#     in generate_static_job_pages.py — same shape as the functions above in
#     every respect except what it talks to: it writes straight into the
#     *frontend's* S3 bucket/CloudFront distribution
#     (SEO_PAGES_BUCKET/SEO_PAGES_CLOUDFRONT_DISTRIBUTION_ID above), not
#     Postgres/Pub/Sub. 2x/day, same time as saved-search-alerts-hourly
#     (2pm/10pm America/New_York, one hour after the 1pm/9pm crawl-dispatch
#     above) — was every 30 minutes on its own independent cadence (see
#     generate_static_job_pages.py's own docstring for the original
#     freshness/cost reasoning), but that predates crawl-dispatch itself
#     dropping to 2x/day: postings now only actually land in bursts right
#     after a dispatch, so a 30-minute sweep was mostly re-rendering
#     unchanged pages. No point regenerating the static pages more often
#     than alerts re-scan for new rows. Verified live 2026-09-28 (back when
#     dispatch ran at 9am): new rows stopped appearing ~25 minutes after
#     dispatch, so the 1-hour buffer before this run has margin to spare.
#
#     IMPORTANT: the yabot-jobs-backend IAM user (whose key/secret are
#     already in the resume-storage-access-key/resume-storage-secret-key
#     secrets above, reused here) must additionally have s3:PutObject/
#     s3:GetObject on SEO_PAGES_BUCKET and cloudfront:CreateInvalidation on
#     SEO_PAGES_CLOUDFRONT_DISTRIBUTION_ID — an AWS console/IAM policy change
#     to make once, not something this script can do.
# ---------------------------------------------------------------------------

gcloud functions deploy generate-static-job-pages \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=generate_static_job_pages.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=1Gi \
  --cpu=1 \
  --timeout=540s \
  --update-labels=function=generate-static-job-pages

gcloud run services update generate-static-job-pages \
  --region="$REGION" \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

GENERATE_STATIC_JOB_PAGES_FUNCTION_URL="$(gcloud functions describe generate-static-job-pages --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding generate-static-job-pages \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http generate-static-job-pages-30min \
  --location="$REGION" \
  --schedule="0 14,22 * * *" \
  --time-zone="America/New_York" \
  --uri="$GENERATE_STATIC_JOB_PAGES_FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$GENERATE_STATIC_JOB_PAGES_FUNCTION_URL"

# ---------------------------------------------------------------------------
# Redeploys after this point (new image/source, no infra changes) — this is
# also exactly what .github/workflows/deploy.yml runs on every push to main:
#   gcloud builds submit --tag "${IMAGE}:$(git rev-parse --short HEAD)" --project="$PROJECT_ID" .
#   gcloud run jobs deploy migrate --image="$IMAGE_TAG" --region="$REGION" --project="$PROJECT_ID" --labels=function=migrate
#   gcloud run jobs execute migrate --region="$REGION" --project="$PROJECT_ID" --wait
#   gcloud run deploy api --image="$IMAGE_TAG" --region="$REGION" --project="$PROJECT_ID" --update-labels=function=api
#   gcloud functions deploy worker --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=handle_scan_request --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=worker.py \
#     --update-labels=function=worker
#   gcloud functions deploy crawl-worker --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=handle_crawl_request --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=crawl_worker.py \
#     --update-labels=function=crawl-worker
#   gcloud functions deploy crawl-dispatcher --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=dispatch --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=crawl_dispatcher.py \
#     --update-labels=function=crawl-dispatcher
#   gcloud functions deploy saved-search-alerts --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=dispatch --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=saved_search_alerts.py \
#     --update-labels=function=saved-search-alerts
#   gcloud functions deploy follow-up-reminders --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=dispatch --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=follow_up_reminders.py \
#     --update-labels=function=follow-up-reminders
#   gcloud functions deploy retry-failed-scans --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=dispatch --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=retry_failed_scans.py \
#     --update-labels=function=retry-failed-scans
#   gcloud functions deploy generate-static-job-pages --gen2 --region="$REGION" --project="$PROJECT_ID" \
#     --source=. --entry-point=dispatch --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=generate_static_job_pages.py \
#     --update-labels=function=generate-static-job-pages
# ---------------------------------------------------------------------------
