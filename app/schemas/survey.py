import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The answer slugs. The frontend (src/components/SurveyModal.tsx) shows labels
# for these; anything else is rejected so the admin counts stay clean.
Source = Literal["google", "reddit", "friend", "social", "ai_chat", "other"]
SearchDuration = Literal["just_started", "lt_1m", "1_3m", "3_6m", "6m_plus", "employed_browsing"]
Industry = Literal[
    "tech", "healthcare", "finance", "education", "retail", "hospitality", "manufacturing_logistics",
    "construction_trades", "government_nonprofit", "marketing_media", "legal", "energy_utilities", "other",
]
WorkArrangement = Literal["remote", "hybrid", "on_site"]
Rating = Literal["very_useful", "somewhat_useful", "dont_care", "confusing", "didnt_notice"]
Liked = Literal["fresh_jobs", "company_sites", "search_filters", "alerts", "clean_site", "no_ads"]
Wanted = Literal["better_search", "salary_info", "resume_tools", "app_tracking", "more_companies", "mobile_app"]


class SurveySubmit(BaseModel):
    """A page of answers. Only the fields sent are saved, so page 1 can be
    posted on its own (completed=false) and page 2 later (completed=true)."""

    visitor_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    completed: bool = False

    source: Source | None = None
    search_duration: SearchDuration | None = None
    industries: list[Industry] | None = Field(default=None, max_length=3)
    role_name: str | None = Field(default=None, max_length=120)
    work_arrangement: list[WorkArrangement] | None = Field(default=None, max_length=3)

    verification_dates_rating: Rating | None = None
    dedup_rating: Rating | None = None
    liked: list[Liked] | None = Field(default=None, max_length=6)
    wanted: list[Wanted] | None = Field(default=None, max_length=6)
    comments: str | None = Field(default=None, max_length=5000)
    contact_email: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    # Signed-in visitors' "OK to contact me" checkbox; ignored when signed out.
    contact_ok: bool | None = None


class SurveyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    completed: bool


class AdminSurveyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    completed: bool
    user_id: uuid.UUID | None
    user_email: str | None
    contact_email: str | None
    contact_ok: bool
    source: str | None
    search_duration: str | None
    industries: list[str] | None
    role_name: str | None
    work_arrangement: list[str] | None
    verification_dates_rating: str | None
    dedup_rating: str | None
    liked: list[str] | None
    wanted: list[str] | None
    comments: str | None


class AdminSurveyListRead(BaseModel):
    items: list[AdminSurveyRead]
    total: int
    completed_count: int
    # question -> answer slug -> number of responses, across ALL responses
    # (ignores the page), for the summary at the top of the admin page.
    counts: dict[str, dict[str, int]]
