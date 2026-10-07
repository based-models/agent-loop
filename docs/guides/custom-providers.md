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

Use it directly with `Agent`:

```python
from agentloop import Agent

agent = Agent([], client=EchoClient())
print(agent.run("hello"))
```

Custom providers should convert vendor failures into the
[`ProviderError` hierarchy](../api/errors.md), preserve unknown stop reasons as
`StopReason.UNKNOWN`, and keep vendor-specific types out of the public method signature.

See the [`provider` API](../api/provider.md).
