# 12 — Next seams

Everything chapter 0 declared out of scope, and where it attaches.

The test of an architecture is not what it does but what it makes cheap next. Each item below names **the seam it plugs into** and **what has to change in what you already wrote** — and in most cases the answer is *nothing*.

---

## Streaming

**Seam:** `complete_stream()` beside `complete()`; `on_text_delta` on `LoopObserver`. Both reserved in chapters 3 and 7.

```python
class LLMClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
    def complete_stream(self, request: ModelRequest) -> Iterator[StreamEvent]: ...
```

**`Iterator`, not `AsyncIterator`.** A sync generator streams perfectly well; this does not force async on anything. That's divergence D11 holding up under the one argument usually made against it.

Work: real, and confined to the adapters. Anthropic's event stream (`content_block_start` / `content_block_delta` / `content_block_stop`) and OpenAI's `delta` chunks are quite different, and tool-call arguments arrive in fragments that must be accumulated before parsing. Budget a day per adapter.

`loop.py` changes by one branch. Nothing else moves.

There's also a version of this you get for free right now: **implement `complete()` over the SDK's streaming helper internally** and return the final message. Anthropic's `client.messages.stream(...).get_final_message()` avoids HTTP timeouts on large `max_tokens` while keeping the seam's signature non-streaming. Streaming as a transport detail rather than an API shape.

---

## A TUI

**Seam:** `LoopObserver`. Chapter 9's `ConsoleObserver` is already a working implementation of the interface a TUI needs.

Chapter 7 shaped the protocol as enter/exit pairs partly for tracing and partly for this. Swapping `ConsoleObserver` for a prompt_toolkit or Textual app touches `loop.py` not at all.

What you'd add: streaming (above), a spinner driven by `on_request`/`on_response`, tool calls rendered as they start, and a keybinding that mutates something on `Deps`. That last one is the reason `Deps.before_tool` is a callable rather than a flag — a live approval prompt is a `before_tool` that blocks on input.

---

## Mid-turn steering

**Seam:** the marked line in `run_conversation`:

```python
# ── the one place a steering message would fold in ──
```

This is the one capability the observer protocol genuinely gave up versus the reference course's async-generator `Boundary` (D2). Adding it back:

```python
def run_conversation(..., steering: Queue[str] | None = None) -> Transcript:
    ...
        if steering is not None:
            pending = _drain(steering)
            if pending:
                transcript.add_user_text("\n".join(pending))
```

The transcript is append-only, so this cannot corrupt history — the steering message is just another user turn, arriving between tool rounds.

Note what the reference needs that you don't: `decode` runs **two** queues, one drained at `MODEL_REQUEST` and one at `WOULD_STOP`, because draining at the wrong moment corrupts an in-flight tool execution. Sync execution means there is exactly one safe point and you are standing on it.

---

## A real permission gate

**Seam:** `Deps.before_tool`, already wired through `run_tools` (chapter 6).

What a real gate adds: a `ToolKind` per tool (read-only / file-edit / exec), modes (`plan`, `edit`, `auto`), per-mode allow/ask/deny policy, an async prompt to the user, and remembered answers within a session.

```python
class ToolSpec(BaseModel):
    ...
    kind: ToolKind = ToolKind.OTHER     # the one field to add
```

Then `before_tool` consults a `PermissionGate` instead of a substring blocklist. Nothing else in the loop or the executor changes — a denial is already a normal `is_error` tool result.

The reference's approach is worth reading here: `decode` routes denials back to the model as `ToolDenied` so the model sees the *reason* and can propose an alternative, rather than just failing. Chapter 6's `fail(f"Denied: {denial}")` already does this; you're only upgrading the policy.

---

## Sandboxing

**Seam:** behind the tool handlers. `read`/`write`/`edit`/`bash` become thin wrappers over a `SandboxBackend`.

```python
class SandboxBackend(Protocol):
    def read_bytes(self, path: PurePosixPath) -> bytes: ...
    def write_bytes(self, path: PurePosixPath, data: bytes) -> None: ...
    def exec(self, cmd: str, timeout: float) -> tuple[int, str, str]: ...
```

Docker locally, Modal sandboxes remotely, direct `pathlib` for `none`.

The design rule worth stealing from the reference: keep the **shared logic host-side** — path containment math, line numbering, truncation, search/replace matching — and put only **byte transport** behind the seam. A sandboxed result then reads identically to a local one, and you have one implementation of the tricky parts rather than one per backend.

Chapter 6's `_resolve` containment check stays as a first line of defense. A real backend adds physical isolation underneath it.

---

## Context compaction

**Seam:** the would-stop point in `run_conversation`, and `ContextLengthExceeded`, which you already raise from all three providers (chapter 8).

The trigger you want is proactive rather than reactive: compact when `usage.input_tokens` crosses a fraction of the model's window, not when a request has already failed.

Two things chapter 2's design makes easier:

