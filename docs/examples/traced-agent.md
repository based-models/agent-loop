# Traced agent

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses OpenAI.
Install OpenAI support and the OpenTelemetry extra, then set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat,otel]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

`trace=True` attaches the library's `OTelObserver`. It sets the OpenTelemetry
[`gen_ai.operation.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
attribute to `invoke_agent` on the conversation span and `chat` on each model-request span.
These are operation values set by `agentloop`. The full span names also include the agent or
model name. An in-memory exporter lets this example inspect the spans without a collector:

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
    with Agent.from_env(
        os.environ,
        tools=[],
        name="example-agent",
        trace=True,
    ) as agent:
        print("answer:", agent.run("Reply with exactly ready."))

    spans = {
        span.attributes["gen_ai.operation.name"]: span
        for span in exporter.get_finished_spans()
    }
    print("operations:", sorted(spans))
    chat = spans["chat"]
    conversation = spans["invoke_agent"]
    print("chat is child of agent:", chat.parent.span_id == conversation.context.span_id)
finally:
    provider.shutdown()
```

Output:

```text
answer: ready
operations: ['chat', 'invoke_agent']
chat is child of agent: True
```

The example reads those operation values from the exported spans. `chat.parent.span_id` is
the ID of the model span's parent, and `conversation.context.span_id` is the conversation
span's own ID; their equality shows that the model request belongs to this conversation.
The application configures and shuts down the tracer provider. For collector export and
metrics, see the [tracing guide](../guides/tracing.md).
