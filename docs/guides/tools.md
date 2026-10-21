# Tools and approval

A local tool combines a handwritten JSON Schema with a Python handler. The schema is what
the model sees; `@tool` does not infer it from the function signature. These examples use
OpenAI, configured with `LLM_PROVIDER=openai` and `OPENAI_API_KEY` as in
[Hello World](../getting-started/hello-world.md).

## Run a local tool

Define the schema, decorate the function, and pass the resulting tool to `Agent`:

```python
import os

from agentloop import Agent, tool

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
    return str(left + right)

with Agent.from_env(
    os.environ,
    tools=[add],
    system="Use the add tool for arithmetic before answering.",
) as agent:
    result = agent.run_result("What is 27 plus 15? Call add to calculate it.")
    print(result.text)
    print(result.summary.tool_calls)
```

Output:

```text
27 plus 15 is 42.
{'add': 1}
```

`result.summary.tool_calls` counts calls by tool name; the full call and result are in
`result.transcript`.

## Dependencies and approval

Declare a keyword-only `deps` parameter when a tool needs trusted application state. A
`before_tool` hook can deny a call before its handler runs:

```python
import os

from agentloop import Agent, Deps, tool
from agentloop.transcript import ToolCallPart

class NoteDeps(Deps):
    notes: dict[str, str]

def approve(call: ToolCallPart) -> str | None:
    if call.arguments.get("name") == "private":
        return "private notes are unavailable"
    return None

@tool(parameters={
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
})
def read_note(name: str, *, deps: NoteDeps) -> str:
    """Read a note by name."""
    return deps.notes.get(name, "Note not found")

deps = NoteDeps(
    notes={"public": "Blue is a color.", "private": "Hidden text."},
    before_tool=approve,
)
with Agent.from_env(
    os.environ,
    tools=[read_note],
    deps=deps,
    system=(
        "Call read_note once for each request. Use the name between backticks exactly "
        "as written. If denied, explain the denial."
    ),
) as agent:
    public = agent.run_result("Read the note named `public`.")
    print(public.text)
    print(public.summary.tool_calls, public.summary.tool_errors)

    private = agent.run_result("Read the note named `private`.")
    print(private.text)
    print(private.summary.tool_calls, private.summary.tool_errors)
```

Output:

```text
Blue is a color.
{'read_note': 1} {}
I’m not allowed to access notes marked as private, so I can’t read or display the contents of the note named `private`.
{'read_note': 1} {'read_note': 1}
```

The `notes` dictionary belongs to the application; the model sees only the `name` schema.
For the private request, the approval hook returns a reason and `read_note` does not run.
The denied call still appears in `tool_calls` and `tool_errors`.

Independent tool calls in one model response run concurrently, up to eight at a time, and
their results retain the model's call order. Set `parallel_safe=False` on `@tool` if a
handler cannot run alongside others. Approval hooks may run on worker threads, so shared
state they access must be thread-safe.

See the [`tooling` API](../api/tooling.md).
