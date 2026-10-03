from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings parses .env for the fields declared below, but that
# doesn't put the values in os.environ — vars like PUBSUB_EMULATOR_HOST and
# GOOGLE_APPLICATION_CREDENTIALS are read directly from the environment by
# the google-cloud SDKs (see job_queue.py) and need an actual load_dotenv()
# to reach them when running outside docker compose (which sets them as
# real container env vars instead).
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/yabot_jobs"

    # Fernet key used to encrypt/decrypt stored LLM API keys.
    # Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    api_key_encryption_key: str

    # App-wide LLM key used for job-posting extraction (every scan, whether
    # user-submitted or crawler-discovered). The per-user UserApiKey /
    # is_default mechanism (see app/models/api_key.py, /api-keys routes)
    # stays in the codebase for a future "bring your own key" feature, but
    # isn't consulted by extraction right now — this system-wide key is.
    system_llm_provider: str | None = None  # e.g. "anthropic"
    system_llm_model: str | None = None  # e.g. "claude-opus-5"
    system_llm_api_key: str | None = None
    system_llm_base_url: str | None = None  # only for provider="other"/custom endpoints
    # Free job evaluations a user without their own key gets on the system
    # key above (see app.services.ai_access). 0 turns the trial off; it's
    # also off whenever the system key isn't configured.
    free_evaluation_limit: int = 10
    # Free resume structurings (the AI turning an uploaded resume into
    # editable sections) for a user without their own key or the plan,
    # counted separately from free_evaluation_limit. Each upload uses one
    # automatically; 0 turns it off. Same system key.
    free_restructure_limit: int = 3

    # Stripe billing for the paid plan (see app.services.billing): subscribers
    # use every resume feature on the system key above instead of their own.
    # Selling it needs all three plus a working SYSTEM_LLM_* key.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # The recurring Price (e.g. $5/month) that Checkout sells.
    stripe_price_id: str | None = None
    # Shown in the UI and error messages — keep in sync with the Stripe price.
    subscription_price_label: str = "$5/month"
    # Optional cap on system-key requests per billing period per subscriber.
    # 0 (the default) means unlimited: at DeepSeek pricing even heavy use
    # costs well under the plan price (see ROADMAP.md in the frontend repo),
    # so this is only a lever in case abuse shows up.
    subscription_monthly_request_limit: int = 0

    # Google Cloud Pub/Sub — job scanning is queued here instead of running
    # inline in POST /jobs (page fetches + LLM calls can take well over a
    # minute). GOOGLE_APPLICATION_CREDENTIALS and PUBSUB_EMULATOR_HOST are
    # read directly by the google-cloud-pubsub SDK from the environment and
    # don't need to be modeled here — set PUBSUB_EMULATOR_HOST for local dev
    # against the emulator, or GOOGLE_APPLICATION_CREDENTIALS to point at a
    # real GCP project (see docker-compose.yml for the local emulator setup).
    gcp_project_id: str
    pubsub_topic_id: str = "job-scan-requests"
    pubsub_subscription_id: str = "job-scan-requests-worker"

    # Separate topic/subscription for crawl-source dispatch (see
    # crawl_dispatcher.py / crawl_worker.py) so crawl-fan-out traffic doesn't
    # share a queue with individual job-scan requests.
    pubsub_crawl_topic_id: str = "crawl-source-requests"
    pubsub_crawl_subscription_id: str = "crawl-source-requests-worker"

    # Per-source scan throttling (see app.services.scan_claims and
    # app.services.jobs.run_source_lane). A lane pauses this long after each
    # page fetch so a source never sees back-to-back requests, stops taking
    # new work after the deadline (re-queueing itself so other sources get
    # the instance), and a claim older than the TTL is treated as abandoned
    # by a crashed lane — keep it above the scan function's 540s timeout so
    # a live scan is never mistaken for a dead one.
    scan_min_interval_seconds: float = 1.0
    scan_lane_deadline_seconds: float = 240.0
    scan_claim_ttl_seconds: float = 600.0

    # How long a CrawlSource.crawl_claimed_at claim (see that column's own
    # comment, and crawl_dispatcher.py/crawl_worker.py) is honored before a
    # source becomes dispatchable again regardless — keep it above
    # crawl-worker's 540s timeout so a crawl that's still genuinely running
    # is never mistaken for one whose message/worker was lost.
    crawl_claim_ttl_seconds: float = 600.0

    # Retry backoff for a FAILED scan (see app.services.jobs.wake_retryable_failed_scans
    # and retry_failed_scans.py, its hourly-cron entrypoint). Each consecutive failure
    # multiplies the previous wait, capped at scan_retry_max_seconds: base * multiplier
    # ** (attempts - 1), so the default schedule is 1h, 4h, 16h, 24h(capped) — same-day
    # recovery from a transient block, never faster than hourly regardless of sweep
    # cadence. After scan_retry_max_attempts consecutive failures the row gives up
    # (ScanStatus.NEEDS_REVIEW) rather than retrying forever.
    scan_retry_base_seconds: float = 3600.0
    scan_retry_backoff_multiplier: float = 4.0
    scan_retry_max_seconds: float = 86400.0
    scan_retry_max_attempts: int = 5

    # How long a crawl-sourced URL can go unlisted on its board before it
    # counts as closed (see app.services.jobs.record_board_presence). Only a
    # healthy crawl ever closes anything, and crawl-dispatch runs 2x/day, so
    # 36h means a job has to be missing from about three crawls in a row.
    job_closed_after_unseen_hours: float = 36.0

    # S3-compatible object storage for uploaded resumes / generated tailored
    # resume files. Point resume_storage_endpoint_url at a local MinIO (see
    # docker-compose.yml) for dev, or leave it unset to use real AWS S3.
    resume_storage_bucket: str = "yabot-resumes"
    resume_storage_endpoint_url: str | None = None
    resume_storage_region: str = "us-east-1"
    resume_storage_access_key_id: str
    resume_storage_secret_access_key: str

    # Where generate_static_job_pages.py publishes the crawlable "jobs by
    # sector, by day" pages (see that script) — the *frontend's* S3
    # bucket/CloudFront distribution (yabot.jobs itself), not the resume
    # bucket above. Reuses resume_storage's AWS access key/secret (same IAM
    # user, though its policy may need widening to cover this bucket too) —
    # no separate credentials modeled here. No default: prod sets it
    # explicitly (COMMON_ENV in deploy/gcloud-deploy.sh), and local dev sets
    # seo_pages_output_dir instead, so a local run can't write into prod.
    seo_pages_bucket: str | None = None
    # Local dev: write the pages into this directory instead of S3 (see
    # app.services.page_store) — the frontend's `npm run dev` serves them.
    seo_pages_output_dir: str | None = None
    seo_pages_region: str = "us-east-1"
    seo_pages_base_url: str = "https://yabot.jobs"
    # CloudFront distribution ID fronting seo_pages_bucket, for invalidating
    # just-written paths after each run. None skips invalidation (local dev).
    seo_pages_cloudfront_distribution_id: str | None = None

    # Standalone Chromium-rendering service for the rare sites that need JS
    # execution to reveal their content (see app/services/browser_fetch.py).
    # Unset just means that fallback is skipped.
    browser_fetch_service_url: str | None = None

    # logo.dev secret key (sk_...) for automatic company logos — server-side
    # only, used by the logo sync to download each logo once into our own
    # storage (see app.services.logo_dev). Never sent to browsers. Unset
    # means no automatic logos; manual ones still work.
    logo_dev_secret_key: str | None = None

    # AWS SES for magic-link login emails (see app/services/email.py).
    # from_address's domain must be a verified SES identity. Credentials are
    # separate from resume storage's (even though both currently point at
    # the same IAM user) so either can be rotated/scoped independently.
    # Leaving the keys unset makes send_magic_link_email() log-only, so
    # local dev works without any AWS setup.
    email_from_address: str = "noreply@yabot.jobs"
    email_sender_region: str = "us-east-1"
    email_sender_access_key_id: str | None = None
    email_sender_secret_access_key: str | None = None

    magic_link_ttl_minutes: int = 15
    session_ttl_days: int = 30

    # Throttles against magic-link abuse (SES send cost, spam to one inbox,
    # one client cycling through many addresses) — see
    # app.services.auth.enforce_magic_link_rate_limit. Counted straight from
    # MagicLinkToken rows already in the table, no separate counter needed.
    magic_link_rate_limit_window_minutes: float = 60.0
    magic_link_rate_limit_max_per_email: int = 5
    magic_link_rate_limit_max_per_ip: int = 20

    # Per-user cap on brand-new job-URL submissions — the ones that create a
    # JobPostingUrl row and trigger a real LLM scan (see
    # app.services.jobs._enforce_submission_rate_limit). Re-submitting an
    # already-known URL doesn't count: it's a cheap dedup lookup, not a new
    # scan.
    job_submission_rate_limit_window_minutes: float = 60.0
    job_submission_rate_limit_max_new_urls: int = 20

    # Per-user cap on in-app feedback/support submissions
    # (app.services.feedback.create_feedback) — each one can email every
    # admin, so this keeps one stuck form or script from flooding inboxes.
    feedback_rate_limit_window_minutes: float = 60.0
    feedback_rate_limit_max_per_user: int = 10

    # Per-user cap on saved searches (app.api.routes.saved_searches) — each
    # one gets re-run against every posting on every alert sweep
    # (saved_search_alerts.py), so this also bounds that sweep's per-user cost.
    saved_search_max_per_user: int = 5

    # Where the emailed magic link points the user's browser (a frontend
    # route that reads ?token=... and POSTs it to /auth/verify).
    frontend_base_url: str = "http://localhost:3000"
    session_cookie_name: str = "session_token"
    # False for local http dev; set true behind https in real deployments.
    session_cookie_secure: bool = False

    # Comma-separated emails auto-promoted to admin on login/creation (see
    # app.services.auth.get_or_create_user). There's no admin UI to grant
    # the role yet — this env var is the only way to create one.
    admin_emails: str = ""

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()
