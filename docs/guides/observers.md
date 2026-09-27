# Observers

Observers receive lifecycle events without changing the agent loop. Subclass
`NullObserver` so new optional callbacks default to no-ops.

```python
from agentloop import NullObserver
from agentloop.provider import ModelResponse

class UsageObserver(NullObserver):
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def on_response(self, response: ModelResponse) -> None:
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
```

Combine independent observers in order:

```python
from agentloop import ConsoleObserver, MultiObserver

usage = UsageObserver()
observer = MultiObserver(ConsoleObserver(), usage)
```

`ConsoleObserver` prints text, tool activity, and retries. `ListObserver` records events for
tests and debugging. Callback failures are logged and swallowed so telemetry cannot corrupt
a conversation; failure while entering `conversation()` is not swallowed because no agent
work has begun.

See the [`observer` API](../api/observer.md).
