import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.crawl_source import MAX_CONCURRENT_SCANS, MIN_CONCURRENT_SCANS
from app.models.enums import CrawlSourceStatus

_VALID_STATUSES = {v.value for v in CrawlSourceStatus}


class CrawlSourceCreate(BaseModel):
    name: str = Field(min_length=1)
    # The board's canonical URL — ats_type (and whatever internal
    # identifier that platform's adapter needs) is auto-detected from its
    # shape, see app.services.ats_adapters.detect_ats_source. Always
    # creates an "active" board; fails with 422 if the platform can't be
    # detected/supported, instead of leaving a placeholder row (unlike
    # boards auto-registered from a single job URL via POST /jobs, which
    # fall back to "pending").
    board_url: str = Field(min_length=1)


class CrawlSourceUpdate(BaseModel):
    # The company's official name, shown on every job from this source.
    # Setting it (or sub_brands) marks the name "manual" and renames the
    # source's existing jobs — see app.services.company_names.
    name: str | None = Field(default=None, min_length=1)
    # Brands a job shows instead of `name` when its page names one
    # (TJX: ["HomeGoods", "Marshalls"]).
    sub_brands: list[str] | None = None
    # The company's own careers site (true) vs a job board/aggregator.
    is_official: bool | None = None
    # Set this (together with status="active") to promote a "pending"
    # board once an adapter for it has been verified and implemented — or
    # set status="rejected" alone if it turns out unsupportable. ats_type
    # is re-derived from the new board_url automatically.
    board_url: str | None = None
    status: str | None = None
    # How many of this board's job pages may be fetched at once — lower it
    # for a site that's sensitive to load.
    max_concurrent_scans: int | None = Field(default=None, ge=MIN_CONCURRENT_SCANS, le=MAX_CONCURRENT_SCANS)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return value

    @field_validator("sub_brands")
    @classmethod
    def _validate_sub_brands(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            raise ValueError("sub_brands cannot be null (send [] for none)")
        # Trimmed, blanks dropped, duplicates removed (first spelling kept).
        seen, brands = set(), []
        for brand in (" ".join(b.split()) for b in value):
            if brand and brand.lower() not in seen:
                seen.add(brand.lower())
                brands.append(brand)
        return brands

    @field_validator("max_concurrent_scans")
    @classmethod
    def _validate_max_concurrent_scans(cls, value: int | None) -> int | None:
        # Only runs when the field is sent (the default isn't validated), so
        # an explicit null — which the NOT NULL column can't take — is rejected
        # here instead of surfacing as a DB error.
        if value is None:
            raise ValueError("max_concurrent_scans cannot be null")
        return value


class CrawlSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    # "placeholder" (an unconfirmed auto label), "auto" or "manual".
    name_source: str
    sub_brands: list[str]
    is_official: bool
    ats_type: str | None
    board_url: str
    status: str
    max_concurrent_scans: int
    last_crawled_at: datetime | None
    last_error: str | None
    # System-managed coverage monitoring (see app.services.coverage_monitor)
    # — not settable via CrawlSourceUpdate.
    coverage_last_count: int | None
    coverage_baseline: float | None
    coverage_sample_count: int
    coverage_flagged_at: datetime | None
    created_at: datetime
    # The logo of the company this source is named after (see
    # app.api.routes.crawl_sources.list_crawl_sources); only set in listings.
    logo_url: str | None = None
