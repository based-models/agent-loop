# Event trace

`EventTraceObserver` writes one JSON object per event, so you can see what a conversation
did: every request, response, retry and tool call, in order, with timings. It is for
debugging and audit, where a [summary](summaries.md) is too coarse.

```python
from agentloop import Agent, EventTraceObserver

with open("trace.jsonl", "a") as log:
    trace = EventTraceObserver(log.write)
    agent = Agent(tools, client=client, observer=trace)
    agent.run("find the lead investor", correlation_id="round-42")
```

The library never opens a file. You pass the callable that receives each line (`file.write`,
a queue's `put`, or a logger's `info` with `terminator=""`), so where the trace goes is your
decision.

## What a line contains

By default only metadata: `seq`, `ts` (wall time), `t` (seconds since the conversation
started), `event`, the conversation, correlation and parent ids, and per event the tool
names, call ids, token counts, cost, stop reason, status codes, error types, `is_error`,
result sizes and tool durations. No prompt, response, argument, result or error text.

Events: `conversation_start`, `request`, `response`, `retry`, `tool_start`, `tool_end`,
`stall`, `budget_warning`, and `conversation_end`, whose line carries the whole
`ConversationSummary`. The trace therefore doubles as a JSONL log of summaries.

## Capturing content

```python
import re

def redact(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9]{20,}", "[KEY]", text)

trace = EventTraceObserver(log.write, capture_content=True, redact=redact)
```

With `capture_content=True` the trace also carries what was said: the system prompt (once),
each request's NEW messages (not the whole transcript every turn), response content, tool
arguments and results, and error messages.

- **Every captured string passes through `redact`**, and is then clipped to
  `max_string_chars` (default 2000). Redaction runs first, so a cut cannot expose half a
  secret.
- **A redactor is required.** `capture_content=True` without one raises
  `ConfigurationError` unless you pass `allow_unredacted=True`.
- **The library ships no patterns.** A built-in list of things that look like secrets would
  promise more than it can keep; plug in your organization's scrubber.
- **It fails closed.** If `redact` raises, that event is dropped (and logged), never written
  unredacted.
- Structure you chose (tool names, ids, roles, part types) is not redacted; everything the
  model, a tool or a provider produced is.

## Using it with others

One `EventTraceObserver` traces one conversation at a time, like `OTelObserver`: the
callbacks carry no conversation id, so build one per concurrent conversation. Combine it
with other observers through `MultiObserver`. Lines are written under a lock, so `seq` and
file order agree even when tools run in parallel.
