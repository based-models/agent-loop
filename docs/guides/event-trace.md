# Event trace

`EventTraceObserver` records each conversation event as one line of JSON (JSONL). It shows
requests, responses, retries, and tool calls in order with their timing. Use it for debugging
or audit workflows when a [conversation summary](summaries.md) does not provide enough
detail.

With OpenAI configured as in [Hello World](../getting-started/hello-world.md), this example
writes a trace to a temporary file and prints the event names from its JSONL records:

```python
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from agentloop import Agent, EventTraceObserver

with TemporaryDirectory() as directory:
    path = Path(directory) / "trace.jsonl"
    with path.open("w") as log:
        trace = EventTraceObserver(log.write)
        with Agent.from_env(os.environ, tools=[], observer=trace) as agent:
            agent.run("Reply with exactly ready.", correlation_id="example-1")

    for line in path.read_text().splitlines():
        record = json.loads(line)
        shown = {"seq": record["seq"], "event": record["event"]}
        if "summary" in record:
            shown["status"] = record["summary"]["status"]
        print(json.dumps(shown))
```

Output:

```text
{"seq": 1, "event": "conversation_start"}
{"seq": 2, "event": "request"}
{"seq": 3, "event": "response"}
{"seq": 4, "event": "conversation_end", "status": "completed"}
```

The library never opens a file or selects a logging service. You provide the function that
receives each line, such as `file.write` or a queue's `put`. For a logger, pass its `info`
method and set `terminator=""` on `EventTraceObserver`.

## What a line contains

By default, each line contains metadata only: sequence number, timestamp, elapsed time,
event name, conversation identifiers, and relevant counts, names, sizes, or status values.
It does not contain prompts, model responses, tool arguments, tool results, or error text.

The event names are `conversation_start`, `request`, `response`, `retry`, `model_error`,
`tool_start`, `tool_end`, `stall`, `budget_warning`, and `conversation_end`. `retry` reports an
attempt that will be retried. `model_error` reports a call that failed for good, with fields
such as `error_type`, `status_code`, `phase`, `request_sent`, `request_id`, `attempts`, and
`attempt_log` when available. Unknown top-level event fields are omitted, so a missing
`request_sent` means unknown, not `false`. The final event embeds the complete
`ConversationSummary`, whose optional fields may be `null`.

## Capturing content

Pass a redaction function to capture prompts and responses. The example uses a simple tag
to show where your application's redactor belongs:

```python
import json
import os

from agentloop import Agent, EventTraceObserver

lines = []

def redact(text: str) -> str:
    return text.replace("private-tag", "[REDACTED]")

trace = EventTraceObserver(lines.append, capture_content=True, redact=redact)
with Agent.from_env(os.environ, tools=[], observer=trace) as agent:
    agent.run("Reply with exactly ready. My tag is private-tag.")

request = next(json.loads(line) for line in lines if '"event": "request"' in line)
print(request["new_messages"][0]["parts"][0]["content"]["text"])
print("private tag in trace:", "private-tag" in "".join(lines))
```

Output:

```text
Reply with exactly ready. My tag is [REDACTED].
private tag in trace: False
```

With `capture_content=True`, the trace also carries the system prompt once, messages added
since the previous request, response content, tool
arguments and results, and error messages.

- **Every captured string passes through `redact`**, then is clipped after
  `max_string_chars` (default 2000 characters), with a truncation marker appended. Redaction
  runs first, so a cut cannot expose part of a secret.
- **A redactor is required.** `capture_content=True` without one raises
  `ConfigurationError` unless you pass `allow_unredacted=True`.
- **The library ships no patterns.** A built-in list of things that look like secrets would
  promise more than it can keep; plug in your organization's scrubber.
- **It fails closed.** If `redact` raises an exception, that event is dropped and logged. It
  is never written without redaction.
- Structural fields such as tool names, call IDs, roles, and part types are not passed through
  `redact`. Review those fields before retaining a trace.

## Using it with others

One `EventTraceObserver` supports one active conversation at a time. Create one instance for
each concurrent conversation. Combine it with other observers through `MultiObserver`.
Sequence numbers and output order remain consistent when tools run in parallel.
