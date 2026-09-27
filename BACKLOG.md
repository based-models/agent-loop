# Backlog

Work that was deliberately deferred, with enough context to pick it up cold.

## OpenRouter reasoning replay

**Status:** not implemented. The `Quirks.replays_reasoning` flag was removed from
`src/agentloop/providers/_openai_compat.py` because nothing read it. OpenRouter's builder
set it to `True`, but the shared adapter never parsed reasoning or sent it back, so the
setting was misleading.

**What was promised:** `tutorial/05-openai-compatible.md`, in the `replays_reasoning`
section, says OpenRouter replays reasoning, but only when `ThinkingPart.provider` and
`.model` are unchanged, and that Modal does not. `tutorial/DIVERGENCES.md` and the
chapter 5 code listings still show the flag. Update them when this lands, or drop the
promise.

**What implementing it takes:**

1. **Parse.** In `_openai_compat._normalize`, read `message["reasoning"]` (text) and
   `message["reasoning_details"]` (a list of blocks) into `ThinkingPart`s. Set `provider`
   and `model`, and keep the original block verbatim in `raw`. The parts must come before
   the text and tool calls, in the order received.
2. **Replay.** In `message_to_wire`, for an assistant message, re-emit `reasoning_details`
   from the `raw` blocks, byte for byte, in their original order. OpenRouter's docs forbid
   reordering or modifying them. Replay only when the part's `provider` and `model` match
   the current request's. Otherwise drop the part, because a different model cannot use it.
3. **Gate.** Reintroduce a quirk flag, so Modal/vLLM never replays: vLLM's
   `reasoning_content` is generally not replayable, and most chat templates discard it.
   The OpenRouter builder turns the flag on.
4. **Usage.** `completion_tokens_details.reasoning_tokens` is already mapped. Nothing to do.
5. **Tests.**
   - A body with `reasoning_details` round-trips through
     `response_from_wire` → `message_to_wire` unchanged.
   - A part from a different model is dropped on replay.
   - Modal quirks never emit reasoning.

**Why it was deferred:** the `src/agentloop` review (September 2026) fixed defects first.
This is a feature, not a fix.

## Model-aware provider `Quirks`

**Status:** not implemented. `Quirks` is selected once per *provider* (one `Quirks()`
instance built alongside `BASE_URL`/`DEFAULT_MODEL` in `openrouter.py`/`modal.py`/
`openai.py`), not per *model*. A single provider module cannot give two models of the
same provider different behaviour.

**Concrete trigger:** `providers/openai.py` now sends `max_completion_tokens` for every
request (fixed in the September 2026 review, since OpenAI's reasoning models reject
plain `max_tokens`). But `parallel_tool_calls` is still sent unconditionally
(`supports_parallel_tool_calls_param=True` by default) — real reasoning models reject
that parameter too, while OpenAI's non-reasoning chat models accept it. There is no way
to configure `openai.py` so a non-reasoning model keeps `parallel_tool_calls` and a
reasoning model drops it, short of instantiating two separately-configured clients by
hand and picking one per model at the call site.

**What implementing it takes:** `Quirks` selection needs a second axis besides provider
— most likely a small `model -> Quirks` override table (regex or prefix match) that
`build_openai_client(model=...)` consults before falling back to the provider default.
Needs real design (what should match, what happens on no match, how Modal's
already-provider-wide quirks interact) rather than a quick patch.

**Why it was deferred:** needs a real API decision (how overrides are expressed and
matched), not something to bolt on during a bug-fix pass.

## No timeout on individual tool-handler execution

**Status:** not implemented. `run_conversation`'s `deadline_seconds` is checked only
*between* model calls (`loop.py`); neither `tooling.run_tools`'s single-call path nor its
`ThreadPoolExecutor` path (`future.result()`, no `timeout=`) bounds how long a tool
handler may run.

**Concrete trigger:** register a tool whose handler makes a network call with no timeout
of its own, to an endpoint that never responds. The whole agent loop hangs forever, even
with `deadline_seconds` configured — the deadline only ever fires before the *next* model
call, which never arrives.

**What implementing it takes:** a per-tool or per-call timeout, likely a `ToolSpec` field
consulted by `run_tools` around `future.result(timeout=...)` for the parallel path and an
equivalent guard for the single-call path; needs a decision on what happens to a handler
thread that's still running after its timeout fires (Python cannot forcibly kill a thread).

