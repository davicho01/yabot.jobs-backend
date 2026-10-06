"""The reviewed source-name list the c4f1a9e7d2b6 migration applies to prod."""

import json
import uuid
from pathlib import Path

from app.services.company_names import AUTO, MANUAL
from app.services.job_dedup import brand_from_source_name

DATA = Path(__file__).resolve().parents[1] / "migrations" / "data" / "source_names_2026_10_06.json"


def test_every_entry_is_one_source_with_a_usable_name_or_sub_brands():
    entries = json.loads(DATA.read_text(encoding="utf-8"))

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    for e in entries:
        uuid.UUID(e["id"])
        assert e["old"]
        assert "new" in e or e.get("sub_brands")
        if "new" in e:
            assert e["name_source"] in (AUTO, MANUAL)
            assert e["new"] != e["old"] and len(e["new"]) <= 255
            assert brand_from_source_name(e["new"]) is not None, e  # never a slug or hostname
        for brand in e.get("sub_brands", []):
            assert brand == " ".join(brand.split()) and brand
