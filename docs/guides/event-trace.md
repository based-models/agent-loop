# Event trace

`EventTraceObserver` records each conversation event as one line of JSON (JSONL). It shows
requests, responses, retries, and tool calls in order with their timing. Use it for debugging
or audit workflows when a [conversation summary](summaries.md) does not provide enough
detail.

```python
from agentloop import Agent, EventTraceObserver

with open("trace.jsonl", "a") as log:
    trace = EventTraceObserver(log.write)
    agent = Agent(tools, client=client, observer=trace)
    agent.run("summarize the open support case", correlation_id="case-42")
```

The library never opens a file or selects a logging service. You provide the function that
receives each line, such as `file.write`, a queue's `put`, or a logger's `info` with
`terminator=""`.

## What a line contains

By default, each line contains metadata only: sequence number, timestamp, elapsed time,
event name, conversation identifiers, and relevant counts, names, sizes, or status values.
It does not contain prompts, model responses, tool arguments, tool results, or error text.

The event names are `conversation_start`, `request`, `response`, `retry`, `tool_start`,
`tool_end`, `stall`, `budget_warning`, and `conversation_end`. The final event carries the
complete `ConversationSummary`, so the trace also serves as a JSONL log of summaries.

## Capturing content

```python
import re

def redact(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9]{20,}", "[KEY]", text)

trace = EventTraceObserver(log.write, capture_content=True, redact=redact)
```

With `capture_content=True`, the trace also carries the system prompt once, messages added
since the previous request, response content, tool
arguments and results, and error messages.

- **Every captured string passes through `redact`**, and is then limited to
  `max_string_chars` (default 2000 characters). Redaction runs first, so a cut cannot expose
  part of a secret.
- **A redactor is required.** `capture_content=True` without one raises
  `ConfigurationError` unless you pass `allow_unredacted=True`.
- **The library ships no patterns.** A built-in list of things that look like secrets would
  promise more than it can keep; plug in your organization's scrubber.
- **It fails closed.** If `redact` raises an exception, that event is dropped and logged. It
  is never written without redaction.
- Structure you chose (tool names, ids, roles, part types) is not redacted; everything the
  model, a tool or a provider produced is.

## Using it with others

One `EventTraceObserver` supports one active conversation at a time. Create one instance for
each concurrent conversation. Combine it with other observers through `MultiObserver`.
Sequence numbers and output order remain consistent when tools run in parallel.
