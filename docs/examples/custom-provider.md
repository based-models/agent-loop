# Custom provider

A custom provider implements the `LLMClient` interface: `name`, `model`, and
`complete(request)`. This fixed client returns a reply without contacting a model service:

```python
from agentloop import Agent
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.transcript import TextPart

class FixedClient:
    name = "fixed"
    model = "fixed-v1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=(TextPart(text="Hello from a fixed response."),),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=1, output_tokens=6),
            model=self.model,
            provider=self.name,
        )

client = FixedClient()
agent = Agent([], client=client)
print(agent.run("Say hello."))
```

Output:

```text
Hello from a fixed response.
```

The agent passes a `ModelRequest` to `complete()` and uses the returned `ModelResponse` to
finish the conversation. `LLMClient` does not require a `close()` method. An `Agent` created
with `client=` borrows that client; if your implementation owns resources, close it in your
application code. See [implementing a custom provider](../guides/custom-providers.md) for
request translation and optional capabilities.
