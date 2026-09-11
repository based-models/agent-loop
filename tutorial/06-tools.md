# 06 — Tools

Four tools, a flat registry, and one function that must never raise.

Builds `src/harness/tools.py`. **Zero third-party imports.**

---

## First, somewhere to report progress

`run_tools` needs to tell the outside world that a tool started and finished — otherwise
a 30-second `bash` call is 30 seconds of silence. So before the registry, one small
module: `src/harness/observer.py`.

```python
class LoopObserver(Protocol):
    """Every interesting thing that happens, as callbacks. Implementations may no-op."""

    def conversation(self) -> AbstractContextManager[None]: ...
    def on_request(self, request: ModelRequest) -> None: ...
    def on_response(self, response: ModelResponse) -> None: ...
    def on_retry(self, attempt: int, error: Exception) -> None: ...
    def on_tool_start(self, call: ToolCallPart) -> None: ...
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: ...

    # Reserved. Unused in v1 — the streaming / TUI hook (chapter 12).
    def on_text_delta(self, text: str) -> None: ...


class NullObserver:
    """Does nothing. The default, so nothing downstream needs a None check."""

    def conversation(self) -> AbstractContextManager[None]: return nullcontext()
    def on_request(self, request: ModelRequest) -> None: pass
    def on_response(self, response: ModelResponse) -> None: pass
    def on_retry(self, attempt: int, error: Exception) -> None: pass
    def on_tool_start(self, call: ToolCallPart) -> None: pass
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: pass
    def on_text_delta(self, text: str) -> None: pass
```

Only `on_tool_start` and `on_tool_end` matter in this chapter; the rest are here because
splitting one small protocol across two chapters would be worse than showing it whole.
**Chapter 7 explains why it is shaped like this** — a callback protocol rather than the
reference course's async generator — and chapter 10 turns it into OpenTelemetry spans
without touching a line of the loop.

Note the enter/exit pairing: `on_tool_start` / `on_tool_end`, `on_request` /
`on_response`. That is not decoration. It is exactly the shape a trace span needs.

---

## The registry

```python
Handler: TypeAlias = Callable[..., str]
ApprovalHook: TypeAlias = Callable[[ToolCallPart], str | None]


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, Any]          # bare JSON Schema, strict-compatible
    handler: Handler


def _allow_all(call: ToolCallPart) -> str | None:
    return None


class Deps(BaseModel):
    """What every tool handler needs. Passed explicitly — cf. RunContext (D6)."""

    model_config = ConfigDict(frozen=True)

    cwd: Path
    before_tool: ApprovalHook = _allow_all


class Registry:
    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        self._by_name = {s.name: s for s in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(name=s.name, description=s.description, parameters=s.parameters)
            for s in self._by_name.values()
        )

    def names(self) -> list[str]:
        return sorted(self._by_name)
```

> **Python note.** `Callable[..., str]` is the type of "a function that returns a
> string" — the `...` means "any arguments". `ToolSpec.handler` holds the function
> itself, not a call to it, which is why the registry can look one up by name and call
> it later. Pydantic accepts callable fields as-is: it checks the value *is* callable and
> then leaves it alone.
>
> Note `_allow_all` is a named function rather than `lambda call: None` as a default.
> A named default is what makes the model reproducible — two `Deps()` instances compare
> equal, and the field shows a readable name in a repr instead of `<lambda>`.

`Path` gets real validation here for free: pydantic coerces a string to a `Path`, so
`Deps(cwd="/tmp/x")` works and still gives you a `Path` object downstream.

A dict. No plugin machinery, no entry points, no decorator registry with import-time side effects — the reference course's flat `ToolSpec` list is right, and we keep it.

`Deps` is divergence **D6**: the idea of a typed bundle threaded into tool execution is good; the `RunContext[AgentDeps]` generic that carries it isn't worth a framework import in every tool module.

---

## Schemas are written by hand — and that's the point

