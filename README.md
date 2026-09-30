# based-models-agentloop

A synchronous, provider-agnostic agent loop for Python. It supplies canonical conversation
types, provider adapters (Anthropic, OpenAI, OpenRouter, Modal/vLLM), tool execution, retries,
observers and optional OpenTelemetry tracing, and leaves application policy in your code.
The import name is `agentloop`.

```bash
pip install "based-models-agentloop[anthropic]"
```

| Extra | Adds |
|---|---|
| `anthropic` | the Anthropic SDK |
| `openai-compat` | `httpx`, for OpenAI, OpenRouter and Modal |
| `otel` | the OpenTelemetry SDK and OTLP HTTP exporter |
| `all` | everything above |

No provider SDK is imported until its client is constructed.

```python
import os

from agentloop import Agent, tool

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string", "description": "City name"}},
    "required": ["city"],
    "additionalProperties": False,
}

@tool(parameters=SCHEMA)
def weather(city: str) -> str:
    """Return example weather for a city."""
    return f"The example forecast for {city} is sunny."

# You supply the environment mapping; the library never reads os.environ on its own.
with Agent.from_env(os.environ, tools=[weather], system="Use the weather tool.") as agent:
    print(agent.run("What is the weather in Lisbon?"))
```

Set `LLM_PROVIDER` and that provider's API key first; see the configuration docs.

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Requires Python 3.12 or newer. While the version is `0.x`, a minor release may change the
  public API (`agentloop.__all__`).
- Licensed under the [Apache License 2.0](LICENSE).
