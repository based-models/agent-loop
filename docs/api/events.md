# `agentloop.events`

Ordered JSONL event records for debugging and audit workflows. `EventTraceObserver`
receives loop callbacks and writes one JSON object per event through a function you provide.

## `EventTraceObserver`

```text
EventTraceObserver(
    write,
    *,
    capture_content=False,
    redact=None,
    allow_unredacted=False,
    max_string_chars=2000,
    terminator="\n",
)
```

`write` is a function that accepts a string, such as `file.write`, a queue's `put`, or a
logger method. The library does not choose or open the destination. Each call receives a
JSON record followed by `terminator`; set `terminator=""` when the writer already adds a
line break. Set `max_string_chars=None` to disable clipping captured strings.

Every record has `seq` (starting at 1 for each conversation), `ts` (Unix timestamp), `t`
(seconds since the conversation began), and `event`. Conversation, correlation, and parent
IDs are included when available. The possible event names are `conversation_start`,
`request`, `response`, `retry`, `model_error`, `tool_start`, `tool_end`, `stall`,
`budget_warning`, and `conversation_end`. `retry` reports an attempt that will be retried;
`model_error` reports a model call that failed after its final attempt. The final record
contains the complete `ConversationSummary`.

By default, records contain metadata such as event names, times, identifiers, tool names,
token counts, status codes, and result sizes. They exclude prompts, responses, tool
arguments, tool results, and error text. Unknown optional fields are omitted from a record;
the nested summary can still contain `null` values.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Keep the trace in memory and inspect the event names and request metadata:

```python
import json
import os

from agentloop import Agent, EventTraceObserver

lines = []
trace = EventTraceObserver(lines.append)
with Agent.from_env(os.environ, tools=[], observer=trace) as agent:
    agent.run("How many days are in a week? Reply with one digit.")

records = [json.loads(line) for line in lines]
print([record["event"] for record in records])
request = next(record for record in records if record["event"] == "request")
print(request["message_count"], "new_messages" in request)
```

Output:

```text
['conversation_start', 'request', 'response', 'conversation_end']
1 False
```

`message_count` reports the one user message sent to the model. `new_messages` is absent
because content capture is off.

Set `capture_content=True` to include content. A `redact` function is then required unless
`allow_unredacted=True` is explicitly set. Every captured string is redacted before it is
limited to `max_string_chars`. If redaction fails, that event is dropped rather than written
without redaction. Structural values such as tool names, call IDs, and message roles do not
pass through the redactor; review them before retaining a trace.

One observer instance supports one active conversation at a time. Create a separate instance
for each concurrent conversation.

See the [event trace guide](../guides/event-trace.md) for content-capture and redaction
examples.