Divergence **D7**. Three reasons, in increasing order of importance:

1. **No dependency.** Core stays at zero third-party imports.
2. **Generated schemas are wrong for this.** `model_json_schema()` emits `$defs` / `$ref` / `anyOf` for anything non-trivial. OpenAI strict mode rejects parts of that, and several vLLM tool-call parsers choke on it — so you'd post-process the output, which is more work than writing the dict.
3. **The schema is prompt engineering.** The `description` is what the model reads to decide whether to call your tool and with what. You want it word for word. You also want it **byte-stable**: tool definitions render *first* in the prompt-cache prefix, so a schema whose key order shuffles between runs silently costs you every cache hit.

### One dialect, both providers

Author every schema in the strict-compatible dialect and the same dict is valid for Anthropic and OpenAI-compatible servers:

- `"additionalProperties": false` on every object
- **every** property listed in `required`
- optional parameters expressed as a nullable union: `"type": ["string", "null"]`

That last rule is the one that feels wrong. Under `strict`, "optional" doesn't mean "absent from `required`" — it means "required, and may be null."

```python
READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file, relative to the working directory.",
        },
        "offset": {
            "type": ["integer", "null"],
            "description": "1-indexed line to start from. Null reads from the top.",
        },
        "limit": {
            "type": ["integer", "null"],
            "description": "Maximum lines to return. Null uses the default of 2000.",
        },
    },
    "required": ["path", "offset", "limit"],
    "additionalProperties": False,
}
```

---

## The worked example: `read`

```python
DEFAULT_READ_LIMIT = 2000
MAX_RESULT_BYTES = 50_000


def read(deps: Deps, path: str, offset: int | None = None,
         limit: int | None = None) -> str:
    target = _resolve(deps.cwd, path)            # containment — see below
    if not target.is_file():
        raise ToolError(f"{path}: not a file")

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max((offset or 1) - 1, 0)
    end = start + (limit or DEFAULT_READ_LIMIT)
    window = lines[start:end]

    body = "\n".join(f"{start + i + 1:6d}\t{line}" for i, line in enumerate(window))
    if end < len(lines):
        body += f"\n... ({len(lines) - end} more lines; use offset={end + 1})"
    return _truncate(body, MAX_RESULT_BYTES)


READ = ToolSpec(
    name="read",
    description=(
        "Read a file from the working directory. Returns the contents with "
        "1-indexed line numbers. Reads at most 2000 lines by default; use offset "
        "and limit to page through a larger file."
    ),
    parameters=READ_SCHEMA,
    handler=read,
)
```

Four things generalize to every tool:

**Line numbers in the output.** Costs a few tokens, and lets the model reference `file.py:42` and target edits precisely. Nearly every production coding agent does this.

**Truncation with a next step.** `"... (N more lines; use offset=...)"` tells the model both that it was truncated and how to continue. `"[truncated]"` alone teaches it nothing.

**A byte cap, not just a line cap.** One minified file will blow your context window in a single call.

**`errors="replace"`.** A `UnicodeDecodeError` from a stray binary file should not end the turn.

The other three — `write`, `edit`, `bash` — follow the same shape:

| Tool | Contract | The thing that matters |
|---|---|---|
| `write` | Create or overwrite; create parent dirs | Write to a temp file and `os.replace` — a crash mid-write must not leave a truncated source file |
| `edit` | Exact-match string replacement | Raise if `old_string` is **not found** *or* appears **more than once**. Ambiguity must be an error, never a silent first-match |
| `bash` | Run a command, return stdout+stderr+exit code | `timeout=` always. Return the exit code in the text — a non-zero exit is information the model needs, not a failure to hide |

### Path containment is mandatory

```python
def _resolve(cwd: Path, raw: str) -> Path:
    base = cwd.resolve()
    target = (base / raw).resolve()          # .resolve() also follows symlinks
    if target != base and base not in target.parents:
        raise ToolError(f"{raw!r} resolves outside the working directory")
    return target
```

