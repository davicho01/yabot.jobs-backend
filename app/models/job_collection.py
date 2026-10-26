from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CollectionStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class JobCollection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A curated set of jobs — "Software Engineer jobs in Raleigh, NC",
    "Remote Healthcare jobs" — defined by one value per dimension
    (app.services.collection_dimensions) and moved through its lifecycle
    only by app.services.job_collections, which records each move as a
    JobCollectionEvent. Nothing renders these yet: the page generator reads
    them once settings.job_collections_enabled is on (PR-08), and the page's
    URL is decided there, with the route-collision tests.

    Whether a collection is indexed follows from its status (is_indexable),
    so it isn't stored twice. Counts and stats are computed from the live
    jobs (PR-06), not stored.
    """

    __tablename__ = "job_collections"

    # The dimension values in a fixed order ("role=software-engineer;
    # location=raleigh-nc"): one collection per combination.
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    collection_type: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    # One slug per dimension, as app.services.collection_dimensions.get takes
    # it; null where the type doesn't use that dimension.
    category: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str | None] = mapped_column(String(80))
    salary: Mapped[str | None] = mapped_column(String(20))
    freshness: Mapped[str | None] = mapped_column(String(30))

    status: Mapped[str] = mapped_column(String(20), default=CollectionStatus.CANDIDATE, index=True, nullable=False)

    # Page copy; null until written (generated defaults come with rendering).
    title: Mapped[str | None] = mapped_column(String(200))
    h1: Mapped[str | None] = mapped_column(String(200))
    meta_description: Mapped[str | None] = mapped_column(String(320))

    # The latest qualification result (PR-07): the overall score, and its
    # components and reasons so a decision can be explained (spec §45).
    qualification_score: Mapped[float | None] = mapped_column(Float)
    qualification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Deleting a collection deletes its history (the FK cascades too): without
    # the cascade, the ORM would null collection_id on loaded events instead.
    events: Mapped[list["JobCollectionEvent"]] = relationship(
        back_populates="collection", order_by="JobCollectionEvent.created_at",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    @property
    def is_indexable(self) -> bool:
        return self.status in (CollectionStatus.INDEXABLE, CollectionStatus.PERFORMING, CollectionStatus.DECLINING)

    def __repr__(self) -> str:
        return f"<JobCollection key={self.key!r} status={self.status!r}>"


class JobCollectionEvent(UUIDPrimaryKeyMixin, Base):
    """One status change of a JobCollection: from what, to what, why and who
    or what decided it (spec §11, §45). Written only by
    app.services.job_collections; never updated."""

    __tablename__ = "job_collection_events"
    __table_args__ = (Index("ix_job_collection_events_collection_id_created_at", "collection_id", "created_at"),)

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_collections.id", ondelete="CASCADE"), nullable=False
    )
    previous_status: Mapped[str | None] = mapped_column(String(20))  # null: the collection was created
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The admin who made a manual change; null for automated ones.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # What the decision was based on (a score and its components, counts).
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Set here, not by the database: Postgres's now() is the transaction's
    # start, so two changes committed together would tie and lose their order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), nullable=False
    )

    collection: Mapped[JobCollection] = relationship(back_populates="events")
