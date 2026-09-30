"""OpenAI: either of its APIs, over one transport.

`api="responses"` (the default) is OpenAI's current API and the only one with
hosted tools such as web search; `api="completions"` is Chat Completions, the
format OpenRouter and Modal also speak. See `_openai_responses.py` for why the
Responses codec is stateless.
"""

from __future__ import annotations

from agentloop.providers._openai_compat import (
    Api,
    OpenAICompatClient,
    Quirks,
    httpx_timeout,
)

BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.1"
TIMEOUT_SECONDS = {"connect": 10.0, "read": 300.0, "write": 30.0, "pool": 10.0}

# Chat Completions: OpenAI requires `max_completion_tokens` for its reasoning
# models and recommends it generally; `max_tokens` is the OpenRouter/Modal-era
# default. (Responses always sends `max_output_tokens`.)
QUIRKS = Quirks(max_tokens_field="max_completion_tokens")


def build_openai_client(
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    organization: str | None = None,
    project: str | None = None,
    api: Api = "responses",
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
        quirks=QUIRKS,
        timeout=httpx_timeout(**TIMEOUT_SECONDS),
        api=api,
    )
