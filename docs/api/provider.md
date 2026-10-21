# `agentloop.provider`

Provider-independent request and response types. The loop sends a `ModelRequest` to an
`LLMClient` and receives a `ModelResponse`; provider adapters translate between these
types and each provider's API. The Pydantic models on this page are frozen.

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
`strict=True` asks supporting providers to constrain tool-call arguments to that schema;
adapters omit or disable the setting when the selected API does not support it.

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

| Field | Type | Default |
|---|---|---|
| `input_tokens`, `output_tokens` | non-negative `int` | `0` |
| `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` | non-negative `int | None` | `None` |
| `cost_usd` | non-negative `float | None` | `None` |
| `input_includes_cached` | `bool` | `True` |

`input_includes_cached` reports whether `input_tokens` already includes cache reads. A
`None` optional field means the provider did not report it; this differs from zero.

## `ModelResponse`

| Field | Type | Default |
|---|---|---|
| `content` | `tuple[ContentPart, ...]` | required |
| `stop_reason` | `StopReason` | required |
| `usage` | `Usage` | required |
| `model` | `str` | required |
| `provider` | `str` | required |
| `raw_stop_reason` | `str | None` | `None` |
| `request_id` | `str | None` | `None` |
| `attempts` | positive `int` | `1` |
| `retry_wait_seconds` | non-negative `float` | `0.0` |
| `attempt_log` | `tuple[AttemptRecord, ...]` | `()` |
| `origin` | `Origin | None` | `None` |

`content` may be empty only when `stop_reason=MAX_TOKENS` and the provider stopped before
producing usable content. `attempts`, `retry_wait_seconds`, and `attempt_log` describe
retries; the log is empty when the first attempt succeeded. `origin` holds the adapter's
wire representation for replay when available.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example calls
OpenAI directly. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Build a request, send it through the configured client, and read the normalized response:

```python
import os

from agentloop import Settings, Transcript, build_client
from agentloop.provider import ModelRequest
from agentloop.transcript import TextPart

transcript = Transcript()
transcript.add_user_message("How many days are in a week? Reply with one digit.")
request = ModelRequest(messages=tuple(transcript.messages))

client = build_client(Settings.from_env(os.environ))
try:
    response = client.complete(request)
    print("".join(part.text for part in response.content if isinstance(part, TextPart)))
    print(response.stop_reason.value)
finally:
    client.close()
```

Output:

```text
7
end_turn
```

`build_client()` supplies a retrying `LLMClient`. This direct call makes one model request;
the higher-level [`Agent`](agent.md) also handles tool calls and transcript updates.

## `AttemptRecord`

One provider attempt in `attempt_log`. Its fields are `attempt` (one-based number), `ok`,
`error_type`, `status_code`, `phase`, `request_sent`, `request_id`, and `wait_seconds`.
Optional error fields are `None` when unknown. `wait_seconds` is the delay after this
attempt and is zero for the last attempt.

## `LLMClient`

Protocol with read-only `name: str` and `model: str` properties and:

```text
complete(request: ModelRequest) -> ModelResponse
```

See [custom providers](../guides/custom-providers.md) for an implementation.

## `supported_hosted_tools`

```text
supported_hosted_tools(client) -> frozenset[str]
```

The hosted tool kinds a client can run: its optional `hosted_tools` attribute, or an empty set
when it has none. A client that says nothing supports nothing, so a custom provider needs no
new attribute to stay an `LLMClient`.

## `HostedTool`

A provider-run capability declared on each request. `kind` names the capability, such as
`"web_search"`, and `options` contains settings for the adapter to translate. `kind` must
be a non-empty string and `options` defaults to an empty dictionary. The provider client
must advertise support for the kind; unsupported hosted tools are rejected when the agent
is created. The separate `agentloop-tools` package offers validated helpers that create
these `HostedTool` objects. See [Hosted tools and web search](../guides/hosted-tools.md).