Every file tool, no exceptions. `..` traversal, absolute paths, and in-tree symlinks pointing outward all collapse into this one check because `.resolve()` normalizes all three.

This is not a sandbox — `bash` can still do whatever the shell can do. It's the cheap 80% that stops a confused model from reading `~/.ssh/id_rsa` because a path looked plausible. Real isolation is chapter 12.

---

## `run_tools` is a total function

The spine of the chapter. "Total" is the mathematical sense: the function returns a value
for *every* possible input, instead of raising for some of them. Whatever the model
sends — a misspelled tool name, broken JSON, arguments that crash the handler — a result
comes back.

> **Exactly one `ToolResultPart` per `ToolCallPart`. Same count, same IDs, same order. Errors become `is_error=True` results — never omissions, never exceptions.**

`ToolError` already exists — chapter 3 added it to `errors.py`. It is what a handler
raises when it fails in a way the *model* should see and can act on: a missing file, a
path outside the working directory, a string that `edit` could not find. Anything else a
handler raises is a bug, and is caught just as carefully a few lines below.

```python
def run_tools(content: Sequence[ContentPart], registry: Registry, deps: Deps,
              observer: LoopObserver) -> list[ToolResultPart]:
    calls = [p for p in content if isinstance(p, ToolCallPart)]
    if not calls:
        return []
    if len(calls) == 1:
        return [_run_one(calls[0], registry, deps, observer)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_run_one, c, registry, deps, observer) for c in calls]
        return [f.result() for f in futures]     # list(...) preserves submit order
```

```python
def _run_one(call: ToolCallPart, registry: Registry, deps: Deps,
             observer: LoopObserver) -> ToolResultPart:
    observer.on_tool_start(call)
    result = _execute(call, registry, deps)
    observer.on_tool_end(call, result)
    return result


def _execute(call: ToolCallPart, registry: Registry, deps: Deps) -> ToolResultPart:
    def fail(msg: str) -> ToolResultPart:
        return ToolResultPart(call_id=call.id, name=call.name,
                              content=msg, is_error=True)

    # F4: the adapter couldn't parse the arguments
    if call.parse_error is not None:
        return fail(f"Could not parse arguments as JSON ({call.parse_error}). "
                    f"Received: {call.raw_arguments!r}. Re-issue with valid JSON.")

    # F3: a tool that doesn't exist
    spec = registry.get(call.name)
    if spec is None:
        return fail(f"Unknown tool {call.name!r}. Available: {registry.names()}")

    # The one in-scope safety seam
    denial = deps.before_tool(call)
    if denial is not None:
        return fail(f"Denied: {denial}")

    # Argument validation, with no dependencies
    try:
        bound = inspect.signature(spec.handler).bind(deps, **call.arguments)
    except TypeError as e:
        return fail(f"Invalid arguments for {call.name}: {e}")

    try:
        return ToolResultPart(call_id=call.id, name=call.name,
                              content=str(spec.handler(*bound.args, **bound.kwargs)))
    except ToolError as e:
        return fail(f"Error: {e}")
    except Exception as e:                      # deliberately broad — see below
        logger.exception("tool %s raised", call.name)
        return fail(f"Error: {type(e).__name__}: {e}")
```

### Why the bare `except Exception` is correct here

Normally a red flag. Here it's the invariant.

If a handler raises past `run_tools`, one of two things happens. Either the turn dies — and the user loses the work — or the loop catches it upstream and continues with one fewer result than there were calls. That second case produces:

```
400 `tool_use` ids were found without `tool_result` blocks immediately after:
toolu_01Abc... Each `tool_use` block must have a corresponding `tool_result`
block in the next message.
```

on the **next** request, with nothing pointing at the handler that raised. This is the single most confusing failure in the whole system: the error is a 400 from a remote server, the cause is a `KeyError` in your `edit` tool three seconds earlier, and nothing connects them.

