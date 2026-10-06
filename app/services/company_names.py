"""Which company a job belongs to, and the name it shows.

The official company site is the authority on who owns a job: a crawl source
is a company's own careers site, and CrawlSource.name is that company's final
name. Every job from the source shows it, except a job whose page names one
of the source's configured sub-brands (TJX's site: "Marshalls of MA" ->
"Marshalls"), which shows that sub-brand. The page's own company name
(JobPosting.page_company_name) is kept only for that match and for sources
whose name isn't confirmed yet.

A source's name is "placeholder" until confirmed: register_discovered_board
names a new board after its slug or hostname. The first scan whose page names
the company in a usable form (promotable_name) replaces the placeholder,
once; until then its jobs get the cleaned page name (today's rules). An
admin edit marks the name "manual", and nothing automatic changes it after.

All of this applies to jobs scanned from now on, and a name change of either
kind applies to jobs scanned after it: existing jobs keep the name they were
scanned with until their next rescan. The only exception is a small,
deliberate validation run over the last few days' jobs
(one_off/backfill_recent_company_names.py).

Jobs with no crawl source, or from a source that isn't a company's own site
(a job board), keep the cleaned page name.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.services.job_dedup import (
    brand_from_source_name,
    clean_company_name,
    has_entity_code,
    prefers_source_brand,
)

logger = logging.getLogger("app.company_names")

PLACEHOLDER = "placeholder"
AUTO = "auto"
MANUAL = "manual"

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _words(text: str | None) -> str:
    """Lowercase alphanumeric words joined by single spaces ("Sam's Club" ->
    "sam s club"), for whole-word matching."""
    return " ".join(_NON_WORD_RE.sub(" ", (text or "").lower()).split())


def matching_sub_brand(sub_brands: list[str] | None, page_name: str | None) -> str | None:
    """The first configured sub-brand the page's company name contains as
    whole words ("Marshalls of MA" contains "Marshalls"; "Galaxy Foods"
    doesn't contain "Golf Galaxy"), or None."""
    page = f" {_words(page_name)} "
    for brand in sub_brands or []:
        words = _words(brand)
        if words and f" {words} " in page:
            return brand
    return None


def cleaned_page_company(page_name: str | None, source_name: str | None = None) -> str | None:
    """The name for a job whose source can't vouch for one (a placeholder
    name, no source, a job board): the page's name cleaned of careers wording
    and hostnames, with a legal entity or portal name ("B10 Wells Fargo Bank,
    N. A.", "Candidate Experience site") giving way to the source's brand
    when it has one, and the raw name as a last resort so nothing is lost."""
    brand = brand_from_source_name(source_name)
    cleaned = clean_company_name(page_name)
    if brand and (prefers_source_brand(page_name) or has_entity_code(cleaned)):
        return brand
    return cleaned or brand or page_name or source_name


def company_for(source: CrawlSource | None, page_name: str | None) -> str | None:
    """The company name a job shows, from its crawl source and its page."""
    if source is None or not source.is_official:
        return cleaned_page_company(page_name)
    # A confirmed name only wins when it's usable as a brand: one still
    # shaped like a board slug or hostname ("greenhouse/clearstreet") is
    # treated like a placeholder until it's curated, and a trailing "(…)"
    # admin note never shows ("CenterWell (Humana primary care)" ->
    # "CenterWell").
    brand = brand_from_source_name(source.name) if source.name_source != PLACEHOLDER else None
    if brand:
        return matching_sub_brand(source.sub_brands, page_name) or brand
    return cleaned_page_company(page_name, source.name)


def promotable_name(page_name: str | None) -> str | None:
    """A page's company name good enough to become a placeholder source's
    official name: cleaned, and not a hostname, a legal entity with a code, or
    a careers portal's own name — or None."""
    if not page_name or prefers_source_brand(page_name) or has_entity_code(page_name):
        return None
    return clean_company_name(page_name)


def promote_placeholder(db: Session, source: CrawlSource, page_name: str | None) -> bool:
    """Replace a placeholder source name with the company the page names,
    once. The update only applies while the name is still a placeholder, so
    concurrent scan lanes for the same source can't race each other into
    overwriting it. Returns whether this call promoted it. Like any name
    change, it applies to jobs scanned from now on — earlier jobs keep their
    name until rescanned."""
    name = promotable_name(page_name)
    if source.name_source != PLACEHOLDER or not name:
        return False
    result = db.execute(
        update(CrawlSource)
        .where(CrawlSource.id == source.id, CrawlSource.name_source == PLACEHOLDER)
        .values(name=name[:255], name_source=AUTO)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        db.refresh(source)
        return False
    db.refresh(source)
    logger.info("Named crawl source %s %r from its job pages.", source.id, source.name)
    return True
