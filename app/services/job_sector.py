"""Job function/department (sector) inferred from a posting's own text.

Unlike workplace_type (read off a posting's location entries, see
app.services.workplace) this can't be derived from anything company-level:
the same company can post HR, Finance, and Engineering roles at once, so
each posting has to be classified from what it itself says it's hiring for.

Keyword-only, no LLM call — job titles are standardized enough that a
keyword match against the title, falling back to the description when the
title doesn't say, classifies the overwhelming majority for free. It won't
be perfect (a title like "Growth Lead" with no other signal falls through to
UNKNOWN) but that's an acceptable trade for running on every scan at zero
marginal cost; false UNKNOWNs can be revisited later, false positives are
what actually erode a sector search page's trust and are avoided by keeping
each sector's keyword list to terms that are genuinely diagnostic of it.
"""

import re

from app.models.enums import JobSector

# Checked in this order — first match wins. Function-specific sectors are
# checked before EXECUTIVE so e.g. "VP of Engineering" lands in
# engineering_tech, not executive; EXECUTIVE's own keywords are kept to
# titles that don't already name a function (C-suite abbreviations,
# "President", "Executive Director").
_SECTOR_KEYWORDS: list[tuple[JobSector, tuple[str, ...]]] = [
    (
        JobSector.ENGINEERING_TECH,
        (
            "engineer", "engineering", "developer", "programmer", "software",
            "devops", "sre", "site reliability", "data scientist", "data engineer",
            "data analyst", "machine learning", "ai researcher", "qa engineer",
            "quality assurance engineer", "test engineer", "systems administrator",
            "sysadmin", "network engineer", "security engineer", "cybersecurity",
            "solutions architect", "software architect", "full stack", "full-stack",
            "backend", "back-end", "frontend", "front-end", "ios developer",
            "android developer", "mobile developer", "cloud engineer",
            "database administrator", "dba", "it support", "it technician",
            "information technology", "web developer", "firmware engineer",
            "embedded engineer", "computer vision",
        ),
    ),
    (
        JobSector.ENGINEERING_TRADITIONAL,
        (
            "civil engineer", "civil engineering", "mechanical engineer",
            "mechanical engineering", "electrical engineer", "electrical engineering",
            "aerospace engineer", "chemical engineer", "structural engineer",
            "industrial engineer", "environmental engineer", "process engineer",
        ),
    ),
    (
        JobSector.SALES,
        (
            "sales representative", "sales manager", "sales executive",
            "sales director", "account executive",
            "account manager", "business development", "bdr", "sdr",
            "territory manager", "inside sales", "outside sales",
            "sales engineer", "channel partner manager", "salesperson",
        ),
    ),
    (
        JobSector.MARKETING,
        (
            "marketing", "seo specialist", "content strategist", "content marketer",
            "social media", "brand manager", "growth marketer", "demand generation",
            "communications specialist", "pr manager", "public relations",
            "copywriter", "digital marketing", "marketing analyst",
        ),
    ),
    (
        JobSector.FINANCE_ACCOUNTING,
        (
            "accountant", "accounting", "finance manager", "financial analyst",
            "financial planning", "controller", "bookkeeper", "payroll",
            "auditor", "audit associate", "treasury", "tax analyst",
            "tax accountant", "credit analyst", "fp&a",
        ),
    ),
    (
        JobSector.HR,
        (
            "human resources", "recruiter", "recruiting", "talent acquisition",
            "people operations", "hr business partner", "hr generalist",
            "hr manager", "hr coordinator", "benefits specialist",
            "compensation analyst", "talent partner",
        ),
    ),
    (
        JobSector.OPERATIONS_MANUFACTURING,
        (
            "manufacturing", "production associate", "production supervisor",
            "warehouse", "logistics", "supply chain", "operations manager",
            "operations associate", "machine operator", "assembly line",
            "assembler", "plant manager", "quality control", "forklift",
            "maintenance technician", "industrial", "fulfillment",
            "distribution center",
        ),
    ),
    (
        JobSector.CUSTOMER_SUPPORT,
        (
            "customer support", "customer service", "support specialist",
            "help desk", "technical support", "customer success",
            "client services", "call center",
        ),
    ),
    (
        JobSector.LEGAL,
        (
            "attorney", "lawyer", "legal counsel", "paralegal",
            "compliance officer", "compliance analyst", "legal assistant",
            "corporate counsel", "contracts manager",
        ),
    ),
    (
        JobSector.HEALTHCARE,
        (
            "registered nurse", "physician", "medical assistant", "healthcare",
            "clinical", "pharmacist", "therapist", "dental hygienist", "dentist",
            "caregiver", "patient care", "nurse practitioner", "medical technician",
            "phlebotomist", "radiology",
        ),
    ),
    (
        JobSector.DESIGN_PRODUCT,
        (
            "product manager", "product owner", "ux designer", "ui designer",
            "graphic designer", "product design", "user experience",
            "user researcher", "visual designer", "product marketing manager",
        ),
    ),
    (
        JobSector.EXECUTIVE,
        (
            "chief executive officer", "chief operating officer",
            "chief financial officer", "chief technology officer",
            "chief marketing officer", "chief people officer", "chief of staff",
            "president", "executive director", " ceo ", " coo ", " cfo ", " cto ",
        ),
    ),
    (
        JobSector.ADMINISTRATIVE_OFFICE,
        (
            "receptionist", "office assistant", "administrative assistant",
            "office manager", "data entry clerk", "data entry",
            "executive assistant", "administrative coordinator",
            "office coordinator", "administrative support",
        ),
    ),
    (
        JobSector.SERVICE_TRADES,
        (
            # Delivery/transport.
            "delivery driver", "truck driver", "route driver", "cdl driver",
            "courier", "delivery associate",
            # Food service.
            "pizza cook", "line cook", "prep cook", "head cook", "food service",
            "food server", "wait staff", "waiter", "waitress", "bartender",
            "barista", "dishwasher", "kitchen staff", "fast food", "restaurant",
            "chef", "cook",
            # Retail.
            "sales associate", "retail associate", "store associate",
            "stock associate", "cashier",
            # Hospitality / hotel.
            "hotel", "housekeeping", "housekeeper", "concierge",
            # Skilled trades / building services.
            "electrician", "plumber", "hvac technician", "hvac", "carpenter",
            "welder", "construction worker", "general contractor", "handyman",
            "landscaper", "groundskeeper", "janitor", "custodian",
            "security guard", "loss prevention", "driver",
        ),
    ),
]

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    # Padded with spaces so a bare keyword regex (e.g. " ceo ") can match at
    # the very start/end of the text too, without a separate word-boundary
    # pattern per keyword.
    return " " + _WHITESPACE_RE.sub(" ", text.lower()) + " "


def _match(text: str) -> JobSector | None:
    # Multi-word keywords ("sales engineer") are more specific than
    # single-word ones ("engineer") and are checked first, across every
    # sector, so a compound title isn't swallowed by a broader single-word
    # keyword from an earlier sector in the list — otherwise
    # engineering_tech's bare "engineer" would catch "Sales Engineer" before
    # sales's own, more specific, entry was ever reached.
    for multi_word in (True, False):
        for sector, keywords in _SECTOR_KEYWORDS:
            for keyword in keywords:
                if (" " in keyword.strip()) == multi_word and keyword in text:
                    return sector
    return None


def classify_sector(title: str | None, description: str | None = None) -> JobSector:
    """The job function this posting is hiring for, from its title first
    (short and reliable) and falling back to its description when the title
    alone doesn't say. UNKNOWN when neither does."""
    if title:
        match = _match(_normalize(title))
        if match is not None:
            return match
    if description:
        match = _match(_normalize(description))
        if match is not None:
            return match
    return JobSector.UNKNOWN
