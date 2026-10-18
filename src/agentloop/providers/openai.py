"""OpenAI: either of its APIs, over one transport.

`api="responses"` (the default) is OpenAI's current API and the only one with
hosted tools such as web search; `api="completions"` is Chat Completions, the
format OpenRouter and Modal also speak. See `_openai_responses.py` for why the
Responses codec is stateless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import httpx

from agentloop.providers._openai_compat import (
    Api,
    OpenAICompatClient,
    Quirks,
    timeout_with_read,
)

BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.1"
TIMEOUT_SECONDS = {"connect": 10.0, "read": 300.0, "write": 30.0, "pool": 10.0}

# Chat Completions: OpenAI requires `max_completion_tokens` for its reasoning
# models and recommends it generally; `max_tokens` is the OpenRouter/Modal-era
# default. (Responses always sends `max_output_tokens`.)
QUIRKS = Quirks(max_tokens_field="max_completion_tokens")

# OpenAI's o-series reasoning models reject `parallel_tool_calls`; its other chat models
# accept it. Matched by model-id prefix, longest first, so a family can be overridden by a
# more specific entry; no match falls back to QUIRKS. Modal and OpenRouter stay
# provider-wide: their models vary too much for one table to be right.
_NO_PARALLEL = QUIRKS.model_copy(update={"supports_parallel_tool_calls_param": False})
MODEL_QUIRKS: tuple[tuple[str, Quirks], ...] = (
    ("o1", _NO_PARALLEL),
    ("o3", _NO_PARALLEL),
    ("o4", _NO_PARALLEL),
)


def quirks_for_model(model: str) -> Quirks:
    """The quirks for `model`: the longest matching `MODEL_QUIRKS` prefix, else `QUIRKS`."""
    matches = [(prefix, q) for prefix, q in MODEL_QUIRKS if model.startswith(prefix)]
    return max(matches, key=lambda m: len(m[0]))[1] if matches else QUIRKS


def build_openai_client(
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    organization: str | None = None,
    project: str | None = None,
    api: Api = "responses",
    quirks: Quirks | None = None,
    http_client: httpx.Client | None = None,
    timeout_seconds: float | None = None,
) -> OpenAICompatClient:
    headers = {"Authorization": f"Bearer {api_key}"}
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    return OpenAICompatClient(
        name="openai",
        model=model,
        base_url=BASE_URL,
        headers=headers,
        quirks=quirks if quirks is not None else quirks_for_model(model),
        timeout=timeout_with_read(TIMEOUT_SECONDS, timeout_seconds),
        timeout_explicit=timeout_seconds is not None,
        http_client=http_client,
        api=api,
    )
