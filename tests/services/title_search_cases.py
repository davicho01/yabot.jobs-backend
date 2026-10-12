"""The test queries for related-title search: what each query should be read
as (app.services.title_search.interpret_query), and example titles it should
and shouldn't find.

The same cases are meant to measure every version of the posting side — today
the title-only baseline (matches_title), later the model's level/role labels —
so a case says what *should* happen, not what the baseline happens to do.
Titles that only the whole posting can settle go in `needs_model`: the
baseline isn't expected to find them, and they're reported as the gap the
model has to close rather than counted as failures.

`sector` is the board's sector filter for the query (Tech by default on the
board); example titles are assumed to be in that sector.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TECH = "engineering_tech"


@dataclass(frozen=True)
class Case:
    query: str
    level: str | None = None
    roles: tuple[str, ...] = ()
    residual: tuple[str, ...] = ()
    sector: str | None = TECH
    exact: bool = False
    should_match: tuple[str, ...] = ()
    should_not_match: tuple[str, ...] = ()
    needs_model: tuple[str, ...] = field(default=())


CASES: list[Case] = [
    # --- The IC ladder: down only, never junior/intern -----------------------
    Case(
        "principal engineer",
        level="principal",
        residual=("engineer",),
        should_match=(
            "Principal Software Engineer",
            "Staff Engineer",
            "Senior Engineer, Payments",
            "Software Engineer II",
            "Principal Engineer",
        ),
        should_not_match=(
            "Junior Software Engineer",
            "Software Engineering Intern",
            "Engineering Manager",
            "Principal Product Manager",
        ),
    ),
    Case(
        "staff software engineer",
        level="staff",
        roles=("software_general",),
        should_match=(
            "Staff Software Engineer",
            "Senior Backend Engineer",
            "Sr. Full-Stack Developer",
            "Software Engineer III",
            "iOS Engineer",
            "Member of Technical Staff",
        ),
        should_not_match=("Principal Software Engineer", "Junior Developer", "Staff Data Scientist"),
        needs_model=("Staff Engineer",),
    ),
    Case(
        "senior backend engineer",
        level="senior",
        roles=("backend",),
        should_match=(
            "Senior Backend Engineer",
            "Sr. Back End Developer",
            "Senior Java Developer",
            "Senior Full Stack Engineer",
            "Backend Engineer",
            "Senior Software Engineer",
        ),
        should_not_match=("Staff Backend Engineer", "Senior Frontend Engineer", "Junior Backend Engineer"),
        needs_model=("Senior Engineer, Payments API",),
    ),
    Case(
        "jr developer",
        level="junior",
        roles=("software_general",),
        should_match=("Junior Developer", "Associate Software Engineer", "Software Engineer I", "Entry Level Web Developer"),
        should_not_match=("Senior Developer", "Software Engineer II", "Software Engineering Intern"),
    ),
    Case(
        "software engineer ii",
        level="mid",
        roles=("software_general",),
        # "Software Engineer III" is found too, and isn't listed either way:
        # "software engineer ii" is a substring of it, and the literal match
        # (today's search) is always kept.
        should_match=("Software Engineer II", "Software Engineer", "Backend Developer", "Software Developer 2"),
        should_not_match=("Senior Software Engineer", "Software Engineer I", "Software Engineer IV"),
    ),
    Case(
        "software engineering intern",
        level="intern",
        roles=("software_general",),
        should_match=("Software Engineer Intern", "Summer 2027 Internship - Software Development", "Frontend Co-op"),
        should_not_match=("Junior Software Engineer", "Software Engineer"),
    ),
    # --- Synonyms -------------------------------------------------------------
    Case(
        "sr devops",
        level="senior",
        roles=("devops_sre_platform",),
        should_match=(
            "Senior DevOps Engineer",
            "Senior Site Reliability Engineer",
            "Sr. Platform Engineer",
            "Senior MLOps Engineer",
            "Senior Cloud Engineer",
            "Senior Infrastructure Engineer",
            "DevOps Engineer",
        ),
        should_not_match=("Junior DevOps Engineer", "Staff SRE", "Senior Software Engineer", "Senior Data Engineer"),
        needs_model=("Enterprise Monitoring Specialist - EHRA",),
    ),
    Case(
        "sre",
        roles=("devops_sre_platform",),
        should_match=("Site Reliability Engineer", "Staff SRE", "Platform Engineer", "DevOps Engineer II"),
        should_not_match=("Software Engineer", "Security Engineer"),
    ),
    Case(
        "full stack",
        roles=("fullstack",),
        should_match=("Full Stack Engineer", "Fullstack Developer", "Full-Stack Software Engineer", "Backend Engineer", "Frontend Developer"),
        should_not_match=("Data Engineer", "DevOps Engineer"),
    ),
    Case(
        "swe",
        roles=("software_general",),
        should_match=("Software Engineer", "Senior Software Developer", "Backend Engineer", "Android Developer"),
        should_not_match=("Data Scientist", "Site Reliability Engineer", "QA Engineer"),
    ),
    Case(
        "ios developer",
        roles=("mobile",),
        should_match=("iOS Engineer", "Senior iOS Developer", "Mobile Engineer", "Android Developer"),
        should_not_match=("Frontend Developer", "Backend Engineer"),
    ),
    Case(
        "machine learning engineer",
        roles=("ml_ai_engineering",),
        should_match=("ML Engineer", "Senior Machine Learning Engineer", "AI Engineer", "Applied Scientist, LLM"),
        should_not_match=("Data Engineer", "Data Analyst"),
    ),
    Case(
        "data engineer",
        roles=("data_engineering",),
        should_match=("Data Engineer", "Senior Analytics Engineer", "ETL Developer", "Big Data Engineer"),
        should_not_match=("Data Scientist", "Data Analyst", "Software Engineer"),
    ),
    Case(
        "analyst",
        roles=("data_science_analytics",),
        should_match=("Data Analyst", "BI Analyst", "Senior Business Intelligence Analyst"),
        should_not_match=("Software Engineer",),
    ),
    Case(
        "security engineer",
        roles=("security",),
        should_match=("Security Engineer", "Senior Application Security Engineer", "Cybersecurity Analyst", "Penetration Tester"),
        should_not_match=("Security Officer", "Software Engineer"),
    ),
    # --- Management: both ways within a band ----------------------------------
    Case(
        "director of engineering",
        level="director",
        residual=("engineering",),
        should_match=("Director of Engineering", "Senior Director, Engineering", "Head of Engineering", "Engineering Director"),
        should_not_match=("Engineering Manager", "VP of Engineering", "Director of Marketing"),
    ),
    Case(
        "sr director engineering",
        level="senior_director",
        residual=("engineering",),
        should_match=("Director of Engineering", "Senior Director of Engineering"),
        should_not_match=("Senior Engineering Manager", "VP Engineering"),
    ),
    Case(
        "engineering manager",
        level="manager",
        residual=("engineering",),
        should_match=("Engineering Manager", "Senior Engineering Manager", "Manager, Software Engineering"),
        should_not_match=("Store Manager", "Senior Software Engineer", "Director of Engineering", "Engineering Product Manager"),
    ),
    Case(
        "vp engineering",
        level="vp",
        residual=("engineering",),
        should_match=("VP of Engineering", "Vice President, Engineering", "SVP Engineering"),
        should_not_match=("Director of Engineering", "CTO"),
    ),
    # --- Traps ------------------------------------------------------------------
    Case(
        "product manager",
        roles=("product_management",),
        should_match=("Product Manager", "Senior Product Manager", "Product Owner"),
        should_not_match=("Engineering Manager", "Store Manager"),
    ),
    Case(
        "senior product manager",
        level="senior",
        roles=("product_management",),
        should_match=("Senior Product Manager", "Product Manager", "Sr. Product Owner"),
        should_not_match=("Senior Manager, Product", "Principal Product Manager", "Associate Product Manager"),
    ),
    Case(
        "security",
        sector=None,
        residual=("security",),
        should_match=("Security Officer", "Security Engineer"),
        should_not_match=("Penetration Tester",),
    ),
    Case(
        "security",
        roles=("security",),
        # A "Security Officer" title is still found by the literal match (as
        # today) — it's the board's Tech filter that keeps guards out.
        should_match=("Security Operations Engineer", "Senior Security IAM Engineer", "Cybersecurity Analyst"),
        should_not_match=("Software Engineer", "Site Reliability Engineer"),
    ),
    # --- From the prod sample (2026-10-12) --------------------------------------
    Case(
        "senior systems engineer",
        level="senior",
        residual=("systems engineer",),
        should_match=("Systems Engineer II", "Sr. Systems Engineer", "Systems Engineer Sr - E3"),
        should_not_match=("Systems Engineer Stf - E4", "Senior Software Engineer"),
    ),
    Case(
        "forward deployed engineer",
        roles=("software_general",),
        should_match=("Forward Deployed Engineer", "Senior Forward Deployed Engineer (FDE) - Remote"),
        should_not_match=("Solutions Engineer",),
    ),
    Case(
        "it engineer",
        roles=("it_support_sysadmin",),
        should_match=("IT Engineer", "Senior IT Systems Engineer", "Data Center Technician II", "Network Operations Engineer"),
        should_not_match=("Software Engineer", "Chief Engineer"),
    ),
    Case(
        "software engineer iii",
        level="senior",
        roles=("software_general",),
        should_match=("Engineer III, Software", "Software Engr II", "Advanced Software Engr"),
        should_not_match=("Software Engineer I", "Systems Engineer III"),
    ),
    Case(
        "chief engineer",
        residual=("chief engineer",),
        should_match=("Chief Engineer", "Assistant Chief Engineer"),
        should_not_match=("CTO", "Chief Technology Officer"),
    ),
    Case(
        "front end",
        roles=("frontend",),
        should_match=("Front End Developer", "Frontend Engineer", "React Developer", "Full Stack Engineer"),
        should_not_match=("Back End Developer",),
    ),
    Case(
        "front end cashier",
        sector=None,
        residual=("front end cashier",),
        should_match=("Front End Cashier",),
        should_not_match=("Front End Developer", "Cashier"),
    ),
    Case(
        "account manager",
        residual=("account manager",),
        should_match=("Account Manager", "Senior Account Manager"),
        should_not_match=("Engineering Manager", "Store Manager"),
    ),
    Case(
        "nurse",
        sector="healthcare",
        residual=("nurse",),
        should_match=("Registered Nurse", "Charge Nurse"),
        should_not_match=("Physician",),
    ),
    Case(
        "devops -senior",
        roles=("devops_sre_platform",),
        should_match=("DevOps Engineer", "Site Reliability Engineer"),
        should_not_match=("Senior DevOps Engineer",),
    ),
    Case(
        '"Software Engineer"',
        exact=True,
        should_match=("Software Engineer", "Senior Software Engineer"),
        should_not_match=("Software Developer", "Backend Engineer"),
    ),
    Case(
        "c++ developer",
        roles=("software_general",),
        residual=("cpp",),
        should_match=("C++ Developer", "Senior C++ Software Engineer"),
        should_not_match=("Java Developer",),
    ),
]
