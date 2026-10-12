"""Reads a search-box query as a job level and role, for related-title search
— "principal engineer" should also find staff and senior engineers, "sr
devops" should find site reliability and platform engineers.

The posting side (each posting's level and role) is meant to come from the
sector model, which reads the whole posting (see yabot.jobs-ml's
title-level-role plan); this module is the query side, which the model never
sees. Queries are short and predictable, so a lexicon reads them reliably in
a way it can't read posting titles ("Enterprise Monitoring Specialist -
EHRA" says nothing about the role on its own).

Level and role IDs are the ML taxonomy's (sector_ml/config.py LEVELS/ROLES)
verbatim, so a query's level/role can be compared with a posting's directly.
Only technology roles and the roles next to them (product, design, project
management) are covered for now — any other query keeps today's literal
title match.

Nothing calls this from search yet. parse_level and matches_title are also
here as an interim, title-only baseline to measure the lexicon (and later
the model) against — see tests/services/title_search_cases.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.jobs import parse_search_query

# --- Levels -----------------------------------------------------------------

IC_LADDER = ["intern", "junior", "mid", "senior", "staff", "principal"]
MANAGEMENT = ["manager", "senior_manager", "director", "senior_director", "vp", "executive"]
LEVELS = [*IC_LADDER, *MANAGEMENT]

# Which posting levels a query's level also finds. The IC ladder goes down
# only (a principal search also shows staff/senior/mid jobs, never junior or
# intern); management matches within its band both ways ("director" and
# "senior director" find each other); the two tracks never mix.
LEVEL_BANDS: dict[str, tuple[str, ...]] = {
    "intern": ("intern",),
    "junior": ("junior",),
    "mid": ("mid",),
    "senior": ("senior", "mid"),
    "staff": ("staff", "senior", "mid"),
    "principal": ("principal", "staff", "senior", "mid"),
    "manager": ("manager", "senior_manager"),
    "senior_manager": ("manager", "senior_manager"),
    "director": ("director", "senior_director"),
    "senior_director": ("director", "senior_director"),
    "vp": ("vp",),
    "executive": ("executive",),
}

LEVEL_PHRASES: dict[str, str] = {
    # IC ladder
    "intern": "intern", "interns": "intern", "internship": "intern", "co op": "intern", "coop": "intern",
    "apprentice": "intern", "apprenticeship": "intern", "student": "intern",
    "junior": "junior", "jr": "junior", "entry level": "junior", "new grad": "junior", "new graduate": "junior",
    "graduate": "junior", "early career": "junior", "associate": "junior", "trainee": "junior",
    "mid": "mid", "mid level": "mid", "intermediate": "mid",
    "senior": "senior", "sr": "senior", "snr": "senior", "lead": "senior", "tech lead": "senior", "team lead": "senior",
    "staff": "staff", "senior staff": "staff", "senior lead": "staff", "sr lead": "staff",
    "principal": "principal", "senior principal": "principal", "distinguished": "principal", "fellow": "principal",
    "chief architect": "principal",
    # Management
    "manager": "manager", "supervisor": "manager", "assistant manager": "manager",
    "senior manager": "senior_manager", "sr manager": "senior_manager", "group manager": "senior_manager",
    "associate director": "senior_manager", "area manager": "senior_manager", "district manager": "senior_manager",
    "director": "director", "head of": "director",
    "senior director": "senior_director", "sr director": "senior_director", "executive director": "senior_director",
    "regional director": "senior_director",
    "vp": "vp", "vice president": "vp", "svp": "vp", "evp": "vp", "avp": "vp",
    "senior vice president": "vp", "executive vice president": "vp", "assistant vice president": "vp",
    "chief": "executive", "ceo": "executive", "cto": "executive", "cfo": "executive", "coo": "executive",
    "cio": "executive", "ciso": "executive", "cmo": "executive", "cpo": "executive", "president": "executive",
}

# Grade numerals ("Engineer II", "Analyst 3") — only read as a level after
# another word, so a query that is just "2" or a title starting "3D …" isn't.
NUMERAL_LEVELS: dict[str, str] = {
    "i": "junior", "1": "junior",
    "ii": "mid", "2": "mid",
    "iii": "senior", "3": "senior",
    "iv": "staff", "4": "staff",
}

# --- Roles ------------------------------------------------------------------

ROLE_PHRASES: dict[str, str] = {
    # software_general
    "software engineer": "software_general", "software engineering": "software_general",
    "software developer": "software_general", "software development engineer": "software_general",
    "software development": "software_general",
    "sde": "software_general", "swe": "software_general", "developer": "software_general",
    "programmer": "software_general", "application developer": "software_general",
    "applications developer": "software_general", "member of technical staff": "software_general",
    "mts": "software_general", "coder": "software_general", "forward deployed": "software_general",
    # backend
    "backend": "backend", "back end": "backend", "server side": "backend", "api engineer": "backend",
    "api developer": "backend", "java": "backend", "golang": "backend", "go developer": "backend",
    "dotnet": "backend", "csharp": "backend", "ruby": "backend", "rails": "backend", "nodejs": "backend",
    "node js": "backend", "node developer": "backend", "php": "backend", "python developer": "backend",
    "scala": "backend",
    # frontend
    "frontend": "frontend", "front end": "frontend", "ui engineer": "frontend", "ui developer": "frontend",
    "web developer": "frontend", "web engineer": "frontend", "react": "frontend", "reactjs": "frontend",
    "angular": "frontend", "vue": "frontend", "vuejs": "frontend", "javascript": "frontend",
    "typescript": "frontend",
    # fullstack
    "full stack": "fullstack", "fullstack": "fullstack",
    # mobile
    "mobile": "mobile", "ios": "mobile", "android": "mobile", "react native": "mobile", "flutter": "mobile",
    "swift": "mobile", "kotlin": "mobile",
    # devops_sre_platform
    "devops": "devops_sre_platform", "dev ops": "devops_sre_platform", "sre": "devops_sre_platform",
    "site reliability": "devops_sre_platform", "systems reliability": "devops_sre_platform",
    "platform engineer": "devops_sre_platform", "platform engineering": "devops_sre_platform",
    "infrastructure engineer": "devops_sre_platform", "infrastructure": "devops_sre_platform",
    "infra engineer": "devops_sre_platform", "cloud engineer": "devops_sre_platform",
    "cloud infrastructure": "devops_sre_platform", "kubernetes": "devops_sre_platform",
    "k8s": "devops_sre_platform", "build engineer": "devops_sre_platform",
    "release engineer": "devops_sre_platform", "observability": "devops_sre_platform",
    "production engineer": "devops_sre_platform", "mlops": "devops_sre_platform",
    # ml_ai_engineering
    "machine learning": "ml_ai_engineering", "ml": "ml_ai_engineering", "ai": "ml_ai_engineering",
    "artificial intelligence": "ml_ai_engineering", "deep learning": "ml_ai_engineering",
    "llm": "ml_ai_engineering", "genai": "ml_ai_engineering", "generative ai": "ml_ai_engineering",
    "nlp": "ml_ai_engineering", "natural language processing": "ml_ai_engineering",
    "computer vision": "ml_ai_engineering", "perception": "ml_ai_engineering",
    "applied scientist": "ml_ai_engineering",
    # data_engineering
    "data engineer": "data_engineering", "data engineering": "data_engineering",
    "analytics engineer": "data_engineering", "etl": "data_engineering", "data warehouse": "data_engineering",
    "big data": "data_engineering", "database engineer": "data_engineering",
    "database administrator": "data_engineering", "dba": "data_engineering", "data platform": "data_engineering",
    "database": "data_engineering", "data integration": "data_engineering", "data modeling": "data_engineering",
    # data_science_analytics
    "data scientist": "data_science_analytics", "data science": "data_science_analytics",
    "data analyst": "data_science_analytics", "data analytics": "data_science_analytics",
    "bi analyst": "data_science_analytics", "bi developer": "data_science_analytics",
    "business intelligence": "data_science_analytics", "analytics": "data_science_analytics",
    "statistician": "data_science_analytics", "power bi": "data_science_analytics",
    "tableau": "data_science_analytics", "data visualization": "data_science_analytics",
    # embedded_firmware
    "embedded": "embedded_firmware", "firmware": "embedded_firmware", "kernel": "embedded_firmware",
    "device driver": "embedded_firmware",
    # qa_test
    "qa": "qa_test", "quality assurance": "qa_test", "sdet": "qa_test", "test automation": "qa_test",
    "software tester": "qa_test", "software test": "qa_test",
    # security
    "security engineer": "security", "cybersecurity": "security", "cyber security": "security",
    "cyber": "security", "information security": "security", "infosec": "security", "appsec": "security",
    "application security": "security", "cloud security": "security", "penetration tester": "security",
    "penetration testing": "security", "pentester": "security", "pen tester": "security",
    "soc analyst": "security", "security analyst": "security", "security architect": "security",
    "devsecops": "security", "security operations": "security", "security engineering": "security",
    "security automation": "security", "security researcher": "security", "iam": "security",
    "identity and access": "security", "incident response": "security", "threat": "security",
    "vulnerability": "security", "grc": "security",
    # it_support_sysadmin
    "help desk": "it_support_sysadmin", "helpdesk": "it_support_sysadmin", "service desk": "it_support_sysadmin",
    "desktop support": "it_support_sysadmin", "it support": "it_support_sysadmin",
    "system administrator": "it_support_sysadmin", "systems administrator": "it_support_sysadmin",
    "sysadmin": "it_support_sysadmin", "network engineer": "it_support_sysadmin",
    "network administrator": "it_support_sysadmin", "it technician": "it_support_sysadmin",
    "it specialist": "it_support_sysadmin", "it": "it_support_sysadmin", "data center": "it_support_sysadmin",
    "network operations": "it_support_sysadmin", "network systems": "it_support_sysadmin",
    "network automation": "it_support_sysadmin", "network support": "it_support_sysadmin",
    # technical_support (customer_support in the taxonomy, but searched for by tech people)
    "support engineer": "technical_support", "application support": "technical_support",
    "product support": "technical_support", "technical support": "technical_support",
    "cloud support": "technical_support",
    # business_systems_analyst
    "business analyst": "business_systems_analyst", "systems analyst": "business_systems_analyst",
    "business systems analyst": "business_systems_analyst", "salesforce administrator": "business_systems_analyst",
    "salesforce admin": "business_systems_analyst", "erp analyst": "business_systems_analyst",
    "workday analyst": "business_systems_analyst", "epic analyst": "business_systems_analyst",
    "salesforce": "business_systems_analyst", "servicenow": "business_systems_analyst",
    "sap": "business_systems_analyst", "workday": "business_systems_analyst", "netsuite": "business_systems_analyst",
    # Next to tech: product, design, project management
    "product manager": "product_management", "product owner": "product_management",
    "product management": "product_management", "apm": "product_management",
    "ux": "ux_ui_design", "ui ux": "ux_ui_design", "ux ui": "ux_ui_design", "ux designer": "ux_ui_design",
    "ui designer": "ux_ui_design", "product designer": "ux_ui_design", "interaction designer": "ux_ui_design",
    "user experience": "ux_ui_design", "ux researcher": "ux_ui_design", "user researcher": "ux_ui_design",
    "ux writer": "ux_ui_design",
    "project manager": "project_program_management", "program manager": "project_program_management",
    "technical program manager": "project_program_management", "tpm": "project_program_management",
    "scrum master": "project_program_management", "project management": "project_program_management",
}

# Phrases that would otherwise be misread — a role that merely contains
# "manager" isn't the manager level, a security guard isn't cybersecurity, a
# front-end cashier isn't a frontend developer, a staff nurse isn't staff
# level. Kept whole and matched literally (like today's search), so they
# neither expand nor get split into misleading parts.
LITERAL_PHRASES = {
    "account manager", "case manager", "care manager", "relationship manager", "property manager",
    "security officer", "security guard", "security specialist",
    "front end cashier", "front end associate", "front end supervisor", "front end mechanic",
    "staff nurse", "staff accountant", "staff pharmacist", "staff rn", "staff attorney",
    "general manager",
    # Building/ship engineers, not executives or software
    "chief engineer", "assistant chief engineer", "operating engineer", "building engineer", "stationary engineer",
    # Split between software and hardware/presales (prod sample, 2026-10-12)
    # — for the model to settle; a level typed with them still applies.
    "systems engineer", "system engineer", "solutions engineer", "solution engineer", "sales engineer",
    "customer engineer", "product engineer", "automation engineer", "research engineer", "research scientist",
}

# Words that only mean something once the board's sector says which field
# the search is in: "analyst" on Tech means data/BI, anywhere else it's left
# as a literal word.
SECTOR_PHRASES: dict[str, dict[str, str]] = {
    "analyst": {"engineering_tech": "data_science_analytics"},
    "test engineer": {"engineering_tech": "qa_test"},
    "designer": {"engineering_tech": "ux_ui_design", "design_product": "ux_ui_design"},
    "architect": {"engineering_tech": "software_general"},
    "software": {"engineering_tech": "software_general"},  # "Engineer III, Software"
    "security": {"engineering_tech": "security"},
    "cloud": {"engineering_tech": "devops_sre_platform"},
    "network": {"engineering_tech": "it_support_sysadmin"},
}

# Which other roles a role also finds. Small on purpose, and editable without
# retraining anything. devops_sre_platform isn't linked to ml_ai_engineering
# — every ML engineer would be too broad for a devops search; MLOps postings
# already read as devops_sre_platform via "mlops".
ROLE_LINKS: dict[str, tuple[str, ...]] = {
    "software_general": ("backend", "frontend", "fullstack", "mobile"),
    "backend": ("fullstack", "software_general"),
    "frontend": ("fullstack", "software_general"),
    "fullstack": ("backend", "frontend", "software_general"),
}

# Generic job nouns that add nothing once a role is known ("devops engineer"
# is just devops) — dropped from what still has to appear literally, so
# "senior backend engineer" also finds "Senior Backend Developer".
_GENERIC_NOUNS = {"engineer", "engineers", "developer", "developers", "dev", "programmer", "specialist"}
# Role phrases that are just a generic job noun: they only name a role when
# nothing more specific did — "ios developer" is mobile, not mobile plus
# general software (which would pull in every backend job).
_GENERIC_ROLE_PHRASES = {("developer",), ("programmer",), ("coder",)}
_STOPWORDS = {"of", "and", "the", "for", "a", "an", "in", "at", "to", "with", "or"}

# --- Tokenizing and phrase matching ------------------------------------------

_SPECIAL_TOKENS = [
    (re.compile(r"c\+\+"), " cpp "),
    (re.compile(r"c#"), " csharp "),
    (re.compile(r"\.net\b"), " dotnet "),
    (re.compile(r"\bnode\.js\b"), " nodejs "),
    (re.compile(r"\bco-op\b"), " co op "),
]


# Abbreviations seen in real titles ("Software Engr II", "Systems Engineer
# Stf", "Sr Software Eng Supervisor").
_TOKEN_ALIASES = {
    "engr": "engineer", "eng": "engineer", "mgr": "manager", "dir": "director", "assoc": "associate",
    "stf": "staff", "prin": "principal",
}


def tokenize(text: str) -> list[str]:
    """Lowercase words with punctuation dropped: "Sr. Full-Stack Engineer (II)"
    -> ["sr", "full", "stack", "engineer", "ii"]."""
    text = text.lower()
    for pattern, replacement in _SPECIAL_TOKENS:
        text = pattern.sub(replacement, text)
    return [_TOKEN_ALIASES.get(token, token) for token in re.findall(r"[a-z0-9]+", text)]


_LEVEL = "level"
_ROLE = "role"
_LITERAL = "literal"
_SECTOR = "sector"

_PHRASES: dict[tuple[str, ...], tuple[str, str | dict[str, str]]] = {}
for _phrase, _level in LEVEL_PHRASES.items():
    _PHRASES[tuple(_phrase.split())] = (_LEVEL, _level)
for _phrase, _role in ROLE_PHRASES.items():
    _PHRASES[tuple(_phrase.split())] = (_ROLE, _role)
for _phrase, _by_sector in SECTOR_PHRASES.items():
    _PHRASES[tuple(_phrase.split())] = (_SECTOR, _by_sector)
for _phrase in LITERAL_PHRASES:
    _PHRASES[tuple(_phrase.split())] = (_LITERAL, _phrase)
_MAX_PHRASE_WORDS = max(len(p) for p in _PHRASES)


@dataclass(frozen=True)
class _Reading:
    levels: list[tuple[str, bool]]  # (level, came from a numeral), in order
    roles: list[str]
    residual: list[str]  # words/phrases that must still appear literally


def _read(tokens: list[str], sector: str | None) -> _Reading:
    """Longest phrase first, left to right: "senior director" is one level,
    not senior + director; "product manager" is a role, not the manager
    level."""
    levels: list[tuple[str, bool]] = []
    roles: list[str] = []
    generic_roles: list[str] = []
    residual: list[str] = []
    i = 0
    while i < len(tokens):
        for size in range(min(_MAX_PHRASE_WORDS, len(tokens) - i), 0, -1):
            entry = _PHRASES.get(tuple(tokens[i : i + size]))
            if entry is None:
                continue
            kind, value = entry
            if kind == _LEVEL:
                levels.append((value, False))
            elif kind == _ROLE and tuple(tokens[i : i + size]) in _GENERIC_ROLE_PHRASES:
                generic_roles.append(value)
            elif kind == _ROLE:
                roles.append(value)
            elif kind == _SECTOR and sector in value:
                roles.append(value[sector])
            else:  # a literal phrase, or a sector word outside its sector
                residual.append(" ".join(tokens[i : i + size]))
            i += size
            break
        else:
            token = tokens[i]
            if i > 0 and token in NUMERAL_LEVELS:
                levels.append((NUMERAL_LEVELS[token], True))
            elif token not in _STOPWORDS:
                residual.append(token)
            i += 1
    roles = roles or generic_roles
    if roles:
        residual = [word for word in residual if word not in _GENERIC_NOUNS]
    return _Reading(levels, roles, residual)


def _pick_level(levels: list[tuple[str, bool]]) -> str | None:
    """One level from everything a text named: a management level over an IC
    one ("Lead, Engineering Manager" is a manager), and a word over a grade
    numeral ("Senior Software Engineer II" is senior)."""
    if not levels:
        return None
    for level, _ in levels:
        if level in MANAGEMENT:
            return level
    for level, numeral in levels:
        if not numeral:
            return level
    return levels[0][0]


# --- Queries ----------------------------------------------------------------


@dataclass(frozen=True)
class QueryIntent:
    phrase: str | None  # today's literal include text — always still matched as a fallback
    exclude: tuple[str, ...]  # "-word" exclusions, unchanged
    exact: bool  # a "quoted" query: literal match only, nothing expanded
    level: str | None = None
    levels: tuple[str, ...] = ()  # LEVEL_BANDS[level]; empty = any level
    roles: tuple[str, ...] = ()  # roles the query named
    linked_roles: tuple[str, ...] = ()  # ROLE_LINKS of those, minus the named ones
    residual: tuple[str, ...] = ()  # words the lexicon didn't use; must appear in the title

    @property
    def expands(self) -> bool:
        """Whether this query finds anything beyond today's literal match."""
        return not self.exact and (self.level is not None or bool(self.roles))


