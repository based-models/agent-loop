# `agentloop.transcript`

Provider-independent conversation history.

## Type aliases

- `Role`: `"user" | "assistant"`.
- `ContentPart`: union of `TextPart`, `ToolCallPart`, `ToolResultPart`, `ThinkingPart`, and
  `HostedToolPart`. The `type` field identifies the concrete class.

All part models are frozen and reject unknown fields.

## Content parts

### `Part`

Base model carrying the shared immutable/strict configuration. It has no data fields.

### `TextPart`

Fields: `type="text"`, required `text: str`, and optional `citations`.

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

### `HostedToolPart`

Describes a provider-run tool call. Fields include `kind`, optional `call_id`, optional
`query`, `sources`, and optional `error`. The loop records this part for inspection but does
not execute it as a local Python tool.

### `Source` and `Citation`

`Source` records the URL and optional title of a page consulted by a provider-hosted tool.
`Citation` connects assistant text to a URL and may include a title, quoted text, or text
offsets when the provider reports them.

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
- `check_invariants() -> None`: validates call/result adjacency, completeness, and unique
  call IDs, and that hosted-tool parts appear only in assistant messages; raises
  `TranscriptError` if the transcript is unsafe to send.

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

Joins the text parts of the last assistant message with `join_text`: a space goes between
separate blocks ("I'll search." then "The answer is..."), but not across Anthropic's mid-sentence
citation splits or before closing punctuation. Returns `""` when there is no assistant
message or it contains no text.
