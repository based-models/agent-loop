# Agents

`Agent` keeps a model client, tools, application dependencies, and loop settings together
so they can be reused across conversations. These examples use OpenAI. Set up the provider
before running them:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

`Agent.from_env()` receives `os.environ` explicitly. The library does not read environment
variables on its own.

## One prompt

`run()` starts a new conversation and returns its final text:

```python
import os

from agentloop import Agent

with Agent.from_env(
    os.environ,
    tools=[],
    system="Answer briefly.",
    name="demo",
) as agent:
    print(agent.run("What is 2 + 2?"))
```

Output:

```text
2 + 2 = 4.
```

Each `run()` call creates its own transcript. Use `run_result()` when you also need that
transcript and a `ConversationSummary`. A `ConversationSummary` records how the run ended,
including status, timing, model and tool calls, retries, and token usage, without storing
the conversation text.

## Continue a conversation

`converse()` advances a `Transcript` you supply. It adds messages to that same object and
returns it, so the next call includes the earlier messages:

```python
import os

from agentloop import Agent, Transcript, final_text

transcript = Transcript()

with Agent.from_env(os.environ, tools=[]) as agent:
    transcript.add_user_message("My name is Ada.")
    returned = agent.converse(transcript)
    print(returned is transcript)
    print(final_text(transcript))
    print(f"messages: {len(transcript.messages)}")

    transcript.add_user_message("What is my name?")
    agent.converse(transcript)
    print(final_text(transcript))
    print(f"messages: {len(transcript.messages)}")
```

Output:

```text
True
Nice to meet you, Ada. How can I help you today?
messages: 2
Your name is Ada.
messages: 4
```

The first printed value is `True`: `converse()` returns the updated transcript. With no
tools, the transcript has two messages after the first turn and four after the second.
`converse()` also runs registered local tools and appends their calls and results when the
model requests them. Use `converse_result()` for the updated transcript and its summary.

## Client ownership

`Agent.from_env()` constructs a provider client from a mapping you supply. The returned
agent owns that client: a `with` block or `agent.close()` closes it, including when a run
raises an exception.

An agent constructed with an existing client borrows it. Close that client yourself:

```python
import os

from agentloop import Agent, Settings, build_client

client = build_client(Settings.from_env(os.environ))
try:
    with Agent([], client=client) as agent:
        print(agent.run("Say hello."))
finally:
    client.close()
```

Output:

```text
Hello.
```

The agent's context manager leaves this borrowed client open. This allows several agents
to reuse one client without one agent closing it for the others.

`trace=True` attaches an `OTelObserver`. A traced agent supports one active conversation at
a time; create separate traced agents for concurrent conversations.

See the [`agent` API](../api/agent.md).
