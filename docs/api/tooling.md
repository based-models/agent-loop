# `agentloop.tooling`

Tool types, registration, approval, and execution.

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

A frozen Pydantic model containing the model-visible definition and handler.

| Field | Type | Meaning |
|---|---|---|
| `name` | non-empty `str` | Model-visible tool name |
| `description` | non-empty `str` | Model-visible instructions |
| `parameters` | `dict[str, Any]` | Bare handwritten JSON Schema |
| `handler` | `Handler` | Callable whose first argument is the deps bundle |
| `parallel_safe` | `bool` | `True`. Set `False` for a handler that is not thread-safe: a turn that calls it runs all of its tool calls one after another, in call order |
| `timeout_seconds` | `float \| None` | Per-call limit; `None` waits forever. On expiry the model gets an error result and the handler thread is abandoned (Python cannot kill it), so the handler should still set its own network timeouts |

```python
def ping(deps: ToolDeps) -> str:
    return "pong"

spec = ToolSpec(
    name="ping",
    description="Return pong.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=ping,
)
```

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

Executes every `ToolCallPart` in `content`. No calls returns `[]`; one runs inline; several
run in a thread pool. Results always retain call order and every call produces exactly one
result.

The result is marked as an error for malformed arguments, unknown tools, approval denial,
signature mismatch, `ToolError`, approval failure, or unexpected handler failure. Observer
callbacks are guarded.
