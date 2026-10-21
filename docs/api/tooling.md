# `agentloop.tooling`

This module defines how the agent loop runs local Python tools. A tool has a model-visible
name, description, and argument schema, plus a Python handler that does the work. When the
model requests a tool, the loop passes the call to the tool executor and sends the result
back to the model.

`ToolSpec` holds the definition and handler. `Registry` collects tools by name so the loop
can find them. `ToolDeps` supplies application data and an approval hook to the handler at
runtime. `run_tools()` executes the calls and returns a result for each one, including calls
that fail or are denied. The [`tool` decorator](agent.md#tool) creates a `ToolSpec` from a
function when you use the higher-level `Agent` API.

## Type aliases and constants

- `Handler = Callable[..., str]`: tool handler shape. Runtime results are converted with
  `str()`, so the annotation describes the intended contract.
- `ApprovalHook = Callable[[ToolCallPart], str | None]`: return `None` to allow a call or a
  reason string to deny it.
- `MAX_PARALLEL_TOOLS = 8`: maximum worker count for a multi-call response.

## `allow_all`

```text
allow_all(call) -> None
```

Default approval hook that permits every tool call.

## `ToolDeps`

Protocol exposing a read-only `before_tool: ApprovalHook` property. Application dependency
bundles may add any other fields their handlers require.

## `ToolSpec`

A frozen Pydantic model containing the model-visible definition and its Python handler.

| Field | Type | Meaning |
|---|---|---|
| `name` | non-empty `str` | Model-visible tool name |
| `description` | non-empty `str` | Model-visible instructions |
| `parameters` | `dict[str, Any]` | Bare handwritten JSON Schema |
| `handler` | `Handler` | Callable whose first argument is the deps bundle |
| `parallel_safe` | `bool` | `True`. Set `False` for a handler that is not thread-safe: a turn that calls it runs all of its tool calls one after another, in call order |
| `timeout_seconds` | `float \| None` | Per-call limit; `None` waits forever. On expiry the model gets an error result and the handler thread is abandoned (Python cannot kill it), so the handler should still set its own network timeouts |

`parameters` is sent to the model as the tool's argument schema. The executor binds the
returned arguments to the handler's Python signature; it does not independently validate
them against the full JSON Schema. Use a handler whose parameters match the schema.

## `Registry`

```text
Registry(specs)
```

Builds a flat name-to-tool mapping and caches handler signatures. Construction raises
`ValueError` for duplicate names or unreadable signatures.

- `get(name) -> ToolSpec | None`: look up a specification.
- `signature(name) -> inspect.Signature`: return the cached signature; raises `KeyError`
  with a descriptive message for an unknown name.
- `names() -> list[str]`: sorted registered names.
- `schemas() -> tuple[ToolSchema, ...]`: provider-independent model-visible schemas in registration
  order.

## `run_tools`

```text
run_tools(content, registry, deps, observer=None) -> list[ToolResultPart]
```

Executes every `ToolCallPart` in `content`. No calls returns `[]`; one runs inline. Several
calls run in a thread pool of at most eight workers unless any called tool has
`parallel_safe=False`, in which case all calls in that batch run sequentially. Results
retain call order, even when calls finish out of order, and every call produces one result.

The result is marked as an error for malformed arguments, unknown tools, approval denial,
signature mismatch, timeout, `ToolError`, approval failure, or unexpected handler failure.
Handler return values are converted to strings. Observer callbacks are guarded.

This example defines a tool, registers it, and runs one call without a model:

```python
from agentloop import Deps, Registry, ToolSpec
from agentloop.tooling import run_tools
from agentloop.transcript import ToolCallPart


def echo(deps: Deps, text: str) -> str:
    return text


spec = ToolSpec(
    name="echo",
    description="Return the supplied text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    handler=echo,
)
call = ToolCallPart(id="call-1", tool_name="echo", arguments={"text": "hello"})
result = run_tools([call], Registry([spec]), Deps())[0]
print(result.content)
print(result.is_error)
```

Output:

```text
hello
False
```

`text` is supplied through the tool call. The executor passes `Deps()` as the handler's
first argument and returns a `ToolResultPart` linked to `call-1`.
