import logging

import anthropic
import openai

from app.models.enums import LlmProvider

logger = logging.getLogger("app.llm_client")

# Anthropic has no configurable base_url in our schema (it's always the
# first-party API), so a model is the only thing that can be left unset.
_DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

# Providers that speak the OpenAI-compatible chat/completions wire format at
# a known endpoint. DeepSeek, and Google and Mistral (via their documented
# OpenAI-compatibility layers) all work through the official `openai` SDK by
# pointing it at a different base_url — this is how those providers document
# integrating, not a workaround. OPENAI itself needs no override (the SDK's
# own default). Adding another OpenAI-compatible provider later needs no
# code change: the user just stores a key with provider=OTHER and their
# endpoint's base_url.
_OPENAI_COMPATIBLE_BASE_URLS: dict[str, str | None] = {
    LlmProvider.OPENAI: None,
    LlmProvider.DEEPSEEK: "https://api.deepseek.com",
    LlmProvider.GOOGLE: "https://generativelanguage.googleapis.com/v1beta/openai/",
    LlmProvider.MISTRAL: "https://api.mistral.ai/v1",
}


class LlmError(Exception):
    """Raised when a provider call fails or returns something unusable."""


def _clean_status_error_message(exc: anthropic.APIStatusError | openai.APIStatusError) -> str:
    """Both SDKs' `.message` is a verbose "Error code: N - {raw JSON body}"
    string — provider APIs conventionally nest the actual human-readable
    reason (e.g. "API key is invalid.") at body["error"]["message"], so
    prefer that when present instead of surfacing the raw blob to users.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        detail = body.get("error")
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"]
    return str(exc.message)


def call_llm(*, provider: str, model: str | None, api_key: str, base_url: str | None, prompt: str) -> str:
    """Send `prompt` to the given provider/model and return the raw text response."""
    if provider == LlmProvider.ANTHROPIC:
        return _call_anthropic(model=model or _DEFAULT_ANTHROPIC_MODEL, api_key=api_key, prompt=prompt)
    return _call_openai_compatible(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)


def _call_anthropic(*, model: str, api_key: str, prompt: str) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            # 2048 used to be the cap here, but a full tailored-resume/
            # cover-letter JSON response (summary + several sections of
            # bullets) routinely exceeds that for a real resume — the
            # response would get cut off mid-string, which surfaced
            # downstream as an opaque "Unterminated string" JSON parse
            # error instead of a clear token-limit message (see the
            # stop_reason=="max_tokens" check below for that case now).
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as exc:
        raise LlmError(f"Anthropic request failed ({exc.status_code}): {_clean_status_error_message(exc)}") from exc
    except anthropic.APIConnectionError as exc:
        raise LlmError(f"Could not reach Anthropic: {exc}") from exc

    if response.stop_reason == "refusal":
        raise LlmError("Anthropic declined to process this request.")
    if response.stop_reason == "max_tokens":
        raise LlmError("Anthropic's response was cut off after hitting the token limit — try again.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text:
        raise LlmError("Anthropic returned no text content.")
    return text


def _call_openai_compatible(
    *, provider: str, model: str | None, api_key: str, base_url: str | None, prompt: str
) -> str:
    if not model:
        raise LlmError(f"No model configured for provider '{provider}'.")
    resolved_base_url = base_url or _OPENAI_COMPATIBLE_BASE_URLS.get(provider)
    if provider == LlmProvider.OTHER and resolved_base_url is None:
        raise LlmError("No base_url configured for a custom ('other') provider.")

    client = openai.OpenAI(api_key=api_key, base_url=resolved_base_url)
    # Explicit cap for the same reason as Anthropic's above: a full tailored
    # resume/cover letter JSON response can be long, and a too-small default
    # would truncate it mid-string rather than fail with a clear reason.
    kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8192}
    try:
        try:
            response = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except openai.BadRequestError:
            # Not every OpenAI-compatible provider supports response_format.
            logger.info("Provider %s rejected response_format; retrying without it.", provider)
            response = client.chat.completions.create(**kwargs)
    except openai.APIStatusError as exc:
        raise LlmError(f"{provider} request failed ({exc.status_code}): {_clean_status_error_message(exc)}") from exc
    except openai.APIConnectionError as exc:
        raise LlmError(f"Could not reach {provider}: {exc}") from exc

    choice = response.choices[0] if response.choices else None
    if choice is not None and choice.finish_reason == "length":
        raise LlmError(f"{provider}'s response was cut off after hitting the token limit — try again.")
    content = choice.message.content if choice else None
    if not content:
        raise LlmError(f"{provider} returned no content.")
    return content
