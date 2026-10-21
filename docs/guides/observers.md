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

## Write a callback

`NullObserver` implements every callback as a no-op. Subclass it and override only the events
you need. Here, `on_response()` receives a `ModelResponse` after each successful model call.
With OpenAI configured as in [Hello World](../getting-started/hello-world.md):

```python
import os

from agentloop import Agent, NullObserver
from agentloop.provider import ModelResponse


class UsageObserver(NullObserver):
    def __init__(self) -> None:
        self.responses = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_response(self, response: ModelResponse) -> None:
        self.responses += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        print("response.usage:", response.usage)


prompt = "How many days are in a week? Reply with one digit."
usage = UsageObserver()
with Agent.from_env(os.environ, tools=[], observer=usage) as agent:
    answer = agent.run(prompt)

print("input:", prompt)
print("output:", answer)
print("responses:", usage.responses)
print("input tokens:", usage.input_tokens)
print("output tokens:", usage.output_tokens)
```

Output:

```text
response.usage: input_tokens=19 output_tokens=11 cache_read_tokens=0 cache_write_tokens=None reasoning_tokens=0 cost_usd=None input_includes_cached=True
input: How many days are in a week? Reply with one digit.
output: 7
responses: 1
input tokens: 19
output tokens: 11
```

## Combine observers

`ConsoleObserver` prints model text and tool activity. `ListObserver` keeps request, response,
retry, and tool events in memory. `MultiObserver` sends each callback to its children in
order, so one run can use both:

```python
import os

from agentloop import Agent, ConsoleObserver, ListObserver, MultiObserver

recorded = ListObserver()
observer = MultiObserver(ConsoleObserver(), recorded)
with Agent.from_env(os.environ, tools=[], observer=observer) as agent:
    agent.run("Reply with exactly ready.")

print("recorded events:", recorded.kinds())
```

Output:

```text
ready
recorded events: ['request', 'response']
```

`ConsoleObserver` also prints retry errors when they occur. `ListObserver` is useful for
tests and debugging. If a callback raises, the library logs the failure and continues the
conversation. An exception raised while entering an observer's `conversation()` context
propagates before agent work begins.

Five callbacks are optional for older observer implementations:
`on_conversation_start(context)`, `on_conversation_end(summary)`,
`on_budget_warning(kind, used, limit)`, `on_stall(tool_name, count)`, and
`on_model_error(error)`. The context carries run identifiers; the summary describes how the
run ended. Budget and stall events require the options described in
[Conversation summaries](summaries.md). To keep a JSONL record of every event, see the
[event trace](event-trace.md).

See the [`observer` API](../api/observer.md).
