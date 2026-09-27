# Tools and approval

A tool combines a handwritten JSON Schema with a Python handler. The schema is part of the
prompt contract and is never inferred from the function signature.

```python
from agentloop import ToolError, tool

ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "left": {"type": "integer"},
        "right": {"type": "integer"},
    },
    "required": ["left", "right"],
    "additionalProperties": False,
}

@tool(parameters=ADD_SCHEMA)
def add(left: int, right: int) -> str:
    """Add two integers."""
    if abs(left) > 1_000_000 or abs(right) > 1_000_000:
        raise ToolError("numbers must be at most one million")
    return str(left + right)
```

Pass `[add]` to `Agent`. `ToolError` is safe feedback for the model. Unexpected exceptions
are logged and converted to an error result so every call still receives a result.

## Dependencies and approval

Declare a keyword-only `deps` parameter when a tool needs application state:

```python
from agentloop import Deps, ToolCallPart, tool

class AppDeps(Deps):
    database_url: str

def approve(call: ToolCallPart) -> str | None:
    if call.tool_name == "delete_record":
        return "destructive tools are disabled"
    return None

@tool(parameters={"type": "object", "properties": {}, "additionalProperties": False})
def database_name(*, deps: AppDeps) -> str:
    """Return the configured database URL."""
    return deps.database_url

deps = AppDeps(database_url="sqlite:///app.db", before_tool=approve)
```

Multiple tool calls in one response execute concurrently, up to
`MAX_PARALLEL_TOOLS`, while results retain call order. Approval hooks may therefore run on
worker threads and should be thread-safe.

See the [`tooling` API](../api/tooling.md).
