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
    # Will be retried automatically (see app.services.jobs.wake_retryable_failed_scans)
    # until scan_retry_max_attempts is reached, at which point it becomes NEEDS_REVIEW.
    FAILED = "failed"
    # Gave up after scan_retry_max_attempts consecutive failures — distinct from
    # FAILED so "still retrying" and "needs a human" aren't the same bucket in the
    # admin dashboard. Only a deliberate rescan (which resets the attempt count)
    # gets a row out of this state.
    NEEDS_REVIEW = "needs_review"


class AtsType(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    GEM = "gem"
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
    ECHO_JOBS = "echo_jobs"
    TALENTBREW = "talentbrew"
    NLX = "nlx"
    CLEARCOMPANY = "clearcompany"
    # Single-company, in-house career sites (not a platform other companies
    # use) — board_key is a fixed constant, not a variable company slug.
    AMAZON = "amazon"
    GOOGLE = "google"
    APPLE = "apple"
    FULLSTACK = "fullstack"
    MOTION_RECRUITMENT = "motion_recruitment"
    DYNATRACE = "dynatrace"
    GLIDEFAST = "glidefast"
    SUCCESSFACTORS = "successfactors"
    # Walmart's in-house career site (careers.walmart.com) — also hosts
    # Sam's Club postings under the same board, distinguished per-job by
    # jobDetails.brand (see adapters/walmart.py).
    WALMART = "walmart"
    # Best Buy's careers site (careers.bestbuy.com) — built on ServiceNow's
    # Service Portal, not any ATS platform (see adapters/bestbuy.py).
    BESTBUY = "bestbuy"
    # Paycor Recruiting (né Newton) — a shared multi-tenant ATS, white-labeled
    # onto each customer's own careers domain via a "gnewton" embed script, no
    # customer-visible ATS host in the URL at all.
    PAYCOR_RECRUITING = "paycor_recruiting"
    # Scan-only: job URLs are only ever submitted directly, never discovered
    # by crawling a board (no ADAPTER.fetch_jobs) — see
    # app.services.adapters.stripe.
    STRIPE = "stripe"
    # Attrax — a career-site CMS white-labeled onto each customer's own
    # domain (e.g. careers.abbvie.com), no shared host to detect statically.
    ATTRAX = "attrax"
    # Phenom People — a career-site CMS white-labeled onto each customer's
    # own domain (e.g. careers.gene.com), no shared host to detect
    # statically; no usable public API found, discovered via SEO sitemap.
    PHENOM = "phenom"
    # iCIMS — two very different products under one brand: "classic"
    # (server-rendered {tenant}.icims.com) and "Jibe/Attract" (a public JSON
    # API, either on the tenant's own vanity domain or reached via a JS
    # redirect off the bare .icims.com subdomain). See adapters/icims.py.
    ICIMS = "icims"
    # Avature — a multi-tenant ATS at {tenant}.avature.net; some tenants
    # front every path with an AWS WAF JS challenge a plain httpx request
    # can't pass (see adapters/avature.py's browser-render fallback).
    AVATURE = "avature"
    # Paradox ("Olivia" AI recruiting chatbot) — also hosts a plain
    # server-rendered career-site CMS white-labeled onto each customer's
    # own domain (e.g. careers.marriott.com), no shared host to detect
    # statically.
    PARADOX = "paradox"
    # Gupy — a Brazilian multi-tenant ATS at {tenant}.gupy.io, jobs listed
    # server-side into a Next.js __NEXT_DATA__ JSON blob (no plain job-list
    # API found).
    GUPY = "gupy"
    # HireHive — a multi-tenant ATS at {tenant}.hirehive.com, jobs
    # server-rendered as plain anchors on the board root (no pagination or
    # API found; verified live: patagonia.hirehive.com).
    HIREHIVE = "hirehive"
    # PeopleAdmin — a multi-tenant ATS at {tenant}.peopleadmin.com, common
    # in higher-ed; jobs server-rendered at /postings/search?page=N,
    # paginating until a page returns none (verified live: San Jose
    # Evergreen Community College District).
    PEOPLEADMIN = "peopleadmin"
    # Pinpoint (Pinpoint HQ) — a career-site CMS white-labeled onto each
    # customer's own domain (e.g. careers.manutd.com), no shared host to
    # detect statically; publishes a public jobs.rss feed.
    PINPOINT = "pinpoint"
    # Zenats — a career-site CMS white-labeled onto each customer's own
    # domain (e.g. jobs.halan.com), jobs server-rendered as plain
    # /position/{slug}/ anchors on the board root.
    ZENATS = "zenats"
    # Talemetry (Cornerstone) — a career-site CMS white-labeled onto each
    # customer's own domain (e.g. careers.progressive.com), applies via
    # apply.talemetry.com. Some tenants front every path with a WAF that
    # blocks plain httpx entirely (verified live: Progressive returns 403
    # even with a real browser User-Agent) — browser-render fallback only,
    # same as Avature's delta.avature.net case.
    TALEMETRY = "talemetry"
    # LINE's career site (careers.linecorp.com) — a Gatsby/Strapi static
    # site with the whole job catalog inline in its own page-data.json, no
    # separate API (see adapters/linecorp.py).
    LINECORP = "linecorp"
    # Toss/Viva Republica's career site (toss.im) — Greenhouse-backed
    # (absolute_url carries gh_jid) but the public boards-api.greenhouse.io
    # candidate API is disabled for this tenant; Toss's own public
    # job-groups API mirrors the same Greenhouse job data instead (see
    # adapters/toss.py).
    TOSS = "toss"
    # Cornerstone OnDemand (CSOD) — a multi-tenant ATS at {corp}.csod.com,
    # distinct from Talemetry above (also a Cornerstone product, but
    # white-labeled onto each customer's own domain instead). One corp
    # tenant hosts several independent per-country/brand "career sites"
    # (see adapters/csod.py), each its own board.
    CSOD = "csod"
    # HiringRoom — a Latin American multi-tenant ATS at
    # {tenant}.hiringroom.com (see adapters/hiringroom.py).
    HIRINGROOM = "hiringroom"
    # Oracle Taleo — a multi-tenant ATS at {tenant}.taleo.net, one tenant
    # hosting several independent "career sections" (see adapters/taleo.py).
    TALEO = "taleo"
    # Rippling ATS — a multi-tenant ATS at ats.rippling.com/{slug}, jobs
    # server-rendered into a Next.js __NEXT_DATA__ blob, no public REST API
    # found (see adapters/rippling.py).
    RIPPLING = "rippling"
    # TalentReef — a career-site CMS white-labeled onto each customer's own
    # domain (e.g. jackintheboxjobs.com), backed by a shared public
    # Elasticsearch proxy with no per-tenant URL scoping (filtered by
    # brandId instead — see adapters/talentreef.py).
    TALENTREEF = "talentreef"
    # UltiPro/UKG Pro's Ignite recruiting portal — multi-tenant at
    # recruiting[2].ultipro.com/{tenant}/JobBoard/{boardId}/. Every job-
    # detail field renders inside a Web Component shadow root, so scanning
    # requires the browser-render service's pierce_shadow option (see
    # adapters/ultipro.py and app.services.browser_fetch).
    ULTIPRO = "ultipro"
    # Oracle SelectMinds — a multi-tenant employee-referral/careers platform
    # at {tenant}.referrals.selectminds.com. The root and most of the site
    # are SSO-gated, but individual job postings and /latest-jobs are
    # genuinely public (see adapters/selectminds.py).
    SELECTMINDS = "selectminds"
    # Sportsman's Warehouse (careers.sportsmans.com) — a custom career
    # module on their own SAP Hybris storefront, not any shared ATS
    # platform (see adapters/sportsmans_warehouse.py).
    SPORTSMANS_WAREHOUSE = "sportsmans_warehouse"
    # ApplicantPro (rebranded "isolved Talent Acquisition") — a multi-tenant
    # ATS at {tenant}.applicantpro.com, jobs server-rendered as plain
    # anchors on a listing endpoint, each detail page a full schema.org
    # JobPosting JSON-LD block (see adapters/applicantpro.py).
    APPLICANTPRO = "applicantpro"


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
