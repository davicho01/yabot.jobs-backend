from enum import StrEnum

# These are stored as plain VARCHAR columns (not Postgres native ENUM types)
# so adding a new value is an application deploy, not a migration.


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class LlmProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    GOOGLE = "google"
    MISTRAL = "mistral"
    OTHER = "other"


class WorkplaceType(StrEnum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class ScanStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class AtsType(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    BAMBOOHR = "bamboohr"
    PERSONIO = "personio"
    WORKDAY = "workday"
    JAZZHR = "jazzhr"
    RECRUITEE = "recruitee"
    BREEZYHR = "breezyhr"
    WORKABLE = "workable"
    ADP = "adp"
    EIGHTFOLD = "eightfold"
    ORACLE_FUSION = "oracle_fusion"
    CLINCH = "clinch"
    TALENTBREW = "talentbrew"
    NLX = "nlx"
    # Single-company, in-house career sites (not a platform other companies
    # use) — board_key is a fixed constant, not a variable company slug.
    AMAZON = "amazon"
    GOOGLE = "google"
    APPLE = "apple"


class CrawlSourceStatus(StrEnum):
    # Detected ats_type not yet implemented (or, rarely, a manually-added
    # board awaiting its first crawl) — a queue of platforms worth
    # investigating.
    PENDING = "pending"
    # ats_type/board_key known and being crawled daily.
    ACTIVE = "active"
    # Investigated; no viable public API, will never be implemented.
    REJECTED = "rejected"
    # Investigated and determined the row itself is wrong and shouldn't
    # exist — e.g. a stale duplicate of a board that's active under a
    # different (canonical) board_url — rather than "can't be crawled."
    # A human-review marker only: never delete a row outright (see
    # DELETE /admin/crawl-sources/{id}), just flag it here and leave the
    # actual delete to a human who's confirmed it.
    DELETE = "delete"


class ApplicationStatus(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
