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

All adapters implement `LLMClient` and translate to the canonical request, response,
content, usage, stop-reason, and error types. `build_client` also applies the configured
retry policy.

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

See the [provider adapter reference](../api/providers/index.md) or
[implement a custom provider](custom-providers.md).
