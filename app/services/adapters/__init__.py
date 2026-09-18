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
    bamboohr,
    breezyhr,
    clinch,
    eightfold,
    gem,
    google,
    greenhouse,
    jazzhr,
    lever,
    nlx,
    oracle_fusion,
    personio,
    recruitee,
    stripe,
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
    oracle_fusion.ADAPTER,
    clinch.ADAPTER,
    talentbrew.ADAPTER,
    eightfold.ADAPTER,
    nlx.ADAPTER,
    stripe.ADAPTER,
]
