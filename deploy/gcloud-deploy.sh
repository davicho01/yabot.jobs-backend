#!/usr/bin/env bash
# End-to-end GCP deploy for yabot.jobs-backend.
#
# Layout:
#   - migrate         Cloud Run Job    (one-shot: alembic upgrade head)
#   - api             Cloud Run service (main.py, HTTP)
#   - worker          Cloud Run worker pool (worker.py, Pub/Sub pull loop)
#   - crawl-worker    Cloud Run worker pool (crawl_worker.py, Pub/Sub pull loop)
#   - crawl-dispatcher  Cloud Function 2nd gen (crawl_dispatcher.py:dispatch)
#                       + Cloud Scheduler cron trigger
#   - browser-fetch   already deployed separately; see BROWSER_FETCH_SERVICE_URL below
#
# Prereqs this script assumes already exist (create once, not here):
#   - `gcloud auth login` + billing enabled on $PROJECT_ID
#   - `gcloud components update` (worker pools need a recent gcloud)
#   - If `gcloud beta run worker-pools ...` fails with "No module named
#     'grpc'": your gcloud's Python lacks grpcio and Homebrew's Python
#     blocks a global `pip install` (PEP 668). Fix without touching system
#     Python:
#       python3 -m venv ~/.gcloud-venv
#       ~/.gcloud-venv/bin/pip install grpcio
#       export CLOUDSDK_PYTHON=~/.gcloud-venv/bin/python3
#     then re-run the worker-pools commands in this shell.
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
create_secret_from_env scraperapi-key              SCRAPERAPI_KEY
create_secret_from_env system-llm-api-key          SYSTEM_LLM_API_KEY               deploy/.env.production
# Real AWS creds for the yabot-jobs-backend IAM user (scoped to just the
# yabot.jobs-files bucket) — kept out of the local dev .env, which stays
# pointed at MinIO. See deploy/.env.production.
create_secret_from_env resume-storage-access-key   RESUME_STORAGE_ACCESS_KEY_ID     deploy/.env.production
create_secret_from_env resume-storage-secret-key   RESUME_STORAGE_SECRET_ACCESS_KEY deploy/.env.production

# Non-secret, shared across api/worker/crawl-worker:
COMMON_ENV="GCP_PROJECT_ID=${PROJECT_ID},BROWSER_FETCH_SERVICE_URL=${BROWSER_FETCH_SERVICE_URL},FRONTEND_BASE_URL=https://app.yabot.jobs,SESSION_COOKIE_SECURE=true,SYSTEM_LLM_PROVIDER=deepseek,SYSTEM_LLM_MODEL=deepseek-v4-flash,RESUME_STORAGE_BUCKET=yabot.jobs-files,RESUME_STORAGE_REGION=us-east-1"

COMMON_SECRETS="DATABASE_URL=database-url:latest,API_KEY_ENCRYPTION_KEY=api-key-encryption-key:latest,SCRAPERAPI_KEY=scraperapi-key:latest,SYSTEM_LLM_API_KEY=system-llm-api-key:latest,RESUME_STORAGE_ACCESS_KEY_ID=resume-storage-access-key:latest,RESUME_STORAGE_SECRET_ACCESS_KEY=resume-storage-secret-key:latest"

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
  --set-secrets="$COMMON_SECRETS"

gcloud run jobs execute migrate --region="$REGION" --wait

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
  --allow-unauthenticated

# ---------------------------------------------------------------------------
# 5. worker / crawl-worker — Cloud Run worker pools
#    (persistent Pub/Sub pull loops, no HTTP port; see conversation notes on
#    why these are pools instead of Cloud Functions)
#
#    IMPORTANT: as of this gcloud version, worker pools only support a fixed
#    manual instance count via --instances=N — there is no --min-instances/
#    --max-instances autoscaling (and no scale-to-zero) for this resource
#    type yet, unlike Cloud Run services. Cost is 1 instance running 24/7
#    per pool at whatever --cpu/--memory you set (defaults if omitted).
#
#    This gcloud install needed `gcloud components update` plus a separate
#    grpcio install to even load this command group — see the venv setup
#    noted at the top of this file if `worker-pools` errors with
#    "No module named 'grpc'".
# ---------------------------------------------------------------------------

gcloud beta run worker-pools deploy worker \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=worker.py \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --instances=1

gcloud beta run worker-pools deploy crawl-worker \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=crawl_worker.py \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --instances=1

# ---------------------------------------------------------------------------
# 6. crawl-dispatcher — Cloud Function (2nd gen), triggered daily by Scheduler
#    Deploys from source (this repo), entry point is dispatch() in
#    crawl_dispatcher.py.
#
#    `gcloud functions deploy` has NO --set-cloudsql-instances flag (2nd gen
#    functions are Cloud Run services under the hood, but the functions CLI
#    doesn't expose this). Deploy the function first, then attach Cloud SQL
#    to its underlying Cloud Run service with `gcloud run services update`.
#
#    The Python buildpack looks for the entry-point function in main.py by
#    default — but this repo's own main.py is the FastAPI app, not the
#    dispatcher, so deploy fails with "main.py is expected to contain a
#    function named 'dispatch'". GOOGLE_FUNCTION_SOURCE points the buildpack
#    at crawl_dispatcher.py instead.
# ---------------------------------------------------------------------------

gcloud functions deploy crawl-dispatcher \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=crawl_dispatcher.py \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s

gcloud run services update crawl-dispatcher \
  --region="$REGION" \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION"

# Give Cloud Scheduler's service account permission to invoke the function,
# then wire up the daily cron trigger via OIDC (no public HTTP exposure).

FUNCTION_URL="$(gcloud functions describe crawl-dispatcher --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"

gcloud functions add-invoker-policy-binding crawl-dispatcher \
  --gen2 \
  --region="$REGION" \
  --member="serviceAccount:${PROJECT_ID}@appspot.gserviceaccount.com"

gcloud scheduler jobs create http crawl-dispatch-daily \
  --location="$REGION" \
  --schedule="0 6 * * *" \
  --uri="$FUNCTION_URL" \
  --http-method=POST \
  --oidc-service-account-email="${PROJECT_ID}@appspot.gserviceaccount.com" \
  --oidc-token-audience="$FUNCTION_URL"

# ---------------------------------------------------------------------------
# Redeploys after this point (new image, no infra changes):
#   gcloud builds submit --tag "${IMAGE}:$(git rev-parse --short HEAD)" .
#   gcloud run jobs execute migrate --region="$REGION" --wait
#   gcloud run deploy api --image="$IMAGE_TAG" --region="$REGION"
#   gcloud beta run worker-pools deploy worker --image="$IMAGE_TAG" --region="$REGION"
#   gcloud beta run worker-pools deploy crawl-worker --image="$IMAGE_TAG" --region="$REGION"
#   gcloud functions deploy crawl-dispatcher --gen2 --region="$REGION" --source=. --entry-point=dispatch
# ---------------------------------------------------------------------------
