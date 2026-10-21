# OpenTelemetry tracing and metrics

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses OpenAI.

Install OpenAI support and the OpenTelemetry extra:

```bash
python -m pip install 'based-models-agentloop[openai-compat,otel]'
```

`trace=True` adds an `OTelObserver` to the agent. Install a tracer provider before running
it. This example uses an in-memory exporter so you can inspect the spans without a collector.
Configure OpenAI as in [Hello World](../getting-started/hello-world.md):

```python
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentloop import Agent

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

try:
    with Agent.from_env(os.environ, tools=[], name="demo", trace=True) as agent:
        print("answer:", agent.run("Reply with exactly ready."))
    spans = exporter.get_finished_spans()
    print("span operations:", [s.attributes["gen_ai.operation.name"] for s in spans])
finally:
    provider.shutdown()
```

Output:

```text
answer: ready
span operations: ['chat', 'invoke_agent']
```

Tracing creates one top-level span for the conversation, one child span for each model
request, and one span for each local tool call. A span is one timed operation in an
OpenTelemetry trace. Retries appear as events on the related model-request span.

For an OTLP collector, `configure_tracing(endpoint, service_name=...)` installs a provider
that exports spans to the endpoint's `/v1/traces` path. Call it once per process, then flush
and shut down the returned provider when your application exits. A traced conversation with
no provider logs one warning and discards its spans. Calling `configure_tracing()` after a
provider is already installed logs a warning because OpenTelemetry keeps the first one.

Prompts, tool arguments, tool results, and provider error messages are not captured by
default. To capture them, construct an `OTelObserver(..., capture_content=True,
redact=scrub)`. A redactor is a function that replaces sensitive text before it is exported;
see [capturing content in the event trace guide](event-trace.md#capturing-content) for a
simple example. A redactor is required unless you explicitly pass
`allow_unredacted=True`. One `OTelObserver` can trace only one conversation at a time.

The conversation span records its status, identifiers, iterations, limits, timing, retries,
usage, and stall flag. A failed call also gets `error.type`, `agentloop.error.phase` and `agentloop.error.request_sent`
(omitted when unknown) on its model span and on the root span, and every model span records
`agentloop.attempts`. It also receives stall and limit-warning events. This metadata is the
same whether content capture is on or off.

## Metrics

For dashboards and alerts, `agentloop.metrics.MetricsObserver` records counters and
distributions through the OpenTelemetry metrics API. `Agent(metrics=True)` provides a shorter
way to enable it. Like tracing, it records nothing until your application configures an
OpenTelemetry `MeterProvider`.

```python
import os

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from agentloop import Agent

reader = InMemoryMetricReader()
meter_provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(meter_provider)

try:
    with Agent.from_env(os.environ, tools=[], metrics=True) as agent:
        print("answer:", agent.run("Reply with exactly ready."))
    data = reader.get_metrics_data()
    names = sorted({
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    })
    print("metric names:", names)
finally:
    meter_provider.shutdown()
```

Output:

```text
answer: ready
metric names: ['agentloop.conversation.duration', 'agentloop.conversation.iterations', 'agentloop.conversation.model_duration', 'agentloop.conversations', 'agentloop.tokens']
```

| Instrument | Kind | Attributes |
|---|---|---|
| `agentloop.conversations` | counter | status, agent_name, provider, model |
| `agentloop.conversation.duration`, `.iterations` | histogram | same |
| `agentloop.conversation.model_duration` | histogram | agent_name, provider, model |
| `agentloop.conversation.tool_duration` | histogram, per tool per conversation | + tool |
| `agentloop.tool.calls` | counter | tool, error |
| `agentloop.tokens` | counter | provider, model, type |
| `agentloop.cost` | counter (USD), only when a provider reports one | provider, model |
| `agentloop.provider.retries` | counter | provider, model |
| `agentloop.provider.errors` | counter | provider, model, error_type, phase, request_sent (`true`/`false`/`unknown`) |
| `agentloop.stalls`, `agentloop.budget_warnings` | counters | tool / kind |

Metric labels use a small, repeatable set of values such as status, provider, model, and tool
name. Conversation, correlation, and parent IDs are excluded because unique IDs would create
a separate metric series for every run and can overwhelm a metrics system. Use distributed
traces or an event trace to find a specific conversation.

See the [`tracing` API](../api/tracing.md).
