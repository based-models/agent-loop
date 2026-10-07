# Custom observer

```python
import os

from agentloop import Agent, MultiObserver, NullObserver
from agentloop.provider import ModelResponse

class TokenCounter(NullObserver):
    def __init__(self) -> None:
        self.total = 0

    def on_response(self, response: ModelResponse) -> None:
        self.total += response.usage.input_tokens
        self.total += response.usage.output_tokens

counter = TokenCounter()
observer = MultiObserver(counter)

with Agent.from_env(os.environ, tools=[], observer=observer) as agent:
    print(agent.run("Hello"))

print(f"reported tokens: {counter.total}")
```

Subclassing `NullObserver` keeps the implementation compatible with callbacks it does not
need.

## A JSONL trace of every conversation

`EventTraceObserver` is a ready-made observer that writes one JSON line per event through a
callable you choose. It is content-free unless you ask, and then it needs a redactor:

```python
import re
from agentloop import Agent, EventTraceObserver

def redact(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9]{20,}", "[KEY]", text)

with open("trace.jsonl", "a") as log:
    trace = EventTraceObserver(log.write, capture_content=True, redact=redact)
    agent = Agent(tools, client=client, observer=trace)
    agent.run("find the lead investor", correlation_id="round-42")
```

The last line of each conversation is `conversation_end`, carrying the full summary. Use one
`EventTraceObserver` per concurrent conversation. See the [event trace guide](../guides/event-trace.md).
