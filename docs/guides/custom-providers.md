# Implement a custom provider

Implement the `LLMClient` protocol with `name`, `model`, and
`complete(request: ModelRequest) -> ModelResponse`. This small client echoes the latest user
message to show the interface without provider-specific wire formats:

```python
from agentloop import Agent
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

agent = Agent([], client=EchoClient())
print(agent.run("hello"))
```

Output:

```text
Echo: hello
```

`Agent` calls `complete()` with a provider-independent `ModelRequest`. The client returns
canonical content, a normalized stop reason, and usage. For a real provider, translate its
wire request and response inside the client, convert vendor failures into the
[`ProviderError` hierarchy](../api/errors.md), and keep vendor types out of the public
method signature. Map unfamiliar stop reasons to `StopReason.UNKNOWN` and retain the
original value in `raw_stop_reason`. A response containing a `ToolCallPart` must use
`StopReason.TOOL_USE` unless it was cut off at `StopReason.MAX_TOKENS`.

## Optional capabilities

- **Pause.** Return `StopReason.PAUSED` when the provider stopped partway through a turn it is
  running itself. The loop sends the unfinished turn back without a new user message and joins
  the continuation to the same assistant message.
- **Hosted tools.** Expose a `hosted_tools` attribute (a `frozenset` of kinds such as
  `"web_search"`) to accept `HostedTool` declarations. Without it the client supports none,
  and `Agent` rejects them at construction. See `supported_hosted_tools`.
- **Exact replay.** Set `ModelResponse.origin` to an `Origin(provider=..., api=..., model=...,
  raw=...)` holding your wire form of the turn. When a later request contains a message whose
  origin matches your provider and API, replay its raw blocks; otherwise translate its
  canonical parts.
- **Empty turns.** `ModelResponse.content` may be empty only when `stop_reason` is
  `StopReason.MAX_TOKENS`; the loop discards that turn and can retry with a bigger budget.
  Convert other empty provider responses to `ResponseInvalid`.

`Agent` borrows a client passed with `client=`. If your client owns a connection, close it in
your application code.

See the [`provider` API](../api/provider.md).
