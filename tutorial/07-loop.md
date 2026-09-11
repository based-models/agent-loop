# 07 — The loop

Forty lines. Every one of them is a guard.

Builds `src/harness/loop.py`. **Zero third-party imports** — including, notably, no
OpenTelemetry (chapter 10 attaches from outside). The `LoopObserver` protocol it uses
came with `observer.py` in chapter 6.

---

## Side by side with the notebook

**The notebook:**

```python
def run_conversation(messages):
    while True:
        response = chat(messages, tools=[get_current_datetime_schema])
        add_assistant_message(messages, response)
        if response.stop_reason != "tool_use":
            break
        tool_results = run_tools(response)
        add_user_message(messages, tool_results)
    return messages
```

**Ours:**

```python
def run_conversation(
    transcript: Transcript,
    client: LLMClient,
    registry: Registry,
    deps: Deps,
    *,
    system: str | None = None,
    max_tokens: int = 16_000,
    max_iterations: int = 50,
    deadline_s: float | None = 600.0,
    observer: LoopObserver | None = None,
) -> Transcript:
    obs = observer or NullObserver()
    started = time.monotonic()

    with obs.conversation():
        for _ in range(max_iterations):
            if deadline_s is not None and time.monotonic() - started > deadline_s:
                raise LoopBudgetExceeded(f"exceeded {deadline_s}s wall clock")

            # ── the one place a steering message would fold in (see below) ──

            request = ModelRequest(
                messages=tuple(transcript.messages),
                system=system,
                tools=registry.schemas(),
                max_tokens=max_tokens,
            )
            obs.on_request(request)
            response = client.complete(request)   # retries live BELOW this line
            obs.on_response(response)

            # F1 — truncated mid-tool-call: discard the turn, do NOT append
            if response.stop_reason is StopReason.MAX_TOKENS and _has_calls(response):
                logger.warning("turn truncated mid-tool-call; discarding and retrying")
                max_tokens = min(max_tokens * 2, 64_000)
                continue

            transcript.add_assistant(response.content)

            if response.stop_reason is not StopReason.TOOL_USE:
                if response.stop_reason is StopReason.UNKNOWN:
                    logger.warning("unrecognized stop reason %r; treating as end of turn",
                                   response.raw_stop_reason)
                return transcript

            results = run_tools(response.content, registry, deps, obs)  # total function
            transcript.add_tool_results(results)                        # asserts pairing

        raise LoopBudgetExceeded(f"exceeded {max_iterations} iterations")
```

Same skeleton, written by hand rather than driven by a framework (**D1**). Seven differences, each fixing a specific failure.

| | Notebook | Here |
|---|---|---|
| 1 | `chat(...)` closes over a module-level Anthropic client | `client.complete(request)` — the seam. No vendor name appears |
| 2 | `add_assistant_message` unconditional | Discard a turn truncated mid-tool-call (**F1 / D8**) |
| 3 | `!= "tool_use"` | An explicit enum, with `UNKNOWN` logged rather than swallowed |
| 4 | `while True` | Iteration cap **and** wall-clock budget (**F10**) |
| 5 | Nothing observes it | `LoopObserver` at every event |
| 6 | `add_user_message(messages, tool_results)` | `add_tool_results` asserts the pairing (**F2**) |
| 7 | Tools run sequentially | Parallel, results in call order |

---

## The failure register

Each stated as *symptom → the invariant that prevents it*. Most of the invariants live in earlier chapters; the loop's job is to not undo them.

### F1 — Truncated mid-tool-call

**Symptom.** `stop_reason` is `MAX_TOKENS` and the content ends with a partial `tool_use` block whose `input` is incomplete — the model was still writing the JSON when it hit the ceiling. On OpenAI-compatible servers: `finish_reason: "length"` with an unterminated `arguments` string.

**Invariant.** *Append only after a response is accepted as well-formed.*

The notebook appends unconditionally. Do that here and you have a permanently malformed message in an append-only transcript. You can't execute the call (the arguments are partial), and you can't skip it (that creates an unpaired `tool_use`, which is F2). There is no recovery — the transcript is poisoned for the rest of the session.

So: discard the whole turn, raise the ceiling, retry. If it truncates twice, something is structurally wrong — a tool asking for an enormous argument, usually — and you should surface that rather than loop.

Note this is the **only** place the loop discards a response. Everywhere else, what came back goes in.

### F2 — Unpaired tool calls

**Symptom.** The Anthropic 400 quoted in chapter 6.

**Invariant.** `run_tools` is total (chapter 6), and `add_tool_results` asserts set-equality of call IDs (chapter 2).

The loop's contribution is negative: it does not have a branch that skips `add_tool_results`, and it does not catch exceptions from `run_tools`. Both would reintroduce the failure.

### F3 / F4 / F9 — Unknown tools, bad arguments, bad IDs

Handled below the loop, in chapter 6's executor and the chapter 5 adapter. Listed here because the loop is where you'd be tempted to handle them, and shouldn't.

