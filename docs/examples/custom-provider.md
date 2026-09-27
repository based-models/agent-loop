# Custom provider

This offline client makes documentation and application tests deterministic:

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

agent = Agent([], client=FixedClient())
assert agent.run("ignored") == "Hello from a fixed response."
```

No close method is required by `LLMClient`. When a custom client owns resources, provide a
`close()` method and close it in application code; a directly constructed `Agent` borrows
the client.
