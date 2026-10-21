# `agentloop.metrics`

OpenTelemetry metrics for dashboards and alerts. `MetricsObserver` turns conversation
summaries and selected loop events into counters and histograms.

## `MetricsObserver`

```text
MetricsObserver(meter=None)
```

Pass an OpenTelemetry `Meter`, or let the observer use
`opentelemetry.metrics.get_meter("agentloop")`. Install and configure a `MeterProvider` in
your application to collect the measurements; the SDK is available through the
`based-models-agentloop[otel]` extra. Most instruments are recorded from
`on_conversation_end(summary)`; the stall and budget-warning counters are recorded when
those events occur.

`MetricsObserver` keeps no per-conversation state and can be shared by concurrent
conversations. Use `Agent(metrics=True)` to add one automatically.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and the OpenTelemetry SDK, then set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat,otel]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

An in-memory reader lets you inspect a metric without running an exporter:

```python
import os

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from agentloop import Agent
from agentloop.metrics import MetricsObserver

reader = InMemoryMetricReader()
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)
try:
    meter = metrics.get_meter("agentloop")
    observer = MetricsObserver(meter)
    with Agent.from_env(os.environ, tools=[], observer=observer) as agent:
        answer = agent.run("How many days are in a week? Reply with one digit.")

    data = reader.get_metrics_data()
    conversations = next(
        metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "agentloop.conversations"
    )
    point = conversations.data.data_points[0]
    print(answer)
    print(point.attributes["status"], point.value)
finally:
    provider.shutdown()
```

Output:

```text
7
completed 1
```

`metrics.get_meter("agentloop")` returns the OpenTelemetry `Meter` used by
`MetricsObserver`. Passing the observer to the agent records one completed conversation.
You can omit the `meter` argument to let `MetricsObserver()` call `get_meter("agentloop")`
itself. `Agent(metrics=True)` creates and attaches that default observer for you.

In the table, **base** means `provider` and `model`, plus `agent_name` when one was
configured. **Outcome** means base plus `status`.

| Instrument | Kind | Attributes | Recorded value |
|---|---|---|---|
| `agentloop.conversations` | Counter | Outcome | One per conversation |
| `agentloop.conversation.duration` | Histogram | Outcome | Elapsed seconds |
| `agentloop.conversation.iterations` | Histogram | Outcome | Model calls attempted |
| `agentloop.conversation.model_duration` | Histogram | Base | Model-call seconds, including retry waits |
| `agentloop.conversation.tool_duration` | Histogram | Base, `tool` | Sum of one tool's call durations in the conversation |
| `agentloop.tool.calls` | Counter | `tool`, `error` | Successful and failed local tool calls |
| `agentloop.tokens` | Counter | Base, `type` | Reported `input`, `output`, `cache_read`, `cache_write`, or `reasoning` tokens |
| `agentloop.cost` | Counter | Base | Provider-reported USD cost |
| `agentloop.provider.retries` | Counter | Base | Attempts beyond the first |
| `agentloop.provider.errors` | Counter | Base, `error_type`, `phase`, `request_sent` | Failed provider attempts |
| `agentloop.stalls` | Counter | `tool` | Repeated tool calls that reached the configured threshold |
| `agentloop.budget_warnings` | Counter | `kind` | Configured iteration or deadline warnings |

`request_sent` is `"true"`, `"false"`, or `"unknown"`; `phase` is `"unknown"` when the
provider did not report it. Token counters skip zero or unreported values. Cost and retry
counters are recorded only when those values are present. Stall and budget-warning counters
require the corresponding [summary options](summary.md#summaryoptions).

Conversation, correlation, and parent IDs are excluded from metric attributes because a
unique value per run would create a separate metric series for every conversation. Use a
trace or [event trace](events.md) to inspect a specific conversation.

See [OpenTelemetry tracing and metrics](../guides/tracing.md#metrics) for a runnable
in-memory example.
