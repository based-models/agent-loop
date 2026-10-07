# Agents

`Agent` keeps a model client, tools, application dependencies, and loop settings together so
they can be reused across conversations.

```python
import os

from agentloop import Agent

with Agent.from_env(
    os.environ,
    tools=[],
    system="Be concise.",
    name="demo",
) as agent:
    answer = agent.run("What is 2 + 2?")
    print(answer)
```

`run()` creates a transcript internally and returns only the final text. Use `converse()`
when you want to keep the messages and continue the same conversation:

```python
import os

from agentloop import Agent, Transcript, final_text

transcript = Transcript()

with Agent.from_env(os.environ, tools=[]) as agent:
    transcript.add_user_message("My name is Ada.")
    returned = agent.converse(transcript)

    print(returned is transcript)       # True
    print(final_text(transcript))       # For example: "Nice to meet you, Ada."
    print(len(transcript.messages))     # 2: one user message and one reply

    transcript.add_user_message("What is my name?")
    agent.converse(transcript)

    print(final_text(transcript))       # For example: "Your name is Ada."
    print(len(transcript.messages))     # 4: both turns are still present
```

`converse()` appends the model reply—and any intermediate tool calls and results—to the
provided `Transcript`. It returns that exact object for convenience; it does not create a
copy. This lets the next call include everything the model has already seen.

## Who closes the provider client?

`Agent.from_env()` constructs a provider client for you, so the returned agent owns that
client. Use a context manager, as above, or call `agent.close()` when finished. Leaving the
context closes the provider's connection pool even if the conversation raises an exception.

When you construct an agent with an existing client, ownership stays with your application:

```python
from agentloop import Agent, Settings, build_client

client = build_client(Settings(provider="anthropic"))
try:
    agent = Agent([], client=client)
    print(agent.run("Hello"))
finally:
    client.close()  # The caller created the client, so the caller closes it.
```

Calling `agent.close()` in this second example would deliberately leave `client` open. That
makes it possible to reuse one client across several agents without one agent unexpectedly
closing it for the others.

`trace=True` attaches an `OTelObserver`. A traced agent supports only one conversation at a
time; create one traced agent per concurrent conversation.

See the [`agent` API](../api/agent.md).
