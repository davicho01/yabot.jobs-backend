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

    # App-wide ScraperAPI key (https://www.scraperapi.com/) used as a fallback
    # fetcher when a direct request to a job posting URL is blocked (403,
    # bot-detection challenge, etc). Optional — scanning still works without
    # it, just fails on sites that block plain HTTP clients.
    scraperapi_key: str | None = None

    # App-wide LLM key used for job-posting extraction (every scan, whether
    # user-submitted or crawler-discovered). The per-user UserApiKey /
    # is_default mechanism (see app/models/api_key.py, /api-keys routes)
    # stays in the codebase for a future "bring your own key" feature, but
    # isn't consulted by extraction right now — this system-wide key is.
    system_llm_provider: str | None = None  # e.g. "anthropic"
    system_llm_model: str | None = None  # e.g. "claude-opus-5"
    system_llm_api_key: str | None = None
    system_llm_base_url: str | None = None  # only for provider="other"/custom endpoints

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

    # S3-compatible object storage for uploaded resumes / generated tailored
    # resume files. Point resume_storage_endpoint_url at a local MinIO (see
    # docker-compose.yml) for dev, or leave it unset to use real AWS S3.
    resume_storage_bucket: str = "yabot-resumes"
    resume_storage_endpoint_url: str | None = None
    resume_storage_region: str = "us-east-1"
    resume_storage_access_key_id: str
    resume_storage_secret_access_key: str

    # Standalone Chromium-rendering service for the rare sites that need JS
    # execution to reveal their content (see app/services/browser_fetch.py).
    # Unset just means that fallback is skipped.
    browser_fetch_service_url: str | None = None

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