def interpret_query(q: str, sector: str | None = None) -> QueryIntent:
    """What a search-box query asks for. `sector` is the board's sector
    filter, if any — it settles words like "analyst" that mean different
    roles in different fields."""
    stripped = q.strip()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        phrase, exclude = parse_search_query(stripped[1:-1])
        return QueryIntent(phrase=phrase, exclude=tuple(exclude), exact=True)

    phrase, exclude = parse_search_query(stripped)
    if phrase is None:
        return QueryIntent(phrase=None, exclude=tuple(exclude), exact=False)

    reading = _read(tokenize(phrase), sector)
    level = _pick_level(reading.levels)
    roles = tuple(dict.fromkeys(reading.roles))
    linked = tuple(dict.fromkeys(r for role in roles for r in ROLE_LINKS.get(role, ()) if r not in roles))
    return QueryIntent(
        phrase=phrase,
        exclude=tuple(exclude),
        exact=False,
        level=level,
        levels=LEVEL_BANDS[level] if level else (),
        roles=roles,
        linked_roles=linked,
        residual=tuple(reading.residual),
    )


# --- Interim title-only baseline ---------------------------------------------
# Not the posting-side design (that's the model, reading the whole posting):
# these read only the title, to measure the lexicon now and the model later
# against the same test queries.


