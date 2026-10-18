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

## Optional capabilities

- **Pause.** Return `StopReason.PAUSED` when the provider stopped partway through a turn it is
  running itself. The loop sends the turn back unchanged, with no user message, and joins the
  continuation to the same assistant message.
- **Hosted tools.** Expose a `hosted_tools` attribute (a `frozenset` of kinds such as
  `"web_search"`) to accept `HostedTool` declarations. Without it the client supports none,
  and `Agent` rejects them at construction. See `supported_hosted_tools`.
- **Exact replay.** Set `ModelResponse.origin` to an `Origin(provider=..., api=..., model=...,
  raw=...)` holding your wire form of the turn. Replay it when `origin.matches(provider, api)`
  and fall back to the canonical parts otherwise.
- **Empty turns.** `ModelResponse.content` may be empty only when `stop_reason` is
  `StopReason.MAX_TOKENS`; the loop answers that with a bigger budget. Any other empty turn
  should raise `ResponseInvalid`.

See the [`provider` API](../api/provider.md).
