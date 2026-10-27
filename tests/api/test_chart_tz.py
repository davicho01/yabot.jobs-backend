from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api.deps import get_chart_tz


def test_known_timezone_is_returned():
    assert get_chart_tz("America/Denver") == ZoneInfo("America/Denver")


@pytest.mark.parametrize("bad", ["Not/AZone", "../etc/passwd", ""])
def test_unknown_timezone_is_rejected(bad):
    with pytest.raises(HTTPException) as exc:
        get_chart_tz(bad)
    assert exc.value.status_code == 422
