# `agentloop.providers.openrouter`

OpenRouter integration using the shared OpenAI-compatible HTTP client. Install
`based-models-agentloop[openai-compat]` and set `OPENROUTER_API_KEY`. For automatic
provider selection and retries, see the [providers guide](../../guides/providers.md).

## Constants

- `BASE_URL = "https://openrouter.ai/api/v1"`
- `DEFAULT_MODEL = "openrouter/free"`
- `TIMEOUT_SECONDS`: connect 10, read 300, write 30, and pool 10 seconds.

## `build_openrouter_client`

```text
build_openrouter_client(
    *,
    api_key,
    model="openrouter/free",
    app_url=None,
    app_name=None,
    http_client=None,
    timeout_seconds=None,
) -> OpenAICompatClient
```

The builder returns a direct `LLMClient` named `openrouter`. `app_url` sets the optional
`HTTP-Referer` attribution header, and `app_name` sets `X-Title`. `timeout_seconds`
overrides the 300-second read timeout while retaining the other timeout defaults.

OpenRouter uses Chat Completions (`api="completions"`) and has no hosted tools
(`hosted_tools` is empty). For reasoning models, it replays `reasoning_details` from an
assistant message's `Origin` when the message came from OpenRouter. This preserves the
provider's reasoning state across turns. The client exposes `name`, `model`, `api`,
`hosted_tools`, `complete(request)`, `close()`, and context manager methods.

This example sends one request and reads the normalized response text:

```python
import os

from agentloop import Transcript, join_text
from agentloop.provider import ModelRequest
from agentloop.providers.openrouter import build_openrouter_client

transcript = Transcript()
transcript.add_user_message("Reply with exactly: ready")
request = ModelRequest(messages=tuple(transcript.messages), max_tokens=100)

with build_openrouter_client(api_key=os.environ["OPENROUTER_API_KEY"]) as client:
    response = client.complete(request)
    print(client.name, client.api)
    print(join_text(response.content))
```

Output:

```text
openrouter completions
ready
```

Pass an `httpx.Client` as `http_client` to configure a trust store, proxy, or transport.
The client remains yours to close, and `close()` leaves it open. Passing another HTTP
client type raises `ConfigurationError`. The builder does not add retries; use
`build_client()` or `RetryingClient` if you need them.
