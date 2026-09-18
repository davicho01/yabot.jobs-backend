import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, api_keys, applications, auth, auth_tokens, crawl_sources, jobs, oauth, resumes
from app.core.config import settings
from app.services.resume_storage import ensure_bucket_exists

logging.basicConfig(level=logging.INFO)
# httpx logs the full request URL at INFO level on every call — noisy given
# how many fetches one scan makes (job page, ATS APIs, browser_fetch_service).
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_bucket_exists()
    yield


app = FastAPI(title="Yabot Jobs API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(auth_tokens.router)
app.include_router(oauth.router)
app.include_router(api_keys.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(crawl_sources.router)
app.include_router(resumes.router)
app.include_router(admin.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