**Why it was deferred:** the "kill a running thread" question needs its own design; not
safe to guess at during a bug-fix pass.

## No way to mark a tool as unsafe to run in parallel

**Status:** not implemented. `run_tools` always parallelizes a turn with two or more tool
calls via a fixed `ThreadPoolExecutor(max_workers=8)` (`tooling.py`). There is no
`ToolSpec` flag for a handler that isn't thread-safe (a shared DB cursor, a non-atomic
shared counter, an SDK client not documented as thread-safe).

**Concrete trigger:** register two tools that both mutate the same plain `list` or
`sqlite3.Connection` with no lock, and let the model call both in one turn.
Intermittent corruption or exceptions appear only once a model happens to call
tools together — invisible in a demo where it calls them one at a time.

**What implementing it takes:** a `ToolSpec.parallel_safe: bool = True` (or similar) that
`run_tools` checks, running any turn containing an unsafe tool call sequentially instead
of handing it to the thread pool.

**Why it was deferred:** the September 2026 review found and named the gap; picking the
right default and flag semantics is a small API decision better made deliberately than as
part of a bug-fix pass.

## No per-provider timeout override via `Settings`/`build_client()`

**Status:** not implemented. Connect/read/write/pool timeouts are hard-coded per adapter
file (`anthropic.py`'s `DEFAULT_TIMEOUT_SECONDS`, `openrouter.py`/`modal.py`/`openai.py`'s
`TIMEOUT_SECONDS` dicts). A consumer who wants a longer read timeout for a slow model has
to bypass `Settings` entirely and call `build_<provider>_client()` directly, losing the
one-call `Settings.from_env()` / `build_client()` ergonomics for everything else.

**What implementing it takes:** an optional `timeout_seconds: float | None` (or a richer
shape) on `Settings`, threaded through each `_build_*` helper in `config.py` into the
matching provider builder's `timeout=` argument.

**Why it was deferred:** low risk, but touches every provider's builder signature at
once; grouped here to do in one pass rather than piecemeal.

## `configure_tracing()` silently no-ops on a second call

**Status:** not implemented (documented gap, not patched). OpenTelemetry's global
tracer-provider registration is one-shot: `trace.set_tracer_provider()` only takes effect
the first time it's called in a process. `configure_tracing()` always returns the
freshly-built `TracerProvider` regardless of whether registration actually took, with no
diagnostic either way.

**Concrete trigger:** two independent subsystems (or a hot-reload, or a test fixture) each
defensively call `configure_tracing()`. The second call returns a live-looking provider;
its `force_flush()`/`shutdown()` do nothing useful, and the actually-active first provider
is never flushed or shut down, risking dropped batched spans on exit.

**What implementing it takes:** detect that `trace.get_tracer_provider()` is already a
real (non-default) provider before installing a new one, and warn or raise instead of
silently returning a decoy. Reliably distinguishing "the default no-op provider" from "a
real one already installed" needs checking across the OpenTelemetry SDK versions this
package supports — not something to guess at without dedicated testing.

**Why it was deferred:** version-fragile to get right blind; flagged during the September
2026 review rather than patched without that testing.

## `trace=True` without `configure_tracing()` silently discards every span

**Status:** not implemented (documented gap, not patched). Correct and deliberate — the
library never touches global tracing state on its own (`tutorial/DIVERGENCES.md` D14, now
gitignored but recoverable from git history) — but a first-time consumer who sets
`Agent(..., trace=True)` and sees no spans anywhere has no signal that a second,
separately-named call (`agentloop.tracing.configure_tracing(endpoint)`) was required.

**What implementing it takes:** a one-time `logger.warning(...)` the first time
`OTelObserver.conversation()` runs while the global tracer provider is still the default
no-op provider. A log line doesn't violate "no global state touched on its own." Blocked
on the same reliable-detection question as the item above — solve both together.

**Why it was deferred:** shares its blocker with "`configure_tracing()` silently no-ops
on a second call"; solve the detection question once, apply it to both.

## `providers/fake.py` (`FakeClient`) was never built

