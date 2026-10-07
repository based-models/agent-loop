# Observers

An observer receives notifications as a conversation starts, calls a model or tool, retries,
and ends. Observers can log or export this activity without changing the conversation.
Subclass `NullObserver` so callbacks you do not implement safely do nothing.

## Choose an output

| Need | Use |
|---|---|
| One metadata record for each conversation | [Conversation summary](summaries.md) |
| An ordered JSONL record of every event | [Event trace](event-trace.md) |
| Spans that connect to other services | [OpenTelemetry tracing](tracing.md) |
| Counters and distributions for dashboards | [OpenTelemetry metrics](tracing.md#metrics) |
| Application-specific reporting | A custom observer |

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

Combine several observers:

```python
from agentloop import ConsoleObserver, MultiObserver

usage = UsageObserver()
observer = MultiObserver(ConsoleObserver(), usage)
```

`ConsoleObserver` prints text, tool activity, and retries. `ListObserver` records events for
tests and debugging. If a callback fails, the library logs the failure and continues the
conversation. An exception raised while starting an observer is returned to the caller
because agent work has not begun.

Four callbacks are optional, so older observers continue to work:
`on_conversation_start`, `on_conversation_end`, `on_budget_warning` and `on_stall`. They
carry the run's identifiers, its `ConversationSummary`, and the optional warnings described
in [Conversation summaries](summaries.md). To keep a JSONL record of everything, see the
[event trace](event-trace.md).

See the [`observer` API](../api/observer.md).
