"""Work type read from a posting's location entries (app.services.workplace)."""

import pytest

from app.models.enums import WorkplaceType
from app.services.workplace import infer_workplace_type, reconcile_workplace_type

REMOTE, HYBRID, ONSITE, UNKNOWN = WorkplaceType.REMOTE, WorkplaceType.HYBRID, WorkplaceType.ONSITE, WorkplaceType.UNKNOWN


@pytest.mark.parametrize(
    "entries, expected",
    [
        (["Remote - California"], REMOTE),  # remote, with only a state
        (["Remote - United States"], REMOTE),
        (["Remote", "Remote - Canada"], REMOTE),
        (["Arizona", "Remote"], REMOTE),  # a state plus a "Remote" tag
        (["Boston or Remote"], HYBRID),  # a real city with a remote option
        (["New York, NY", "Remote - US"], HYBRID),
        (["Remote-Friendly (Travel-Required)", "San Francisco, CA", "Seattle, WA"], HYBRID),
        (["Hybrid"], HYBRID),
        (["San Francisco- Hybrid, US"], HYBRID),
        (["New York, NY Office"], ONSITE),
        (["New York, NY HQ USA, United States of America"], ONSITE),
        (["Remote or Office"], HYBRID),
        # nothing stated
        (["San Francisco, CA"], None),
        (["Utah"], None),
        (["Home Office - Illinois"], None),  # "Home Office" isn't a work-from-home signal
        ([], None),
        # conflicting or too uncertain to say
        (["Arizona", "Remote", "Utah", "Hybrid"], None),
        (["New York, NY Office", "Remote - US"], None),
        (["Toronto, Canada", "Remote - Canada"], None),  # a place we don't recognize might be a city: not "remote"
    ],
)
def test_infer_workplace_type(entries, expected):
    assert infer_workplace_type(entries) == expected


@pytest.mark.parametrize(
    "current, derived, expected",
    [
        (UNKNOWN, REMOTE, REMOTE),  # fills in what was unknown
        (UNKNOWN, ONSITE, ONSITE),
        (ONSITE, REMOTE, REMOTE),  # the adapter's on-site default for a plain place is overridden
        (ONSITE, HYBRID, HYBRID),
        (REMOTE, HYBRID, REMOTE),  # an adapter's explicit remote/hybrid is kept
        (HYBRID, REMOTE, HYBRID),
        (HYBRID, ONSITE, HYBRID),
        (ONSITE, ONSITE, ONSITE),
        (ONSITE, None, ONSITE),  # nothing stated: keep what the adapter said
        (UNKNOWN, None, UNKNOWN),
    ],
)
def test_reconcile_workplace_type(current, derived, expected):
    assert reconcile_workplace_type(current, derived) == expected