**Status:** not implemented, and it breaks `scripts/test-wheel.sh`. `tests/test_loop.py`'s
`ScriptedClient` docstring says "chapter 11 promotes this to
`agentloop.providers.fake.FakeClient`," and `scripts/test-wheel.sh` already imports
`from agentloop.providers.fake import FakeClient` in its bare-install smoke test — but no
`src/agentloop/providers/fake.py` exists. Running `scripts/test-wheel.sh` today fails at
that import with `ModuleNotFoundError: No module named 'agentloop.providers.fake'`,
before it can check anything the script was written to check (discovered while verifying
the September 2026 review's httpx-import fix, which otherwise had to be confirmed by
hand-running the script's one relevant check in isolation instead of end to end).

**What implementing it takes:** promote `tests/test_loop.py`'s `ScriptedClient` into a
real `LLMClient` implementation at `src/agentloop/providers/fake.py` — takes canned
`ModelResponse`s, replays them in order, records requests, satisfies the same protocol
`AnthropicClient`/`OpenAICompatClient` do. `ScriptedClient` is a ready-made reference for
the shape.

**Why it was deferred:** a new module, not a fix to an existing one — matches the
project's "writing a chapter's module for the first time is the learning" convention
rather than something to add unprompted during a review.

## `async` support (`AsyncLLMClient`, `Agent.arun()`)

**Status:** not implemented. Every provider client (`AnthropicClient`,
`OpenAICompatClient`) blocks on a sync SDK/`httpx.Client` call; `run_conversation`
(`loop.py`) and `run_tools` (`tooling.py`) are entirely synchronous, matching
`tutorial/DIVERGENCES.md` D11 ("one sync path; parallelism only via
`ThreadPoolExecutor`").

**Why deferred instead of built:** `client.complete()` spends its time blocked on a
socket, and both the `anthropic` SDK and `httpx.Client` release the GIL while waiting —
exactly what Python threads already handle well. An application that wants N concurrent
conversations can call `agent.run(prompt)` from N threads (a `ThreadPoolExecutor`, or
`await asyncio.to_thread(agent.run, prompt)` from an async framework) **today, with zero
changes here**. A real `async`/`await` rewrite only earns its cost at a fleet size where
thread count itself is the bottleneck — typically four figures of concurrent
conversations on one process.

**What implementing it would take, if that threshold is ever reached:**

The seam already pays for most of this: `provider.py`'s canonical types
(`ModelRequest`/`ModelResponse`/`Usage`/`StopReason`/`ToolSchema`) are pure pydantic
models, and every adapter's wire-translation functions (`request_to_wire`/
`response_from_wire`/`classify_*_error`) do no I/O — an async client would call the exact
same functions a sync one does. `observer.py`/`tracing.py` need **no** async variants
either: `LoopObserver.conversation()`'s body does no I/O, and a plain `with` works fine
inside an `async def`. `errors.py`/`transcript.py` are unaffected. What would need a
genuine async twin:

1. `provider.py` — an `AsyncLLMClient(Protocol)` beside `LLMClient`
   (`async def complete(...)`), mirroring the shape of the already-reserved
   `complete_stream` comment.
2. `providers/anthropic.py` — `AsyncAnthropicClient` on `anthropic.AsyncAnthropic`
   (confirmed present in the installed SDK).
3. `providers/_openai_compat.py` — one `AsyncOpenAICompatClient` on `httpx.AsyncClient`,
   which covers openrouter/modal/openai (no per-provider duplication needed).
4. `retry.py` — `AsyncRetryingClient` (`await self._inner.complete(...)`,
   `await asyncio.sleep(delay)`; `_delay_for()` is pure and reusable as-is).
5. `tooling.py` — an `AsyncHandler` type and `run_tools_async()` using
   `asyncio.gather`/a `TaskGroup` in place of `ThreadPoolExecutor`, preserving the
   totality guarantee (`_execute` never raises) in the async path too.
6. `loop.py` — `run_conversation_async()`, structurally identical to
   `run_conversation`, with `await client.complete(...)`/`await run_tools_async(...)`.
7. `agent.py` — `Agent.arun()`/`Agent.aconverse()`; `Agent.from_env()` needs an
   async-aware path.
8. `config.py` — `build_async_client()`, mirroring `build_client()`'s `_build_*` helpers.
9. Tests — an async counterpart for every sync-path test in `test_anthropic.py`,
   `test_openai_compat.py`, `test_loop.py`, `test_agent.py`, `test_retry.py`,
   `test_tooling.py`, plus a new `[dev]` dependency (`pytest-asyncio` or `anyio`).

Additive, not a rewrite — the real cost is item 9, doubling the provider and loop test
surface.
