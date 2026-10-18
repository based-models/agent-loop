# `agentloop.tracing`

OpenTelemetry tracing for agent conversations. Importing this module needs only the
OpenTelemetry API dependency. Sending traces requires the optional `otel` extra.

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

Creates a top-level span for `conversation()`, a child span for each model request, and a
span for each local tool call. It implements all observer callbacks:

- `conversation()` manages the root span and reports unhandled failures.
- `on_request()` starts the current model span.
- `on_response()` records response metadata and usage, then ends the model span.
- `on_retry()` adds an event to the current model span.
- `on_model_error()` records the phase and send state of a failed call on its model span.
- `on_tool_start()` starts a tool span keyed by call ID.
- `on_tool_end()` records result metadata and ends the matching span.
- `on_text_delta()` is currently a no-op reserved for streaming.

With `capture_content=False`, prompts, system instructions, arguments, results, and error
messages are excluded. With it enabled those values may reach the exporter.

One observer instance supports one active conversation. Overlapping `conversation()`
contexts raise `RuntimeError`. Tool callbacks are synchronized because tools can run in
parallel.

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

Configures the process to send batches of traces over OTLP/HTTP to
`{endpoint}/v1/traces`. Returns the provider so the application can call `force_flush()` and
`shutdown()`. Raises `ImportError` with installation guidance if the `otel` extra is absent.
The function does not read `OTEL_*` environment variables.

Metrics are documented separately in the [`agentloop.metrics` reference](metrics.md) and the
[tracing and metrics guide](../guides/tracing.md#metrics).