So: every failure path produces a result. The `logger.exception` keeps the traceback for you; the model gets a sentence it can act on.

Chapter 2's `add_tool_results` asserts the pairing, so a regression here is caught by a local `TranscriptError` rather than by a remote 400.

### Four failure paths, four normal results

| Failure | What the model sees |
|---|---|
| `parse_error` set (F4) | The decoder message and the raw string, plus "re-issue with valid JSON" |
| Unknown tool (F3) | The bad name **and the list of real ones** |
| Denied by hook | The reason |
| Handler raised | Exception type and message |

Models self-correct from all four remarkably reliably — *if* the message says what to do next. `"Error"` doesn't. `"Unknown tool 'read_file'. Available: ['bash', 'edit', 'read', 'write']"` gets a correct call on the next turn essentially every time.

### Validation without a dependency

```python
inspect.signature(spec.handler).bind(deps, **call.arguments)
```

> **Python note.** `inspect.signature(fn)` reads a function's parameter list at runtime.
> `.bind(...)` tries to match arguments to parameters exactly as a real call would —
> but without calling anything — and raises `TypeError` if they don't fit. So this is a
> dry run of the call. `**call.arguments` unpacks the dict into keyword arguments:
> `{"path": "a.py"}` becomes `path="a.py"`.

One line. Catches missing required parameters and unexpected extra ones, raising `TypeError` with a readable message — no `jsonschema`, no Pydantic. It doesn't check *types*, which is what `strict: true` is for on the wire and what the handler's own guards are for underneath.

### Parallel execution, ordered results

Anthropic can emit several `tool_use` blocks in one turn, and does — two `read` calls on different files is the common case. Running them sequentially wastes the parallelism the model just expressed.

> **Python note — `ThreadPoolExecutor`.** `pool.submit(fn, arg)` starts `fn(arg)` on a
> background thread and immediately returns a `Future` — a handle to a result that isn't
> ready yet. `future.result()` waits for it and returns the value (or re-raises whatever
> the function raised). The `with` block waits for everything to finish before exiting.

`ThreadPoolExecutor` is the right tool here. Python threads can't run CPU work in
parallel — the interpreter only lets one thread execute Python bytecode at a time, which
is what people mean by "the GIL". But that lock is *released* while a thread waits on
disk, a subprocess, or the network, so threads do give you real parallelism for work
that is mostly waiting. Reading four files and running a shell command is exactly that. And `[f.result() for f in futures]` returns in **submit order**, not completion order.

Ordering matters twice over: Anthropic wants `tool_result` blocks in call order, and chapter 5's OpenAI fan-out emits `role: "tool"` messages positionally. Chapter 2's `add_tool_results` re-sorts as a second line of defense, but getting it right here means the sort is a no-op.

The `len(calls) == 1` fast path skips the pool for the common case — most turns call one tool, and a thread pool for one function call is pure overhead in every stack trace you'll read.

### The approval hook

```python
before_tool: Callable[[ToolCallPart], str | None] = lambda call: None
```

Return `None` to allow, or a string reason to deny. Default allows everything.

This is the one exception to chapter 0's scope rule, and it's four lines. A real gate — allow/ask/deny by tool kind, prompting the user, remembering answers — is chapter 12. But a `bash` tool with no gate *at all* is not a shape worth internalizing, and having the seam here means adding the policy later touches nothing else.

A worked example you can actually use:

```python
def deny_destructive(call: ToolCallPart) -> str | None:
    if call.name == "bash":
        cmd = call.arguments.get("command", "")
        if any(p in cmd for p in ("rm -rf /", ":(){", "mkfs", "dd if=")):
            return "This command looks destructive. Refusing."
    return None
```

Substring matching is not a security boundary and you should not mistake it for one — it's a guardrail against an obvious accident. The boundary is a sandbox.

---

## Checkpoint

