# Providers

`Agent.from_env()` selects and builds a provider client from the mapping you give it. With
`LLM_PROVIDER=openai` and `OPENAI_API_KEY` configured as in
[Hello World](../getting-started/hello-world.md):

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[]) as agent:
    print(agent.run("Say hello in one word."))
```

Output:

```text
Hello
```

Each built-in provider implements the same `LLMClient` interface and converts requests,
responses, usage, stop reasons, and errors into shared types. `Agent.from_env()` uses
`Settings.from_env()` and `build_client()` internally; `build_client()` also applies the
configured retry policy. See [configuration](../getting-started/configuration.md) if you
need to validate settings separately.

## Provider-specific clients

Use a provider builder when you need options that `Settings` does not expose. For example,
the OpenAI builder accepts an organization or project header and lets you choose an API:

```python
import os

from agentloop.providers.openai import build_openai_client

with build_openai_client(api_key=os.environ["OPENAI_API_KEY"]) as client:
    print(client.name, client.model, client.api)
```

Output:

```text
openai gpt-5.1 responses
```

The builder returns a direct provider client. Wrap it in `RetryingClient` if you need
retries; `build_client()` and `Agent.from_env()` do that for you.

## HTTP client, trust, and proxy settings

Pass an HTTP client you configured when you need a custom trust store, proxy, transport, or
timeout. OpenAI, OpenRouter, and Modal use `httpx.Client`. Anthropic uses the HTTP client
type required by the installed Anthropic SDK (`httpx2.Client` in the version used here).

The supplied HTTP client stays yours to close. This example gives OpenAI a 30-second HTTP
timeout and checks that leaving the agent's context did not close the shared client:

```python
import os

import httpx

from agentloop import Agent

with httpx.Client(timeout=30.0) as http:
    with Agent.from_env(os.environ, tools=[], http_client=http) as agent:
        print(agent.run("Reply with the word ready."))
    print(f"HTTP client closed by agent: {http.is_closed}")
```

Output:

```text
ready
HTTP client closed by agent: False
```

For a corporate trust store or proxy, configure `httpx.Client(verify=..., proxy=...)` in
the same place. The adapter supplies its own URL and credentials for each request; your
client needs no `base_url` or default authorization header. If `LLM_TIMEOUT_SECONDS` is set,
that explicit per-call timeout overrides the HTTP client's timeout.

The same `http_client=` parameter is available on `build_client()` and provider builders.
Passing a client of the wrong HTTP package raises `ConfigurationError`. A TLS, DNS, or proxy
failure raises non-retryable `ProviderUnreachable`; a transient connection error may be
retried. OpenAI-compatible clients created by the library do not follow redirects and
report a 3xx as `UnexpectedRedirect`. Anthropic's SDK follows redirects by default; supply
a client with `follow_redirects=False` if you want it to report them instead.

## Chat Completions or Responses

OpenAI uses the Responses API by default. Set `OPENAI_API=completions` (or
`Settings.openai_api="completions"`) only when you need Chat Completions compatibility.
OpenRouter and Modal always use Chat Completions.

| | Chat Completions | Responses |
|---|---|---|
| Providers | OpenAI, OpenRouter, Modal/vLLM | OpenAI |
| Hosted web search | Not supported | Supported |
| Reasoning state | Provider-specific (OpenRouter `reasoning_details`) | Reasoning items replayed |

Responses requests send `store: false` and the full transcript. The adapter strips output
item IDs on replay, so a continued conversation does not depend on the provider retaining
server-side state.

See the [provider reference](../api/providers/index.md) or
[implement a custom provider](custom-providers.md).
