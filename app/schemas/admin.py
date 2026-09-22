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


class WindowCounts(BaseModel):
    last_24h: int
    last_7d: int
    last_30d: int
    last_90d: int


class DashboardTotals(BaseModel):
    job_listings: int
    crawl_sources: int
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
