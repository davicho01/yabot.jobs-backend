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
#   - A Cloud SQL for Postgres instance with the yabot_jobs database
#   - `gcloud auth login` + `gcloud config set project $PROJECT_ID`
#   - `gcloud components update` (worker pools need a recent gcloud;
#     verify with `gcloud beta run worker-pools deploy --help` before
#     running that section — the beta command surface has been changing)
#
# Run section by section, not all at once — read the comments first.

set -euo pipefail

PROJECT_ID="yabot-jobs"                 # <-- set me
REGION="us-central1"
REPO="yabot-jobs"                       # Artifact Registry repo name
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend"
CLOUDSQL_INSTANCE_CONNECTION="${PROJECT_ID}:${REGION}:yabot-jobs-db"  # <-- set me
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

create_secret_from_env database-url               DATABASE_URL
create_secret_from_env api-key-encryption-key      API_KEY_ENCRYPTION_KEY
create_secret_from_env scraperapi-key              SCRAPERAPI_KEY
create_secret_from_env system-llm-api-key          SYSTEM_LLM_API_KEY
# Real AWS creds for the yabot-jobs-backend IAM user (scoped to just the
# yabot.jobs-files bucket) — kept out of the local dev .env, which stays
# pointed at MinIO. See deploy/.env.production.
create_secret_from_env resume-storage-access-key   RESUME_STORAGE_ACCESS_KEY_ID     deploy/.env.production
create_secret_from_env resume-storage-secret-key   RESUME_STORAGE_SECRET_ACCESS_KEY deploy/.env.production

# NOTE: for Cloud SQL, DATABASE_URL should use the unix-socket form instead
# of the local docker-compose one, e.g.:
#   postgresql+psycopg://USER:PASSWORD@/yabot_jobs?host=/cloudsql/PROJECT:REGION:INSTANCE

# Non-secret, shared across api/worker/crawl-worker:
COMMON_ENV="GCP_PROJECT_ID=${PROJECT_ID},BROWSER_FETCH_SERVICE_URL=${BROWSER_FETCH_SERVICE_URL},FRONTEND_BASE_URL=https://app.yabot.jobs,SESSION_COOKIE_SECURE=true,SYSTEM_LLM_PROVIDER=anthropic,SYSTEM_LLM_MODEL=claude-opus-5,RESUME_STORAGE_BUCKET=yabot.jobs-files,RESUME_STORAGE_REGION=us-east-1"

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
#    Verify flags first — this is a newer gcloud surface:
#      gcloud beta run worker-pools deploy --help
#    If your gcloud version doesn't have `worker-pools` yet:
#      gcloud components update
# ---------------------------------------------------------------------------

gcloud beta run worker-pools deploy worker \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=worker.py \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --min-instances=0 \
  --max-instances=5

gcloud beta run worker-pools deploy crawl-worker \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --command=python --args=crawl_worker.py \
  --add-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --min-instances=0 \
  --max-instances=5

# ---------------------------------------------------------------------------
# 6. crawl-dispatcher — Cloud Function (2nd gen), triggered daily by Scheduler
#    Deploys from source (this repo), entry point is dispatch() in
#    crawl_dispatcher.py. Cloud SQL access for Cloud Functions 2nd gen also
#    goes through --set-cloudsql-instances.
# ---------------------------------------------------------------------------

gcloud functions deploy crawl-dispatcher \
  --gen2 \
  --region="$REGION" \
  --runtime=python313 \
  --source=. \
  --entry-point=dispatch \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE_CONNECTION" \
  --set-env-vars="$COMMON_ENV" \
  --set-secrets="$COMMON_SECRETS" \
  --memory=512Mi \
  --timeout=540s

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