### F8 — Empty assistant turn

**Symptom.** `content: null`, `tool_calls: null`, `finish_reason: "stop"`.

**Invariant.** *An adapter never returns a `ModelResponse` with empty content* — it raises `ResponseInvalid`, which chapter 8 classifies as retryable.

If an empty turn reached the loop it would append an empty `Message`, which chapter 2 rejects, which fails on the *next* request. Caught at the edge instead.

### F10 — Non-terminating loop

**Symptom.** The model calls `read` on the same file forever. Or two tools ping-pong. Neither is rare.

**Invariant.** *An iteration cap and a wall-clock budget, both owned by the loop.*

Two limits, not one, because they catch different things. `max_iterations` catches a logical cycle. `deadline_s` catches slow progress — 50 iterations at 45 seconds each is a 37-minute run that nobody is watching. Note this is **independent of** the per-request timeouts in chapter 8: with retries, a single `complete()` can legitimately take several minutes, and `max_iterations × timeout × retries` is not a bound anyone wants to discover empirically.

A friendlier variant — on the last iteration, inject "you are out of turns, answer with what you have" — is a genuinely good idea and a nice exercise. Not bare bones.

### The `UNKNOWN` warning

```python
if response.stop_reason is StopReason.UNKNOWN:
    logger.warning("unrecognized stop reason %r; treating as end of turn",
                   response.raw_stop_reason)
```

Chapter 3's Rule 2 said never fold `UNKNOWN` into `END_TURN`. This is what that buys. The loop still terminates — there's nothing better to do — but the log line names the wire value, which turns "the agent randomly stopped" into a one-minute diagnosis.

---

## `LoopObserver` — why it's shaped like that

Chapter 6 already defined the protocol in `observer.py`, because `run_tools` needed it.
This is the chapter that explains the shape. It is divergence **D2**: the reference
course's `Boundary` async generator, replaced by plain callbacks.

```python
class LoopObserver(Protocol):
    def conversation(self) -> AbstractContextManager[None]: ...
    def on_request(self, request: ModelRequest) -> None: ...
    def on_response(self, response: ModelResponse) -> None: ...
    def on_retry(self, attempt: int, error: Exception) -> None: ...
    def on_tool_start(self, call: ToolCallPart) -> None: ...
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: ...
    def on_text_delta(self, text: str) -> None: ...       # reserved
```

Add one more implementation alongside `NullObserver`, for tests:

```python
class ListObserver(NullObserver):
    """Records every event, in order. Inherits the no-op methods it doesn't override."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def on_request(self, request): self.events.append(("request", request))
    def on_response(self, response): self.events.append(("response", response))
    def on_retry(self, attempt, error): self.events.append(("retry", error))
    def on_tool_start(self, call): self.events.append(("tool_start", call))
    def on_tool_end(self, call, result): self.events.append(("tool_end", result))
```

> **Python note.** `ListObserver` subclasses `NullObserver` purely to inherit its
> do-nothing methods, so it only has to write the ones it cares about. That is normal
> inheritance — unrelated to `Protocol`, which is about what an object *has*, not what it
> inherits from.

### Why the pairs matter

`conversation()` / `on_request`+`on_response` / `on_tool_start`+`on_tool_end` are three **enter/exit pairs**. That's not incidental — it's exactly the shape a span needs, and it's why chapter 10 can implement tracing as a drop-in observer without touching `loop.py`.

`conversation()` returns a context manager rather than being a pair of methods so that an exception unwinds the root span correctly. A `try/finally` in the caller would work; a context manager is harder to get wrong.

`on_retry` sits on the observer even though retries happen in chapter 8, below the loop. Wire it through so that when tracing arrives, retries show up as events *inside* the model span instead of as an invisible multiplier on latency.

### What this gives up, honestly

An async generator can send a value *back in* via `.asend()`. That's how the reference injects mid-turn steering: the loop yields `MODEL_REQUEST`, the runner drains a queue of things the user typed, and sends them back as the generator's return value.

A callback protocol cannot do that. The exchange:

| | `Boundary` generator | Observer protocol |
|---|---|---|
| Observation | ✅ | ✅ |
| Injection | ✅ | ❌ |
| Exception semantics | Awkward across `yield` | Ordinary |
| Forces async on consumers | Yes | No |
| Tracing is a drop-in | No | Yes |

Injection is the only thing lost, and it's needed for exactly one feature. Which is why the loop marks the spot:

```python
# ── the one place a steering message would fold in ──
```

Adding it later is: a `steering: Queue[str] | None` parameter, drained at that line into a `transcript.add_user_text(...)` before the request is built. The transcript is append-only, so this cannot corrupt history. Chapter 12.

---

## What the loop deliberately does not do

Worth being explicit, because each is a real temptation:

