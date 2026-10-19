# Providers

The high-level path is `Settings` plus `build_client`, or `Agent.from_env`:

```python
from agentloop import Agent

env = {
    "LLM_PROVIDER": "openrouter",
    "OPENROUTER_API_KEY": "supplied securely at runtime",
    "OPENROUTER_MODEL": "openrouter/free",
}

with Agent.from_env(env, tools=[]) as agent:
    print(agent.run("Hello"))
```

Every provider integration implements `LLMClient` and converts provider data into the same
request, response, content, usage, stop-reason, and error types. `build_client` also applies
the configured retry policy.

Concrete constructors are available for applications that need provider-specific options:

```python
from agentloop.providers.openrouter import build_openrouter_client

with build_openrouter_client(
    api_key="supplied securely at runtime",
    app_url="https://example.com",
    app_name="Example Agent",
) as client:
    print(client.name, client.model)
```

## Corporate networks: trust, proxy and transport

The library never reads certificates or builds a trust store. Pass an HTTP client you
configured, and it carries your settings for every request. It stays yours to close: closing
the agent or the provider client leaves it open.

OpenAI, OpenRouter and Modal take an `httpx.Client`. Anthropic takes an `httpx2.Client`,
because the Anthropic SDK is built on httpx2, whose classes are not httpx's. Both packages
take the same `verify`, `proxy` and `transport` arguments. Passing the wrong one raises
`ConfigurationError` naming the package the provider needs.

```python
import ssl

import httpx
import truststore

from agentloop import Agent

context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # the operating system's trust store
http = httpx.Client(verify=context, proxy="http://proxy.corp.example:8080")

with Agent.from_env(env, tools=[], http_client=http) as agent:
    print(agent.run("Hello"))
http.close()
```

For `LLM_PROVIDER=anthropic`, build the same client with `httpx2.Client(...)` instead.

The same `http_client=` is accepted by `build_client`, the provider builders and
`AnthropicClient`. Without it, the HTTP package's environment variables (`HTTPS_PROXY`,
`SSL_CERT_FILE`) still apply. The adapter sends its URL and credentials per request, so your client needs no
`base_url` or default headers; its own timeout wins if you set one.

Redirects are not followed on the clients the library builds, because that would resend your
credentials to another host: a 3xx raises `UnexpectedRedirect`. The Anthropic SDK follows
redirects by default; pass a client with `follow_redirects=False` if you want one reported.
A TLS, DNS or proxy failure raises `ProviderUnreachable` immediately, without retries.

See the [provider reference](../api/providers/index.md) or
[implement a custom provider](custom-providers.md).

## Chat Completions or Responses

OpenAI can be reached over two APIs, chosen with `OPENAI_API` (`responses`, the default, or
`completions`; `Settings.openai_api`).

| | Chat Completions | Responses |
|---|---|---|
| Spoken by | OpenAI, OpenRouter, Modal/vLLM | OpenAI |
| Hosted tools (web search) | Not supported | Supported |
| Reasoning state | Provider-specific (OpenRouter `reasoning_details`) | Reasoning items replayed |

Responses runs **stateless**: every request sends `store: false` and the full transcript, and
item ids are stripped on replay, so nothing depends on the provider remembering a conversation.
OpenRouter and Modal always use Chat Completions.
