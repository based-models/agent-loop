# Custom observer

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. These examples use
OpenAI. Install its extra and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

## Count tokens with a callback

An observer receives events while the agent runs. Subclass `NullObserver` and override only
the callback you need. `on_response()` runs after each successful model response:

```python
import os

from agentloop import Agent, NullObserver
from agentloop.provider import ModelResponse

class TokenCounter(NullObserver):
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def on_response(self, response: ModelResponse) -> None:
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens

counter = TokenCounter()
with Agent.from_env(os.environ, tools=[], observer=counter) as agent:
    answer = agent.run("How many days are in a week? Reply with one digit.")

print("answer:", answer)
print("input tokens:", counter.input_tokens)
print("output tokens:", counter.output_tokens)
```

Output:

```text
answer: 7
input tokens: 19
output tokens: 11
```

Passing `counter` as `observer` registers it with the agent. After each successful model
call, the agent loop calls `counter.on_response(response)` automatically; your code does
not call the method directly.

`input_tokens` and `output_tokens` are integers in `ModelResponse`. Other usage fields,
such as `cache_read_tokens`, may be `None` when the provider does not report them.

## Record JSONL events

For a ready-made event log, use `EventTraceObserver`. Its writer receives one JSONL line per
event. This example keeps those lines in memory and prints their sequence numbers and names:

```python
import json
import os

from agentloop import Agent, EventTraceObserver

lines = []
trace = EventTraceObserver(lines.append)
with Agent.from_env(os.environ, tools=[], observer=trace) as agent:
    agent.run("Reply with exactly ready.")

for line in lines:
    record = json.loads(line)
    print(json.dumps({"seq": record["seq"], "event": record["event"]}))
```

Output:

```text
{"seq": 1, "event": "conversation_start"}
{"seq": 2, "event": "request"}
{"seq": 3, "event": "response"}
{"seq": 4, "event": "conversation_end"}
```

The default trace contains metadata, without prompt or response text. See the
[event trace guide](../guides/event-trace.md) to write the lines to a file or capture
content with a redactor.
