# `agentloop.transcript`

Provider-independent conversation history. A `Transcript` is an ordered list of user and
assistant `Message` objects. Each message contains one or more content parts, such as text,
a local tool call, or its result. Provider adapters convert their wire formats to these
types so the loop can use one transcript shape across providers.

## Type aliases

- `Role`: `"user" | "assistant"`.
- `ContentPart`: union of `TextPart`, `ToolCallPart`, `ToolResultPart`, `ThinkingPart`, and
  `HostedToolPart`. The `type` field identifies the concrete class.

All part models are frozen and reject unknown fields.

## Content parts

### `Part`

Base model carrying the shared immutable/strict configuration. It has no data fields.

### `TextPart`

Fields: `type="text"`, required `text: str`, and `citations: tuple[Citation, ...] = ()`.

### `ToolCallPart`

| Field | Type | Default |
|---|---|---|
| `type` | literal `"tool_call"` | `"tool_call"` |
| `id` | non-empty `str` | required |
| `tool_name` | non-empty `str` | required |
| `arguments` | `dict[str, Any]` | required |
| `raw_arguments` | `str | None` | `None` |
| `parse_error` | `str | None` | `None` |

`arguments` is always a parsed dictionary. Adapters preserve malformed input in
`raw_arguments` and explain it in `parse_error`.

### `ToolResultPart`

Fields: `type="tool_result"`, required non-empty `call_id`, required non-empty `tool_name`,
required `content: str`, and `is_error: bool = False`.

### `ThinkingPart`

Fields: `type="thinking"`, required `text: str`, and optional `signature: str`,
`provider: str`, `model: str`, and `raw`. `raw` preserves a provider block for exact replay.
Reasoning text may be empty even when a signed block is present.

### `HostedToolPart`

Describes a provider-run tool call. Fields are `type="hosted_tool"`, required non-empty
`kind: str`, optional `call_id: str`, `query: str`, and `error: str`, and
`sources: tuple[Source, ...] = ()`. The loop records this part for inspection but does not
execute it as a local Python tool.

### `Source` and `Citation`

`Source` has a required `url: str` and optional `title: str`. It records a page consulted by
a provider-hosted tool. `Citation` has a required `url: str` and optional `title: str`,
`cited_text: str`, `start: int`, and `end: int`. It connects assistant text to a URL and
records quoted text or text offsets when the provider reports them.

## `Origin`

Provider-specific information attached to an assistant message. Its fields are
`provider: str`, `api: str`, `model: str`, and `raw: tuple[dict[str, Any], ...]`. An adapter
for the same provider and API can replay `raw` to continue the conversation faithfully.
Other adapters rebuild the message from its provider-independent content parts.
`matches(provider, api) -> bool` reports whether an adapter can use this origin; it returns
`False` when `provider` is `None`.

## `Message`

A frozen model with required `role: Role`, non-empty `content: tuple[ContentPart, ...]`, and
optional `origin`. `origin` preserves provider-specific data needed to continue the same
conversation accurately.

## `Transcript`

Mutable Pydantic model with public `messages: list[Message]`, defaulting to an empty list.

- `add_user_message(text) -> None`: appends one user `TextPart` message.
- `add_assistant_message(parts, origin=None) -> None`: appends an assistant message, keeping
  the provider's `Origin` for exact replay.
- `extend_last_assistant(parts, origin=None) -> None`: continues the last assistant message
  (a turn the provider paused), merging `origin.raw` when both origins match. Raises
  `TranscriptError` when the last message is not from the assistant.
- `add_tool_results(results) -> None`: verifies that every pending call is answered exactly
  once, orders results by call order, and appends a user-role result message. Raises
  `TranscriptError` on mismatch.
- `pending_tool_calls() -> tuple[ToolCallPart, ...]`: calls in the final assistant message,
  or an empty tuple.
- `check_invariants() -> None`: validates call/result adjacency, completeness, unique call
  IDs within each message, and that hosted-tool parts appear only in assistant messages.
  It raises `TranscriptError` if the transcript is unsafe to send.

## Serialization and extraction

### `to_jsonl`

```text
to_jsonl(messages) -> str
```

Returns one JSON-serialized message per line with a trailing newline when there are
messages. It performs no I/O; an empty input returns an empty string.

### `from_jsonl`

```text
from_jsonl(lines) -> Transcript
```

Parses any iterable of lines, skipping blanks. Invalid JSON or models raise validation
errors. It intentionally does not call `check_invariants()`.

### `join_text`

```text
join_text(parts) -> str
```

Joins text parts from one message. It adds a space between separate blocks when needed,
while preserving mid-sentence citation splits and punctuation.

### `final_text`

```text
final_text(transcript) -> str
```

Uses `join_text` on the last assistant message. Returns `""` when there is no assistant
message or it contains no text.

Create a transcript, serialize it, and restore it:

```python
from agentloop import Transcript, final_text
from agentloop.transcript import TextPart, from_jsonl, to_jsonl

transcript = Transcript()
transcript.add_user_message("Hello")
transcript.add_assistant_message((TextPart(text="Hi!"),))
transcript.check_invariants()

jsonl = to_jsonl(transcript.messages)
restored = from_jsonl(jsonl.splitlines())
restored.check_invariants()
print(final_text(restored))
print(len(restored.messages))
print(jsonl.count("\n"))
```

Output:

```text
Hi!
2
2
```

The two JSONL lines represent the user and assistant messages. `from_jsonl()` recreates
both; `final_text()` reads the assistant reply.
