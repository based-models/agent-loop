# `agentloop.transcript`

Canonical provider-independent conversation history.

## Type aliases

- `Role`: `"user" | "assistant"`.
- `ContentPart`: discriminated union of `TextPart`, `ToolCallPart`, `ToolResultPart`, and
  `ThinkingPart` using the `type` field.

All part models are frozen and reject unknown fields.

## Content parts

### `Part`

Base model carrying the shared immutable/strict configuration. It has no data fields.

### `TextPart`

Fields: `type="text"` and required `text: str`.

```python
part = TextPart(text="Hello")
```

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

Fields: `type="thinking"`, required `text`, and optional `signature`, `provider`, `model`,
and `raw`. `raw` preserves a vendor block for byte-faithful replay. Reasoning text may be
empty even when a signed block is present.

## `Message`

A frozen model with required `role: Role` and non-empty
`content: tuple[ContentPart, ...]`.

## `Transcript`

Mutable Pydantic model with public `messages: list[Message]`, defaulting to an empty list.

- `add_user_message(text) -> None`: appends one user `TextPart` message.
- `add_assistant_message(parts) -> None`: appends an assistant message.
- `add_tool_results(results) -> None`: verifies that every pending call is answered exactly
  once, orders results by call order, and appends a user-role result message. Raises
  `TranscriptError` on mismatch.
- `pending_tool_calls() -> tuple[ToolCallPart, ...]`: calls in the final assistant message,
  or an empty tuple.
- `check_invariants() -> None`: validates call/result adjacency, completeness, and unique
  call IDs; raises `TranscriptError` if the transcript is unsafe to send.

```python
transcript = Transcript()
transcript.add_user_message("hello")
assert transcript.pending_tool_calls() == ()
transcript.check_invariants()
```

## Serialization and extraction

### `to_jsonl`

```text
to_jsonl(messages) -> str
```

Returns one JSON-serialized message per line with a trailing newline. It performs no I/O.

### `from_jsonl`

```text
from_jsonl(lines) -> Transcript
```

Parses any iterable of lines, skipping blanks. Invalid JSON or models raise validation
errors. It intentionally does not call `check_invariants()`.

### `final_text`

```text
final_text(transcript) -> str
```

Concatenates all text parts in the last assistant message. Returns `""` when there is no
assistant message or it contains no text.
