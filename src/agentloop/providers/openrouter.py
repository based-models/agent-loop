"""OpenRouter: an OpenAI-compatible gateway. Ten lines of configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import httpx

from agentloop.providers._openai_compat import OpenAICompatClient, Quirks, httpx_timeout

BASE_URL = "https://openrouter.ai/api/v1"
# The `openrouter/free` router spreads across free models and auto-filters for
# tool-calling support, so one congested upstream cannot hard-block you.
DEFAULT_MODEL = "openrouter/free"
TIMEOUT_SECONDS = {"connect": 10.0, "read": 300.0, "write": 30.0, "pool": 10.0}


def build_openrouter_client(
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    app_url: str | None = None,
    app_name: str | None = None,
    http_client: httpx.Client | None = None,
) -> OpenAICompatClient:
    headers = {"Authorization": f"Bearer {api_key}"}
    if app_url:
        headers["HTTP-Referer"] = app_url      # optional attribution
    if app_name:
        headers["X-Title"] = app_name
    return OpenAICompatClient(
        name="openrouter",
        model=model,
        base_url=BASE_URL,
        headers=headers,
        quirks=Quirks(),
        timeout=httpx_timeout(**TIMEOUT_SECONDS),
        http_client=http_client,
    )
