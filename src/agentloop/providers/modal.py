"""A self-deployed vLLM endpoint on Modal, serving an OpenAI-compatible /v1."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import httpx

from agentloop.providers._openai_compat import OpenAICompatClient, Quirks, httpx_timeout

DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
# Cold starts spin up a container and load weights: minutes, not seconds.
TIMEOUT_SECONDS = {"connect": 60.0, "read": 900.0, "write": 30.0, "pool": 10.0}

QUIRKS = Quirks(
    reports_usage_details=False,        # vLLM omits prompt_tokens_details
    errors_are_json=False,              # the proxy's 401 body is plain text
    default_parallel_tool_calls=False,  # small models get parallel calls wrong
)


def build_modal_client(
    *,
    endpoint_url: str,
    model: str = DEFAULT_MODEL,
    proxy_token_id: str = "",
    proxy_token_secret: str = "",
    http_client: httpx.Client | None = None,
) -> OpenAICompatClient:
    """Proxy tokens are both-or-neither; empty means an --unauthenticated endpoint."""
    headers: dict[str, str] = {}
    if proxy_token_id and proxy_token_secret:
        headers = {"Modal-Key": proxy_token_id, "Modal-Secret": proxy_token_secret}
    return OpenAICompatClient(
        name="modal",
        model=model,
        base_url=f"{endpoint_url.rstrip('/')}/v1",
        headers=headers,
        quirks=QUIRKS,
        timeout=httpx_timeout(**TIMEOUT_SECONDS),
        http_client=http_client,
    )
