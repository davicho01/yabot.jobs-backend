import uuid

import pytest
from fastapi import HTTPException, Response

from app.api.routes.jobs import _is_own_site, submit_job_url
from app.models.user import User
from app.schemas.job import JobUrlSubmit


@pytest.mark.parametrize(
    "url",
    [
        "https://yabot.jobs/jobs/c5245f5d-b13a-40ad-9965-17104daed117",
        "https://www.yabot.jobs/job/some-slug",
        "https://YABOT.JOBS/jobs/abc",
    ],
)
def test_submit_rejects_own_site_links(url):
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")
    # Rejected before any DB access, so no session is needed.
    with pytest.raises(HTTPException) as exc:
        submit_job_url(JobUrlSubmit(url=url), Response(), current_user=user, db=None)
    assert exc.value.status_code == 400
    assert "original job posting URL" in exc.value.detail


@pytest.mark.parametrize(
    "host",
    ["boards.greenhouse.io", "notyabot.jobs", "yabot.jobs.example.com", None],
)
def test_other_hosts_are_not_own_site(host):
    assert not _is_own_site(host)
