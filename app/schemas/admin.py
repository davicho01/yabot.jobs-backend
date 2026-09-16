from datetime import date

from pydantic import BaseModel, ConfigDict

from app.schemas.crawl_source import CrawlSourceRead


class ScanDayCount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
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


class AdminDashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    totals: DashboardTotals
    users_joined: WindowCounts
    user_activity: WindowCounts
    application_scans: WindowCounts


class CrawlSourceStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: CrawlSourceRead
    total_listings: int
    listings_added: WindowCounts
    scans: WindowCounts
