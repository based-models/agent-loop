# `agentloop.provider`

Provider-independent request and response types. Every model is frozen.

## `StopReason`

A `StrEnum` with members `END_TURN`, `TOOL_USE`, `MAX_TOKENS`, `STOP_SEQUENCE`, `REFUSAL`,
`ERROR`, `PAUSED`, and `UNKNOWN`. Provider integrations preserve unfamiliar values as
`UNKNOWN`; a local tool call forces `TOOL_USE` unless the response was truncated at
`MAX_TOKENS`. `PAUSED` means a provider-hosted tool stopped partway through a turn and needs
the loop to continue it.

## Type aliases

- `ToolChoice`: `"auto" | "none" | "required"`.

## `ToolSchema`

| Field | Type | Default |
|---|---|---|
| `name` | non-empty `str` | required |
| `description` | `str` | required |
| `parameters` | `dict[str, Any]` | required |
| `strict` | `bool` | `True` |

`parameters` is the bare JSON Schema, without a vendor-specific function envelope.

## `ModelRequest`

| Field | Type | Default |
|---|---|---|
| `messages` | `tuple[Message, ...]` | required |
| `system` | `str | None` | `None` |
| `tools` | `tuple[ToolSchema, ...]` | `()` |
| `max_tokens` | positive `int` | `16_000` |
| `temperature` | `float 0..2 | None` | `None` |
| `stop_sequences` | `tuple[str, ...]` | `()` |
| `tool_choice` | `ToolChoice` | `"auto"` |
| `parallel_tool_calls` | `bool` | `True` |
| `hosted_tools` | `tuple[HostedTool, ...]` | `()` |

`temperature=None` means the parameter should be omitted.

## `Usage`

Fields: `input_tokens` and `output_tokens` are non-negative integers defaulting to zero;
`cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens` are optional non-negative
integers; `cost_usd` is an optional non-negative float. `input_includes_cached` reports
whether `input_tokens` already includes cache reads. `None` means not reported, not zero.

## `ModelResponse`

| Field | Type | Default |
|---|---|---|
| `content` | non-empty `tuple[ContentPart, ...]` | required |
| `stop_reason` | `StopReason` | required |
| `usage` | `Usage` | required |
| `model` | `str` | required |
| `provider` | `str` | required |
| `raw_stop_reason` | `str | None` | `None` |
| `request_id` | `str | None` | `None` |
| `attempts` | positive `int` | `1` |
| `retry_wait_seconds` | non-negative `float` | `0.0` |
| `origin` | `Origin | None` | `None` |

```python
response = ModelResponse(
    content=(TextPart(text="Hello"),),
    stop_reason=StopReason.END_TURN,
    usage=Usage(input_tokens=3, output_tokens=1),
    model="demo-v1",
    provider="demo",
)
```

## `LLMClient`

Protocol with read-only `name: str` and `model: str` properties and:

```text
complete(request: ModelRequest) -> ModelResponse
```

See [custom providers](../guides/custom-providers.md) for an implementation.

## `HostedTool`

A provider-run capability declared on each request. `kind` names the capability, such as
`"web_search"`, and `options` contains provider-independent settings. The provider client
must advertise support for the kind; unsupported hosted tools are rejected when the agent
is created.
