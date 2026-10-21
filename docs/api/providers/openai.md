# `agentloop.providers.openai`

OpenAI integration using the shared OpenAI-compatible HTTP client. Install
`based-models-agentloop[openai-compat]` and set `OPENAI_API_KEY`. For automatic provider
selection and retries, see the [providers guide](../../guides/providers.md).

## Constants

- `BASE_URL = "https://api.openai.com/v1"`
- `DEFAULT_MODEL = "gpt-5.1"`
- `TIMEOUT_SECONDS`: connect 10, read 300, write 30, and pool 10 seconds.
- `QUIRKS`: OpenAI's default compatibility settings. Chat Completions uses
  `max_completion_tokens`; Responses uses `max_output_tokens`.
- `MODEL_QUIRKS` and `quirks_for_model(model)`: select model-specific settings. For
  `o1`, `o3`, and `o4` model families, Chat Completions omits `parallel_tool_calls`.

## `build_openai_client`

```text
build_openai_client(
    *,
    api_key,
    model="gpt-5.1",
    organization=None,
    project=None,
    api="responses",
    quirks=None,
    http_client=None,
    timeout_seconds=None,
) -> OpenAICompatClient
```

The builder returns a direct `LLMClient` named `openai`. `organization` and `project` add
the `OpenAI-Organization` and `OpenAI-Project` request headers. `quirks` overrides the
settings selected for the model; most applications can leave it unset. `timeout_seconds`
overrides the 300-second read timeout while retaining the other timeout defaults.

`api="responses"` posts to `/responses` and supports hosted web search. It sends the
full transcript with `store: false`, so conversation history stays in the local
`Transcript`. `api="completions"` posts to `/chat/completions` and supports local tools,
but no hosted tools. Passing a hosted tool directly to a Completions client raises
`BadRequestError`; `Agent` rejects that combination during configuration.

Both formats return a normalized `ModelResponse`. The client exposes `name`, `model`,
`api`, `hosted_tools`, `complete(request)`, `close()`, and context manager methods.
`hosted_tools` contains `web_search` for Responses and is empty for Completions.

This example sends the same request through each API and reads the response text:

```python
import os

from agentloop import Transcript, join_text
from agentloop.provider import ModelRequest
from agentloop.providers.openai import build_openai_client

transcript = Transcript()
transcript.add_user_message("Reply with exactly: ready")
request = ModelRequest(messages=tuple(transcript.messages), max_tokens=500)

for api in ("responses", "completions"):
    with build_openai_client(
        api_key=os.environ["OPENAI_API_KEY"], model="gpt-4.1-mini", api=api
    ) as client:
        response = client.complete(request)
        print(client.api, join_text(response.content))
```

Output:

```text
responses ready
completions ready
```

Pass an `httpx.Client` as `http_client` to configure a trust store, proxy, or transport.
The client remains yours to close, and `close()` leaves it open. Passing another HTTP
client type raises `ConfigurationError`. The builder does not add retries; use
`build_client()` or `RetryingClient` if you need them.
