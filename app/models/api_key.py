from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import decrypt_secret, encrypt_secret
from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class UserApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user-supplied LLM API key (e.g. their own OpenAI/Anthropic key),
    stored encrypted at rest. Plaintext only ever exists in memory,
    briefly, when the app needs to call the provider on the user's behalf.

    A user can store keys for several providers/models at once; `is_default`
    marks the single one used for job-posting extraction (see
    app.services.job_llm_extractor). At most one row per user may have
    is_default=True — enforced by the partial unique index below.
    """

    __tablename__ = "user_api_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "label", name="uq_user_api_keys_user_provider_label"),
        Index(
            "uq_user_api_keys_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="default")
    # Which model to call with this key (e.g. "claude-opus-5", "deepseek-chat").
    # Required for every provider except ANTHROPIC, which falls back to a
    # sensible default when unset (see app.services.llm_client).
    model: Mapped[str | None] = mapped_column(String(100))
    # OpenAI-compatible endpoint override. Required for provider=OTHER;
    # optional for OPENAI/DEEPSEEK/GOOGLE/MISTRAL, which have known defaults.
    base_url: Mapped[str | None] = mapped_column(String(255))
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # The key used for job-posting extraction, when the user has more than one.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="api_keys")

    def set_plaintext_key(self, plaintext: str) -> None:
        self.encrypted_key, self.encryption_key_version = encrypt_secret(plaintext)

    def get_plaintext_key(self) -> str:
        return decrypt_secret(self.encrypted_key, self.encryption_key_version)

    def __repr__(self) -> str:
        return f"<UserApiKey user_id={self.user_id} provider={self.provider!r} label={self.label!r}>"
