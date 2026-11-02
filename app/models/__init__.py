from app.models.api_key import UserApiKey
from app.models.auth import MagicLinkToken, PersonalAccessToken, UserSession
from app.models.auto_score_run import AutoScoreRun
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.device_token import DeviceToken
from app.models.feedback import Feedback
from app.models.free_trial_job import FreeTrialJob
from app.models.job_application import UserJobApplication
from app.models.job_collection import JobCollection, JobCollectionEvent
from app.models.job_posting import JobPosting
from app.models.job_share import JobShare
from app.models.job_url import JobPostingUrl
from app.models.oauth import OAuthAuthorizationRequest, OAuthClient, OAuthRefreshToken
from app.models.resume import Resume, ResumeReview, ResumeScore, ResumeSkillAddition, TailoredResume
from app.models.saved_search import SavedSearch
from app.models.search_opportunity import SearchOpportunity
from app.models.search_performance import SearchPerformanceDay
from app.models.subscription_evaluation_job import SubscriptionEvaluationJob
from app.models.user import User
from app.models.user_activity import UserActiveHour

__all__ = [
    "User",
    "UserActiveHour",
    "MagicLinkToken",
    "UserSession",
    "PersonalAccessToken",
    "UserApiKey",
    "JobPostingUrl",
    "JobPosting",
    "JobShare",
    "UserJobApplication",
    "JobCollection",
    "JobCollectionEvent",
    "CrawlSource",
    "Company",
    "Resume",
    "ResumeReview",
    "ResumeScore",
    "ResumeSkillAddition",
    "TailoredResume",
    "SavedSearch",
    "SearchPerformanceDay",
    "SearchOpportunity",
    "OAuthClient",
    "OAuthAuthorizationRequest",
    "OAuthRefreshToken",
    "Feedback",
    "FreeTrialJob",
    "SubscriptionEvaluationJob",
    "AutoScoreRun",
]
