from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.crawl_source import CrawlSourceRead


class ScanDayCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    count: int


class ScanHourCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hour: datetime
    count: int


class ScanWeekCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    week: date
    count: int


class ScanMonthCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    month: date
    count: int


class WindowCounts(BaseModel):
    last_24h: int
    last_7d: int
    last_30d: int
    last_90d: int


class DashboardTotals(BaseModel):
    job_listings: int
    jobs_flagged: int
    crawl_sources: int
    crawl_sources_flagged: int
    users: int


class ScanStatusCounts(BaseModel):
    """How many JobPostingUrls are in each ScanStatus right now (not
    windowed, unlike WindowCounts — a point-in-time snapshot). `failed` will
    retry automatically; `needs_review` gave up and needs a human rescan."""

    pending: int
    success: int
    failed: int
    needs_review: int


class AdminDashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    totals: DashboardTotals
    users_joined: WindowCounts
    user_activity: WindowCounts
    application_scans: WindowCounts
    scan_status_counts: ScanStatusCounts


class CrawlSourceStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: CrawlSourceRead
    total_listings: int
    listings_added: WindowCounts
    scans: WindowCounts
    scan_status_counts: ScanStatusCounts


class AdminCompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_key: str
    display_name: str
    domain: str | None
    domain_source: str | None
    logo_url: str | None = None
    # Where the logo came from ("logo_dev", or "url"/"upload" when an admin
    # set it), the last automatic lookup's outcome (ok / none / error, null =
    # not tried yet), and the source address — why a company has an avatar.
    logo_origin: str | None = None
    logo_status: str | None = None
    logo_source_url: str | None = None
    # Canonical postings under this company — what the "missing domain"
    # list is sorted by, so the companies most users see get fixed first.
    posting_count: int = 0


class AdminCompanyDomainUpdate(BaseModel):
    company_key: str
    # A bare domain or any URL on it ("acme.com", "https://careers.acme.com/");
    # null/blank clears a manual override back to automatic resolution.
    domain: str | None = None


class AdminCompanyLogoUrl(BaseModel):
    company_key: str
    # Used only if the company row doesn't exist yet (a brand-new source).
    display_name: str | None = None
    # An image's address, or any page the logo is on (e.g. the homepage).
    url: str
