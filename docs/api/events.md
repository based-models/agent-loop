# `agentloop.events`

Ordered JSONL event records for debugging and audit workflows.

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
logger method. The library does not choose or open the destination.

By default, records contain metadata such as event names, times, identifiers, tool names,
token counts, status codes, and result sizes. They exclude prompts, responses, tool
arguments, tool results, and error text.

Set `capture_content=True` to include content. A `redact` function is then required unless
`allow_unredacted=True` is explicitly set. Every captured string is redacted before it is
limited to `max_string_chars`. If redaction fails, that event is dropped rather than written
without redaction.

One observer instance supports one active conversation at a time. Create a separate instance
for each concurrent conversation.

See the [event trace guide](../guides/event-trace.md) for the event list and examples.