- **No retries.** They live below `complete()`, in chapter 8. The loop calls one function and either gets a response or an exception.
- **No transcript trimming.** On `ContextLengthExceeded` it fails loudly. Compaction is chapter 12, and a half-trimmed transcript is how you manufacture F2.
- **No provider knowledge.** No `if provider == "anthropic"` anywhere. Chapter 11's AST test enforces this.
- **No text extraction.** It returns the `Transcript`, not a string. The caller decides what to display. `"".join(p.text for p in t.messages[-1].content if isinstance(p, TextPart))` if you want the final answer.

---

## Checkpoint

The whole loop, against a fake. No network, no API key.

`tests/test_ch07_loop.py`:

```python
import pytest
from harness.loop import run_conversation, ListObserver, LoopBudgetExceeded
from harness.provider import ModelRequest, ModelResponse, StopReason, Usage
from harness.tools import Deps, Registry, READ
from harness.transcript import (
    TextPart, ToolCallPart, Transcript, TranscriptError,
)


class ScriptedClient:
    """Replays canned responses; records every request. Chapter 11 expands this."""
    name, model = "fake", "fake-1"

    def __init__(self, *responses): self._queue = list(responses); self.requests = []
    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._queue.pop(0)


def _resp(*parts, stop=StopReason.END_TURN):
    return ModelResponse(content=tuple(parts), stop_reason=stop,
                         usage=Usage(1, 1), model="fake-1", provider="fake")


def _run(client, tmp_path, **kw):
    t = Transcript(); t.add_user_text("go")
    return run_conversation(t, client, Registry([READ]), Deps(cwd=tmp_path), **kw)


def test_terminates_on_end_turn(tmp_path):
    t = _run(ScriptedClient(_resp(TextPart("done"))), tmp_path)
    assert len(t.messages) == 2


def test_full_tool_round_trip(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    client = ScriptedClient(
        _resp(ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
              stop=StopReason.TOOL_USE),
        _resp(TextPart("it sets x to 1")),
    )
    t = _run(client, tmp_path)
    # user, assistant(call), user(result), assistant(text)
    assert [m.role for m in t.messages] == ["user", "assistant", "user", "assistant"]
    assert "x = 1" in t.messages[2].content[0].content
    t.check_invariants()


def test_f1_truncated_tool_call_is_discarded(tmp_path):
    client = ScriptedClient(
        _resp(ToolCallPart(id="c1", name="read", arguments={}),
              stop=StopReason.MAX_TOKENS),          # truncated mid-call
        _resp(TextPart("recovered")),
    )
    t = _run(client, tmp_path)
    # the truncated turn never entered the transcript
    assert [m.role for m in t.messages] == ["user", "assistant"]
    assert t.messages[-1].content[0].text == "recovered"
    assert client.requests[1].max_tokens > client.requests[0].max_tokens


def test_f1_does_not_discard_a_truncated_text_turn(tmp_path):
    # MAX_TOKENS with no tool call is a normal (if clipped) answer — keep it
    t = _run(ScriptedClient(_resp(TextPart("half an ans"), stop=StopReason.MAX_TOKENS)),
             tmp_path)
    assert len(t.messages) == 2


def test_f10_iteration_cap(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    loop_forever = [_resp(ToolCallPart(id=f"c{i}", name="read",
                                       arguments={"path": "a.py"}),
                          stop=StopReason.TOOL_USE) for i in range(10)]
    with pytest.raises(LoopBudgetExceeded, match="iterations"):
        _run(ScriptedClient(*loop_forever), tmp_path, max_iterations=5)


def test_unknown_stop_reason_terminates_with_a_warning(tmp_path, caplog):
    _run(ScriptedClient(_resp(TextPart("?"), stop=StopReason.UNKNOWN)), tmp_path)
    assert "unrecognized stop reason" in caplog.text


def test_observer_sees_every_event(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    obs = ListObserver()
    client = ScriptedClient(
        _resp(ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
              stop=StopReason.TOOL_USE),
        _resp(TextPart("done")),
    )
    _run(client, tmp_path, observer=obs)
    assert [k for k, _ in obs.events] == [
        "request", "response", "tool_start", "tool_end", "request", "response",
    ]


def test_the_loop_never_names_a_vendor():
    import inspect, harness.loop
    src = inspect.getsource(harness.loop)
    for vendor in ("anthropic", "openai", "openrouter", "modal", "httpx", "claude"):
        assert vendor not in src.lower()
```

```bash
pytest tests/test_ch07_loop.py -v
```

Eight pass. Three are worth a second look:

- **`test_f1_does_not_discard_a_truncated_text_turn`** — the guard is narrow on purpose. `MAX_TOKENS` alone is a clipped but usable answer; only `MAX_TOKENS` **with a tool call** is unrecoverable. A broader check would silently throw away good answers.
- **`test_observer_sees_every_event`** — the event sequence is the contract chapter 10 builds spans from.
- **`test_the_loop_never_names_a_vendor`** — crude, and it works. Chapter 11 does it properly with `ast`.

You now have a complete, tested agent loop that has never made an HTTP request.

---

Next: [08-resilience.md](08-resilience.md) — what happens when the network disagrees.