**The transcript is append-only**, so compaction builds a *new* `Transcript` rather than mutating one. This is not stylistic — Anthropic's preserved-thinking check rejects a request whose earlier turns were edited, so "summarize the first 30 messages in place" is a 400 waiting to happen.

**The pairing invariant constrains where you can cut.** You cannot split an assistant turn from its tool results. `check_invariants()` on the compacted transcript catches the mistake locally.

The reference's two-tier cascade is a good model: *microcompaction* (drop old tool result bodies, keep the calls) fires first and is cheap; *full compaction* (LLM-summarize the head, keep a verbatim recent tail) fires later. Both need a token estimate, and the reference's hard-won note is worth repeating — a cumulative `RunUsage` across an N-round turn overcounts context occupancy by roughly N×. Use the **last** response's own input token count, which is what `ModelResponse.usage` already gives you.

---

## Session persistence and resume

**Seam:** `model_dump_json()` / `model_validate_json()`. Chapter 2 already gave you this.

```python
def append_jsonl(path: Path, messages: Sequence[Message]) -> None:
    with path.open("a") as f:
        for message in messages:
            f.write(message.model_dump_json() + "\n")


def load_jsonl(path: Path) -> Transcript:
    return Transcript(messages=[
        Message.model_validate_json(line)
        for line in path.read_text().splitlines() if line.strip()
    ])
```

Keep a cursor of how many messages are persisted; append the rest at the would-stop
point. Resume is the loader above — the discriminated union picks the right part class
for every entry, and a truncated final line fails with a message naming the field rather
than silently producing a half-built transcript.

The one trap: a crashed run can leave a log whose last message is an assistant turn with unanswered tool calls. Run `check_invariants()` on load and either refuse or append synthesized error results — this is exactly what `decode`'s `_heal_dangling_tool_calls()` does, and the one place you'd genuinely need it.

---

## Subagents

**Seam:** `run_conversation` itself. A subagent is a nested call with a different registry and its own transcript.

```python
def spawn(task: str, client: LLMClient, *, tools: Registry, cwd: Path) -> str:
    t = Transcript(); t.add_user_text(task)
    run_conversation(t, client, tools, Deps(cwd=cwd), max_iterations=25)
    return _final_text(t)
```

Wrap that as a tool and the parent can fan out. Three caps you want from the start: parallel width, per-child iteration budget, and a **shared byte budget** for the folded results — divided across children, so a fan-out of 8 costs the same in context as a fan-out of 2.

Read-only tool sets for explore-style children keep the blast radius small.

---

## Async

**Seam:** a sibling protocol.

```python
class AsyncLLMClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
```

The seam is one method, so this is cheap — which was chapter 3's argument for the shape, arriving.

Reach for it when you're **serving** many conversations concurrently, not because the loop would be faster. The loop is serial by nature (D11). The cheaper intermediate step is running the existing sync loop in a thread pool; try that first and measure before coloring the codebase.

`transcript.py`, `provider.py`, `errors.py`, and `tools.py` need no changes at all — they're pure data and pure functions.

---

## A private package index

Chapter 1 set up git tags over SSH. Move when you have more than one consumer, or CI that shouldn't hold deploy keys.

Consumers point pip at it — in `pip.conf`, in an env var, or per-command:

```bash
export PIP_INDEX_URL=https://your-index/simple/
pip install "harness[anthropic]"
```

Or in their `environment.yml`, which keeps the whole thing declarative:

```yaml
  - pip:
      - --index-url https://your-index/simple/
      - harness[anthropic]
```

GitHub Packages, AWS CodeArtifact, GCP Artifact Registry, and Gemfury all work. Publish
from CI on tag:

```yaml
- run: pip install build twine
- run: python -m build
- run: twine upload --repository-url https://your-index/ dist/*
  env:
    TWINE_USERNAME: __token__
    TWINE_PASSWORD: ${{ secrets.PACKAGE_TOKEN }}
```

Keep chapter 11's wheel test in that pipeline, *before* publish. It is the thing that stops you shipping a version whose optional extras aren't.

---

## Evals

Not a seam — a different discipline, and the natural next thing after this tutorial.

Unit tests answer "does the loop hold its invariants." Evals answer "does the agent fix the bug." You need both, and the second needs a task set, a grader, and a budget, because every run costs real money.

`FakeClient` doesn't help here. What does help: chapter 2's golden transcripts give you a diffable record of what an agent actually did, and chapter 10's spans give you per-run token and cost accounting to score against.

---

## What you built

```
~900 lines of Python
  3 providers behind 2 adapters behind 1 seam
  1 loop that never names a vendor
  ~50 tests, no network
  1 wheel
```

And, more to the point, a set of invariants you can state:

- The transcript is append-only, and a tool call without its result cannot be constructed.
- `run_tools` is total: every call gets exactly one result, errors included.
- The loop discards a turn only when it is unrecoverable, and never otherwise.
- No vendor type crosses the seam, enforced by a test rather than by discipline.
- Retry is safe because `complete()` is pure.

Those are the things that make the next feature cheap. Everything above is an hour or a day; none of it is a rewrite.
