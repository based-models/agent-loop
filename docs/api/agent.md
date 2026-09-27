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

The separation is important: the model sees a tool's name, description, and JSON Schema,
but it never sees `Deps`. Values chosen by the model belong in the schema; trusted values
supplied by the application belong in `Deps`.

```python
import datetime as dt
import os

import yfinance as yf

from agentloop import Agent, Deps, tool

class MarketDeps(Deps):
    use_adjusted_prices: bool = True

@tool(
    parameters={
        "type": "object",
        "properties": {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "date": {"type": "string", "format": "date"},
        },
        "required": ["tickers", "date"],
        "additionalProperties": False,
    }
)
def closing_prices(tickers: list[str], date: str, *, deps: MarketDeps) -> str:
    """Return closing prices for one or more stock tickers on a date."""
    trading_day = dt.date.fromisoformat(date)
    next_day = trading_day + dt.timedelta(days=1)
    prices: dict[str, float | None] = {}

    for ticker in tickers:
        history = yf.Ticker(ticker).history(
            start=trading_day,
            end=next_day,
            auto_adjust=deps.use_adjusted_prices,
        )
        prices[ticker] = None if history.empty else float(history["Close"].iloc[0])

    return str(prices)

deps = MarketDeps(use_adjusted_prices=True)
with Agent.from_env(os.environ, tools=[closing_prices], deps=deps) as agent:
    answer = agent.run("What did AAPL and MSFT close at on 2025-01-02?")
```

Here, `tickers` and `date` are model-provided inputs. Whether to adjust prices is an
application policy, so it is injected through `deps` and omitted from the model-visible
tool schema. This example requires the separately installed `yfinance` package; it is not
an `agentloop` dependency.

## When to use `Deps`

Omit `deps` when the agent has no tools, or when every tool can do its work using only its
model-provided arguments. `Agent` supplies an empty default `Deps` bundle in that case.

Provide `Deps` when tools need either:

- shared application state or configuration, such as a tenant ID, repository, API client,
  database handle, or request context; or
- a `before_tool` hook that approves or denies tool calls before execution.

Subclass `Deps` to declare application-specific fields, then add a keyword-only `deps`
parameter only to handlers that need the bundle. Do not put ordinary model-selected tool
arguments in `Deps`; doing so hides them from the tool schema and prevents the model from
supplying them.

The bundle is attached to the `Agent`, not created for each call. If its fields contain
mutable or request-specific state, manage that lifetime deliberately. Also remember that
multiple tool calls may execute concurrently, so shared dependencies and approval hooks
must be safe for concurrent use.

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
tool(parameters, *, name=None, description=None)
```

Decorator that converts a callable into a [`ToolSpec`](tooling.md#toolspec).

- `parameters`: required, handwritten JSON Schema for model-visible arguments.
- `name`: model-visible name; defaults to the function name.
- `description`: defaults to the first paragraph of the docstring. If neither exists,
  decoration raises `ValueError`.
- A keyword-only parameter named `deps` receives the configured dependency bundle and is
  hidden from the model-visible handler signature and JSON Schema. The same bundle is
  available to every tool registered with the agent, though a handler can omit the
  parameter when it does not need it.

```python
from agentloop import tool

@tool(
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
)
def greet(name: str) -> str:
    """Greet a person."""
    return f"Hello, {name}!"
```

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
)
```

Public attribute: `name`, the application-level agent name. Other constructor values are
kept as internal state.

`client` is borrowed: an agent constructed directly never closes it. `tools` must be a
sequence of `ToolSpec`; values produced by `@tool` can be passed directly. Duplicate names
fail during construction. `deps` is the one runtime dependency bundle passed to registered
tool handlers, defaulting to `Deps()`. `trace=True` composes an `OTelObserver` with the
supplied observer.

### `Agent.from_env`

```text
Agent.from_env(env, tools, **agent_options) -> Agent
```

Builds `Settings`, creates a retrying provider client, and returns an agent that owns that
client. `env` is an explicit mapping such as `os.environ`. The method does not load dotenv
files. `observer` and `trace` are connected to both retries and loop events.

### `Agent.run`

```text
agent.run(prompt) -> str
```

Runs one new conversation and returns text from the final assistant message. Its transcript
is discarded.

### `Agent.converse`

```text
agent.converse(transcript) -> Transcript
```

Advances, mutates, and returns the supplied transcript.

### `Agent.close`

```text
agent.close() -> None
```

Closes only a client created by `from_env()`. It is safe to use `Agent` as a context manager;
`__enter__()` returns the agent and `__exit__()` calls `close()`.

A traced `Agent` is not reentrant. Concurrent traced runs require separate instances.
