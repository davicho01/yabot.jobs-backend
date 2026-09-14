import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import LlmProvider

# Providers with a documented OpenAI-compatible endpoint we already know
# (see app.services.llm_client) — anything else must supply base_url.
_KNOWN_BASE_URL_PROVIDERS = {
    LlmProvider.OPENAI,
    LlmProvider.DEEPSEEK,
    LlmProvider.GOOGLE,
    LlmProvider.MISTRAL,
}


class ApiKeyCreate(BaseModel):
    provider: str
    label: str = "default"
    api_key: str = Field(min_length=1)
    # Which model this key should call (e.g. "claude-opus-5", "deepseek-chat",
    # "gpt-4o-mini"). Optional only for ANTHROPIC, which has a sensible
    # default; required for every other provider since there's no single
    # "current" default we can safely assume on the user's behalf.
    model: str | None = None
    # OpenAI-compatible endpoint. Required when provider is OTHER; optional
    # override for providers with a known default (openai/deepseek/google/mistral).
    base_url: str | None = None
    # Mark this key as the one used for job-posting extraction. Setting it
    # unsets any other key's default for this user.
    is_default: bool = False

    @model_validator(mode="after")
    def _require_model_and_base_url(self) -> "ApiKeyCreate":
        if self.provider != LlmProvider.ANTHROPIC and not self.model:
            raise ValueError("model is required for every provider except anthropic.")
        if self.provider == LlmProvider.OTHER and not self.base_url:
            raise ValueError("base_url is required when provider is 'other'.")
        if self.provider in _KNOWN_BASE_URL_PROVIDERS.union({LlmProvider.ANTHROPIC}) and self.base_url:
            raise ValueError(f"base_url is not used for provider '{self.provider}' — omit it.")
        return self


class ApiKeyUpdate(BaseModel):
    label: str | None = None
    model: str | None = None
    base_url: str | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    label: str
    model: str | None
    base_url: str | None
    is_active: bool
    is_default: bool
    last_used_at: datetime | None
    created_at: datetime
    # Never the real key — just enough to recognize which one this is.
    masked_key: str