`tests/test_ch06_tools.py`:

```python
import pytest
from harness.tools import Deps, Registry, ToolSpec, ToolError, run_tools, READ
from harness.transcript import ToolCallPart
from harness.observer import NullObserver


def _reg(*specs):
    return Registry(list(specs) or [READ])


def test_every_call_gets_exactly_one_result(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = [
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
        ToolCallPart(id="c2", name="nope", arguments={}),                 # F3
        ToolCallPart(id="c3", name="read", arguments={},
                     parse_error="Expecting value"),                      # F4
    ]
    out = run_tools(calls, _reg(), Deps(cwd=tmp_path), NullObserver())
    assert [r.call_id for r in out] == ["c1", "c2", "c3"]    # same count, same order
    assert [r.is_error for r in out] == [False, True, True]


def test_a_raising_handler_becomes_an_error_result(tmp_path):
    def boom(deps): raise RuntimeError("kaboom")
    spec = ToolSpec(name="boom", description="d",
                    parameters={"type": "object", "properties": {},
                                "required": [], "additionalProperties": False},
                    handler=boom)
    out = run_tools([ToolCallPart(id="c1", name="boom", arguments={})],
                    _reg(spec), Deps(cwd=tmp_path), NullObserver())
    assert len(out) == 1 and out[0].is_error
    assert "kaboom" in out[0].content        # and it did NOT propagate


def test_unknown_tool_message_lists_the_real_ones(tmp_path):
    out = run_tools([ToolCallPart(id="c1", name="read_file", arguments={})],
                    _reg(), Deps(cwd=tmp_path), NullObserver())
    assert "read" in out[0].content          # the model can self-correct from this


def test_bad_arguments_are_an_error_result_not_a_crash(tmp_path):
    out = run_tools([ToolCallPart(id="c1", name="read", arguments={"nope": 1})],
                    _reg(), Deps(cwd=tmp_path), NullObserver())
    assert out[0].is_error and "Invalid arguments" in out[0].content


def test_path_containment(tmp_path):
    out = run_tools([ToolCallPart(id="c1", name="read",
                                  arguments={"path": "../../etc/passwd"})],
                    _reg(), Deps(cwd=tmp_path), NullObserver())
    assert out[0].is_error and "outside" in out[0].content


def test_before_tool_hook_denies(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    deps = Deps(cwd=tmp_path, before_tool=lambda c: "not today")
    out = run_tools([ToolCallPart(id="c1", name="read", arguments={"path": "a.py"})],
                    _reg(), deps, NullObserver())
    assert out[0].is_error and "not today" in out[0].content


def test_parallel_results_keep_call_order(tmp_path):
    import time
    def slow(deps, ms: int):
        time.sleep(ms / 1000); return str(ms)
    spec = ToolSpec(name="slow", description="d",
                    parameters={"type": "object",
                                "properties": {"ms": {"type": "integer"}},
                                "required": ["ms"], "additionalProperties": False},
                    handler=slow)
    calls = [ToolCallPart(id=f"c{i}", name="slow", arguments={"ms": ms})
             for i, ms in enumerate([120, 10, 60])]      # finish out of order
    out = run_tools(calls, _reg(spec), Deps(cwd=tmp_path), NullObserver())
    assert [r.call_id for r in out] == ["c0", "c1", "c2"]
    assert [r.content for r in out] == ["120", "10", "60"]


def test_schemas_are_strict_compatible():
    for tool in _reg().schemas():
        assert tool.parameters["additionalProperties"] is False
        assert set(tool.parameters["required"]) == set(tool.parameters["properties"])
```

```bash
pytest tests/test_ch06_tools.py -v
```

Eight pass. The first is the invariant; the last catches a schema that would fail under `strict` on both providers, which otherwise surfaces as a 400 on your first real request in chapter 9.

---

Next: [07-loop.md](07-loop.md) — forty lines, every one of them a guard.
