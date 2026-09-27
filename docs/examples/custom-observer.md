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