def parse_level(title: str) -> str | None:
    """The level a title states, or None when it states none (a plain
    "Software Engineer" — the taxonomy's mid, the default working level)."""
    return _pick_level(_read(tokenize(title), None).levels)


def title_roles(title: str, sector: str | None = None) -> set[str]:
    return set(_read(tokenize(title), sector).roles)


def _contains_words(title_tokens: list[str], phrase: str) -> bool:
    words = phrase.split()
    n = len(words)
    for i in range(len(title_tokens) - n + 1):
        window = title_tokens[i : i + n]
        if all(t == w or t == w + "s" for t, w in zip(window, words)):
            return True
    return False


def matches_title(intent: QueryIntent, title: str, sector: str | None = None) -> bool:
    """Whether a posting with this title would be found, judging its level
    and role from the title alone."""
    lowered = title.lower()
    if any(term.lower() in lowered for term in intent.exclude):
        return False
    if intent.phrase is None:
        return True  # exclusions only
    if intent.phrase.lower() in lowered:
        return True  # today's literal match is never lost
    if not intent.expands:
        return False
    tokens = tokenize(title)
    if intent.levels and (parse_level(title) or "mid") not in intent.levels:
        return False
    if intent.roles and not title_roles(title, sector) & {*intent.roles, *intent.linked_roles}:
        return False
    return all(_contains_words(tokens, word) for word in intent.residual)
