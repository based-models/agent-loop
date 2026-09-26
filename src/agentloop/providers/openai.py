"""OpenAI: the origin of the wire format `_openai_compat.py` already speaks."""

from __future__ import annotations

import httpx

from agentloop.providers._openai_compat import OpenAICompatClient, Quirks

BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.1"
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def build_openai_client(
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    organization: str | None = None,
    project: str | None = None,
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
        quirks=Quirks(),
        timeout=TIMEOUT,
    )
