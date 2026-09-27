# Implement a custom provider

Implement the `LLMClient` protocol: `name`, `model`, and `complete()`.

```python
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.transcript import TextPart

class EchoClient:
    name = "echo"
    model = "echo-v1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        last = request.messages[-1]
        text = " ".join(
            part.text for part in last.content if isinstance(part, TextPart)
        )
        return ModelResponse(
            content=(TextPart(text=f"Echo: {text}"),),
            stop_reason=StopReason.END_TURN,
            usage=Usage(),
            model=self.model,
            provider=self.name,
        )
```

Use it with the ergonomic API:

```python
from agentloop import Agent

agent = Agent([], client=EchoClient())
print(agent.run("hello"))
```

Adapters should normalize vendor failures into the
[`ProviderError` hierarchy](../api/errors.md), preserve unknown stop reasons as
`StopReason.UNKNOWN`, and never expose vendor types in the protocol signature.

See the [`provider` API](../api/provider.md).
