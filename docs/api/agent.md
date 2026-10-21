# `agentloop.agent`

The high-level assembly layer for running an agent.

An `Agent` is a configured conversation runner. It keeps together the parts that should stay
the same across calls:

- the model `client` and system prompt,
- the tools the model may call,
- the application dependencies and approval policy used to execute those tools, and
- limits, observation, and tracing settings.

This is why `Agent`, `tool`, and `Deps` live in the same module. The `tool` decorator defines
a capability that can be offered to the model. `Deps` holds runtime-only state and policy
that the capability needs when it executes. `Agent` registers the tools and passes its deps
bundle to each tool call.

The model sees a tool's name, description, and JSON Schema, but it never sees `Deps`.
Values chosen by the model belong in the schema; application data belongs in `Deps`.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses OpenAI.

Install OpenAI support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

The model chooses a note name. The application supplies the notes through `Deps`:

```python
import os

from agentloop import Agent, Deps, tool

class NoteDeps(Deps):
    notes: dict[str, str]

@tool(
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
)
def read_note(name: str, *, deps: NoteDeps) -> str:
    """Read a note by name."""
    return deps.notes.get(name, "Note not found.")

deps = NoteDeps(notes={"welcome": "Hello from the agent loop."})
with Agent.from_env(os.environ, tools=[read_note], deps=deps) as agent:
    result = agent.run_result(
        "Read the welcome note with read_note. Reply with its text only."
    )
    print(result.text)
    print(result.summary.tool_calls)
```

Output:

```text
Hello from the agent loop.
{'read_note': 1}
```

`tool_calls` confirms the agent ran the tool.

## When to use `Deps`

Omit `deps` when the agent has no tools, or when every tool can do its work using only its
model-provided arguments. `Agent` supplies an empty default `Deps` bundle in that case.

Provide `Deps` when tools need either:

- shared application state or configuration, such as a tenant ID, repository, API client,
  database handle, or request context; or
- a `before_tool` hook that approves or denies tool calls before execution.

The `read_note` example shows the two kinds of input:

- `name` is in the tool's JSON Schema. The model chooses it when calling `read_note`.
- `notes` is declared on `NoteDeps` and passed to the agent by the application. The model
  can ask for a note by name, but cannot supply or replace the notes dictionary.

Define a `Deps` subclass for the application values a tool needs. Add `*, deps: NoteDeps`
to a handler that reads those values, as `read_note` does. A handler that needs only its
model-chosen arguments can leave out `deps`. Keep those arguments in the JSON Schema so
the model knows what it can pass. For example, `read_note` lists `name` under `properties`
and `required`, so the model knows to supply a note name when it calls the tool.

The agent keeps the same `deps` object for every run and every tool call. For example, two
calls to `agent.run_result(...)` would use the same `notes` dictionary. If the notes differ
by request, create an agent with the appropriate `NoteDeps` for each request. If a tool
changes shared data, account for the fact that multiple tool calls can run concurrently;
the same applies to a custom `before_tool` approval hook.

## `Deps`

```text
Deps(*, before_tool=allow_all)
```

A frozen Pydantic model implementing [`ToolDeps`](tooling.md#tooldeps). It is the default
runtime dependency bundle used by `Agent`.

`before_tool` is called before every tool execution. It receives the requested
`ToolCallPart`; return `None` to allow the call or a reason string to deny it. The default,
`allow_all`, permits every call. Subclass `Deps` to add typed fields needed by your tool
handlers.

## `tool`

```text
tool(parameters, *, name=None, description=None, timeout_seconds=None, parallel_safe=True)
```

Decorator that converts a callable into a [`ToolSpec`](tooling.md#toolspec).

- `parameters`: required, handwritten JSON Schema for model-visible arguments.
- `name`: model-visible name; defaults to the function name.
- `description`: defaults to the first paragraph of the docstring. If neither exists,
  decoration raises `ValueError`.
- `timeout_seconds`: optional positive limit for a local tool call.
- `parallel_safe`: set to `False` to run local tool calls in a turn sequentially.
- A keyword-only parameter named `deps` receives the configured dependency bundle and is
  hidden from the model-visible handler signature and JSON Schema. The same bundle is
  available to every tool registered with the agent, though a handler can omit the
  parameter when it does not need it.

## `Agent`

```text
Agent(
    tools,
    *,
    client,
    system=None,
    deps=None,
    observer=None,
    name="agentloop",
    trace=False,
    max_tokens=16_000,
    max_iterations=50,
    deadline_seconds=600.0,
    summary_options=None,
    metrics=False,
)
```

Public attribute: `name`, the application-level agent name. Other constructor values are
kept as internal state.

`client` is borrowed: an agent constructed directly never closes it. `tools` accepts local
`ToolSpec` objects and provider-run `HostedTool` objects. Values produced by `@tool` can be
passed directly. Construction fails for duplicate local tool names, hosted tools the client
does not support, or a local tool whose name matches a hosted tool passed to the same agent
(for example, both named `web_search`).

`deps` is the one runtime dependency bundle passed to local tool handlers, defaulting to
`Deps()`. `trace=True` combines an `OTelObserver` with the supplied observer. `metrics=True`
adds a `MetricsObserver`. `summary_options` configures the optional timeline, repeated-call
detection, and limit warnings described in
[Conversation summaries](../guides/summaries.md).

### `Agent.from_env`

```text
Agent.from_env(env, tools, *, http_client=None, **agent_options) -> Agent
```

Builds `Settings`, creates a retrying provider client, and returns an agent that owns that
client. `env` is an explicit mapping such as `os.environ`. The method does not load dotenv
files. `observer` and `trace` are connected to both retries and loop events.

`http_client` is passed to [`build_client`](config.md#build_client): an `httpx2.Client` for
Anthropic, an `httpx.Client` for the other providers, or `ConfigurationError`. The agent's
`close()` closes the provider client it built but leaves your `http_client` open.

### `Agent.run`

```text
agent.run(
    prompt,
    *,
    conversation_id=None,
    correlation_id=None,
    parent_id=None,
) -> str
```

Runs one new conversation and returns text from the final assistant message. Its transcript
is discarded. The optional identifiers are copied to the conversation summary and trace.

### `Agent.run_result`

Accepts the same arguments as `run()` and returns a `ConversationResult` containing the
final transcript, text, and summary.

### `Agent.converse`

```text
agent.converse(
    transcript,
    *,
    conversation_id=None,
    correlation_id=None,
    parent_id=None,
) -> Transcript
```

Updates and returns the supplied transcript. The optional identifiers have the
same meaning as in `run()`.

### `Agent.converse_result`

Accepts the same arguments as `converse()` and returns a `ConversationResult` containing the
transcript, text, and summary.

### `Agent.close`

```text
agent.close() -> None
```

Closes only a client created by `from_env()`. It is safe to use `Agent` as a context manager;
`__enter__()` returns the agent and `__exit__()` calls `close()`.

A traced `Agent` supports one active conversation at a time. Concurrent traced runs require
separate agent instances.
