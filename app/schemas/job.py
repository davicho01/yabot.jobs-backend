import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.models.enums import FlagReason


class JobUrlSubmit(BaseModel):
    url: HttpUrl


class JobFlagCreate(BaseModel):
    reason: FlagReason
    note: str | None = None


class MetroRead(BaseModel):
    """A searchable area — a metro/micro area or a state — and how many postings
    fall in it."""

    slug: str
    name: str
    kind: str  # "metro" | "micro" | "state"
    count: int


class JobPostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url_id: uuid.UUID
    # None when the response was built for an anonymous caller — see
    # to_job_detail's include_url flag.
    apply_url: str | None
    title: str | None
    company_name: str | None
    # Null when the company's domain isn't known yet or logos aren't
    # configured — see app.services.company_logos.
    company_logo_url: str | None = None
    company_domain: str | None = None
    # Normalized company identity (see app.services.job_dedup) — what the
    # admin company-domain fix (PATCH /admin/companies) is keyed by.
    company_key: str | None = None
    location: str | None
    workplace_type: str
    employment_type: str
    sector: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    extracted_fields: dict[str, Any] | None
    posted_at: date | None
    scanned_at: datetime | None
    extraction_status: str
    # How many other JobPostingUrls this same job was also found at (see
    # app.services.job_dedup) — 0 for most postings. Only meaningful on a
    # canonical posting; GET /jobs already only returns those.
    also_posted_count: int


class JobPostingUrlRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # None when the response was built for an anonymous caller — see
    # to_job_detail's include_url flag.
    url: str | None
    domain: str
    scan_status: str
    scan_error: str | None
    crawl_source_id: uuid.UUID | None
    last_scanned_at: datetime | None
    created_at: datetime
    # Set once the listing is gone from its board or found expired (see
    # app.services.jobs.record_board_presence) — such a job is out of search
    # but still reachable by id, e.g. from a user's Applications.
    closed_at: datetime | None = None
    # Populated from the row regardless of caller, then nulled out by
    # to_job_detail unless include_flag=True (admin-only) — another user's
    # report shouldn't be visible to a regular viewer of the listing.
    flagged_at: datetime | None = None
    flag_reason: str | None = None
    flag_note: str | None = None


class JobDetailRead(BaseModel):
    """A URL plus its current (most recent) extracted posting, if any."""

    url: JobPostingUrlRead
    posting: JobPostingRead | None


class SearchAreaRead(BaseModel):
    """What a city search covered: the city and how far around it was looked."""

    label: str  # "West Bountiful, Utah"
    radius_miles: int


class JobListRead(BaseModel):
    items: list[JobDetailRead]
    total: int
    # True when there are more matches than `total` — GET /jobs only counts
    # so far past the current page (see list_job_urls).
    total_is_capped: bool = False
    page: int
    page_size: int
    search_area: SearchAreaRead | None = None  # set when the location search was a city, searched by distance


class SimilarJobsRead(BaseModel):
    """See app.services.jobs.find_similar_job_urls."""

    same_company: list[JobDetailRead]
    similar_title: list[JobDetailRead]
