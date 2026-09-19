"""One module per supported ATS platform, each exporting a single ADAPTER
(see base.AtsAdapter). ADAPTERS below is the registry every platform gets
collected into — the only thing app.services.ats_adapters needs to build
its public detect/list functions on top of.
"""

from app.services.adapters import (
    adp,
    amazon,
    apple,
    ashby,
    attrax,
    avature,
    bamboohr,
    breezyhr,
    clearcompany,
    clinch,
    dynatrace,
    echo_jobs,
    eightfold,
    fullstack,
    gem,
    glidefast,
    google,
    greenhouse,
    icims,
    jazzhr,
    lever,
    motion_recruitment,
    nlx,
    oracle_fusion,
    paradox,
    paycor_recruiting,
    personio,
    phenom,
    recruitee,
    stripe,
    successfactors,
    talentbrew,
    workable,
    workday,
)
from app.services.adapters.base import AtsAdapter

ADAPTERS: list[AtsAdapter] = [
    greenhouse.ADAPTER,
    lever.ADAPTER,
    ashby.ADAPTER,
    gem.ADAPTER,
    bamboohr.ADAPTER,
    personio.ADAPTER,
    workday.ADAPTER,
    jazzhr.ADAPTER,
    recruitee.ADAPTER,
    breezyhr.ADAPTER,
    workable.ADAPTER,
    adp.ADAPTER,
    amazon.ADAPTER,
    google.ADAPTER,
    apple.ADAPTER,
    fullstack.ADAPTER,
    motion_recruitment.ADAPTER,
    oracle_fusion.ADAPTER,
    # Ahead of clinch: clinch's embedded_match fallback (any sitemap.xml
    # with /jobs/ paths) false-positives on iCIMS/Jibe tenants whose own
    # unrelated site sitemap happens to list /jobs/{id} detail pages
    # (verified live: careers.mheducation.com) — icims's stricter
    # jibecdn.com/.icims.com signature check should get first refusal.
    icims.ADAPTER,
    clinch.ADAPTER,
    clearcompany.ADAPTER,
    echo_jobs.ADAPTER,
    talentbrew.ADAPTER,
    eightfold.ADAPTER,
    nlx.ADAPTER,
    stripe.ADAPTER,
    dynatrace.ADAPTER,
    glidefast.ADAPTER,
    paycor_recruiting.ADAPTER,
    successfactors.ADAPTER,
    attrax.ADAPTER,
    phenom.ADAPTER,
    avature.ADAPTER,
    paradox.ADAPTER,
]
