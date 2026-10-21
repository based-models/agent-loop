# `agentloop.tracing`

Tracing records the steps of an agent conversation as timed OpenTelemetry spans. One span
covers the whole conversation; child spans cover each model request and local tool call.
The span hierarchy lets you see which work belongs to a conversation and where time was
spent, including retries and tool execution.

`OTelObserver` creates these spans from agent loop callbacks. Pass it as `observer=` when
you want to configure tracing yourself, or use `Agent(trace=True)` to attach one with
default settings. Your application also installs a tracer provider to collect or export
the spans. Importing this module needs only the OpenTelemetry API; the optional `otel`
extra supplies the SDK and an OTLP exporter for the provider setup.

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
    redact=None,
    allow_unredacted=False,
)
```

Creates a top-level span for `conversation()`, a child span for each model request, and a
span for each local tool call. It implements all observer callbacks:

- `conversation()` manages the root span and reports unhandled failures.
- `on_request()` starts the current model span.
- `on_response()` records response metadata and usage, then ends the model span.
- `on_retry()` adds an event to the current model span.
- `on_model_error()` records the phase and send state of a failed call on its model span.
- `on_response()` also sets `agentloop.hosted_tool.calls`, a count of provider-run tool calls
  (never the queries).
- `on_tool_start()` starts a tool span keyed by call ID.
- `on_tool_end()` records result metadata and ends the matching span.
- `on_conversation_start()` adds conversation IDs to the root span.
- `on_conversation_end()` adds the final status, counts, timing, and usage to the root span.
- `on_budget_warning()` and `on_stall()` add events to the root span.
- `on_text_delta()` is currently a no-op reserved for streaming.

With `capture_content=False`, prompts, system instructions, arguments, results, and error
messages are excluded. Enabling it requires `redact` (a `str -> str` function) or
`allow_unredacted=True`, otherwise `ConfigurationError`. A redactor replaces sensitive text
before export; see the [content-capture example](../guides/event-trace.md#capturing-content)
for a simple one. Every captured string passes through `redact`, and error details are then
clipped to `ERROR_DETAIL_CHARS`. If `redact` raises, that attribute is dropped.
`Agent(trace=True)` never captures content: build an `OTelObserver` yourself to opt in.

One observer instance supports one active conversation. Overlapping `conversation()`
contexts raise `RuntimeError`. Tool callbacks are synchronized because tools can run in
parallel.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and the OpenTelemetry SDK, then set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat,otel]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

An in-memory exporter lets you inspect spans without a collector:

```python
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentloop import Agent, Settings
from agentloop.tracing import OTelObserver

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

settings = Settings.from_env(os.environ)
observer = OTelObserver(
    provider=settings.provider,
    model=settings.model,
    agent_name="example-agent",
)
try:
    with Agent.from_env(os.environ, tools=[], name="example-agent", observer=observer) as agent:
        print("answer:", agent.run("Reply with exactly ready."))

    operations = sorted(
        span.attributes["gen_ai.operation.name"]
        for span in exporter.get_finished_spans()
    )
    print("operations:", operations)
finally:
    provider.shutdown()
```

Output:

```text
answer: ready
operations: ['chat', 'invoke_agent']
```

The `invoke_agent` span covers the conversation. The `chat` span covers its one model
request. The application owns the tracer provider and shuts it down when finished.

## `configure_tracing`

```text
configure_tracing(endpoint, *, service_name="agentloop") -> TracerProvider
```

Configures the process to send batches of traces over OTLP/HTTP to
`{endpoint}/v1/traces`. Returns the provider so the application can call `force_flush()` and
`shutdown()`. Raises `ImportError` with installation guidance if the `otel` extra is absent.
The function does not read `OTEL_*` environment variables. Configure the process-global
tracer provider once: a later call cannot replace an existing provider and the returned
provider will receive no spans.

Set `OTLP_ENDPOINT` to the base URL of your OTLP/HTTP collector, such as
`http://localhost:4318`. With OpenAI configured as above:

```python
import os

from agentloop import Agent
from agentloop.tracing import configure_tracing

provider = configure_tracing(os.environ["OTLP_ENDPOINT"], service_name="example-agent")
try:
    with Agent.from_env(os.environ, tools=[], trace=True) as agent:
        print(agent.run("Reply with exactly ready."))
    provider.force_flush()
finally:
    provider.shutdown()
```

Output:

```text
ready
```

`configure_tracing()` sends spans to the endpoint's `/v1/traces` path. `force_flush()`
waits for the current batch to be exported; `shutdown()` releases the provider's resources.

Metrics are documented separately in the [`agentloop.metrics` reference](metrics.md) and the
[tracing and metrics guide](../guides/tracing.md#metrics).
