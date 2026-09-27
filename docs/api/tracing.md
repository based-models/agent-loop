# `agentloop.tracing`

OpenTelemetry implementation of `LoopObserver`. Importing this module needs only the OTel
API dependency; exporter setup needs the optional `otel` extra.

## Constants

- `DEFAULT_AGENT_NAME = "agentloop"`
- `ERROR_DETAIL_CHARS = 200`: maximum captured error-detail length.

## `OTelObserver`

```text
OTelObserver(
    *,
    provider,
    model,
    agent_name="agentloop",
    capture_content=False,
)
```

Emits a root span for `conversation()`, a child span per model request, and a sibling span
per tool call. It implements all observer callbacks:

- `conversation()` manages the root span and reports unhandled failures.
- `on_request()` starts the current model span.
- `on_response()` records response metadata and usage, then ends the model span.
- `on_retry()` adds an event to the current model span.
- `on_tool_start()` starts a tool span keyed by call ID.
- `on_tool_end()` records result metadata and ends the matching span.
- `on_text_delta()` is currently a no-op reserved for streaming.

With `capture_content=False`, prompts, system instructions, arguments, results, and error
messages are excluded. With it enabled those values may reach the exporter.

An observer is non-reentrant: overlapping `conversation()` contexts raise `RuntimeError`.
Tool callbacks themselves are synchronized because tool handlers can run concurrently.

```python
observer = OTelObserver(
    provider="openai",
    model="gpt-5.1",
    agent_name="support-agent",
)
```

## `configure_tracing`

```text
configure_tracing(endpoint, *, service_name="agentloop") -> TracerProvider
```

Installs a process-global SDK tracer provider with a batched OTLP/HTTP exporter targeting
`{endpoint}/v1/traces`. Returns the provider so the application can call `force_flush()` and
`shutdown()`. Raises `ImportError` with installation guidance if the SDK extra is absent.
The function does not read `OTEL_*` variables.
