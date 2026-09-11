# Divergences from the reference architecture

Two references shape this tutorial:

- **`decode`** — the [Decoding AI course repo](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course) and [its article](https://www.decodingai.com/p/the-coding-agent-loop). Built on Pydantic AI.
- **The notebook** — Anthropic's tool-use course notebook. Sync, single-provider, Anthropic-native.

Every place this tutorial does something different is listed here, with the reason. Each row is argued again in the chapter where it comes up.

---

## Summary

| # | Reference | This tutorial | Chapter |
|---|---|---|---|
| D1 | Pydantic AI `Agent` + `agent.iter()` | Hand-written `while` loop | 07 |
| D2 | `Boundary` async generator | Sync `LoopObserver` callback `Protocol` | 07 |
| D3 | Gemini provider | Anthropic provider | 04 |
| D4 | Three provider branches | Two adapters; OpenRouter + Modal share one | 05 |
| D5 | `_build_model()` returns a `Model` | `build_client()` returns a configured `LLMClient` | 09 |
| D6 | `RunContext[AgentDeps]` generic | Plain frozen `Deps` dataclass | 06 |
| D7 | Pydantic models generate tool schemas | Hand-written JSON Schema dicts | 06 |
| D8 | *(notebook)* appends every assistant turn | Discards a turn truncated mid-tool-call | 07 |
| D9 | *(notebook)* `temperature=1.0` hardcoded | `temperature: float \| None = None`, omitted when `None` | 03, 04 |
| D10 | Framework owns message history | You own `Transcript`, append-only | 02 |
| D11 | `run` / `run_sync` duality | One sync path | 03 |
| D12 | TUI, permissions, sandbox, compaction, subagents | Out of scope; seams named | 12 |
| D13 | Module-level `settings` singleton | `Settings` constructed explicitly | 09 |
| D14 | Opik + `logfire.instrument_pydantic_ai()` | OpenTelemetry API directly, as an observer | 10 |
| D15 | *(n/a)* | Pydantic models for all data that crosses a boundary | 2, 3 |

---

## D1 — No agent framework

**`decode`:** `Agent(model, deps_type=AgentDeps, output_type=[str, DeferredToolRequests])`, driven via `async with agent.iter(...) as run`.

**Here:** a `for` loop you write.

The obvious reason is "no framework, that's the exercise." The real reason is subtler. `agent.iter()` hands you a stream of nodes *in Pydantic AI's shape* — `is_model_request_node`, `is_call_tools_node`, `ModelResponse`, `ToolCallPart`. So you don't escape having an abstraction over the model; you end up with **two**, yours and theirs, and every bug lives in the space between them.

Worse for learning: Pydantic AI's model adapters have already made every normalization decision this tutorial is about. Which stop reasons map to which. Whether `arguments` arrives parsed. What happens to a turn truncated mid-tool-call. Those decisions are the actual content of a provider-agnostic harness, and in the reference they are invisible inside a dependency.

The replacement is about 40 lines. See chapter 7.

---

## D2 — An observer protocol, not an async-generator boundary

**`decode`:** `AgentTurnHandler.__call__` is an async generator that yields `Boundary.MODEL_REQUEST` and `Boundary.WOULD_STOP`; the runner drives it and sends values back in through `.asend()`.

**Here:**

```python
class LoopObserver(Protocol):
    def conversation(self) -> AbstractContextManager[None]: ...
    def on_request(self, request: ModelRequest) -> None: ...
    def on_response(self, response: ModelResponse) -> None: ...
    def on_retry(self, attempt: int, error: ProviderError) -> None: ...
    def on_tool_start(self, call: ToolCallPart) -> None: ...
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: ...
```

The `Boundary` idea is genuinely the best thing in the reference architecture: one place where every loop event funnels through, so logging, cost accounting, tracing, and eventually a UI attach without touching the loop. **Keep the idea.** Replace the mechanism.

Two-way async generators are a poor interception primitive. Exceptions propagate strangely across a suspended `yield` — the reference has a comment explaining that `GeneratorExit` forbids `await`, which is why its cleanup path is awkwardly synchronous. Aborting requires `agen.aclose()` throwing into the suspension. And every consumer is forced `async` whether it needs to be or not.

A callback `Protocol` gives the same interception points with none of that, and it is what makes chapter 10's tracing a pure add-on: `loop.py` never imports OpenTelemetry.

The cost is real and worth naming: an async generator can send a value *back in*, which is how the reference injects mid-turn steering. A callback protocol cannot. Chapter 7 marks the one place that fold-in would go, and chapter 12 shows how to add it.

---

## D3 — Anthropic instead of Gemini

Your requirement. It also happens to make the exercise better: Gemini, OpenRouter, and Modal are three providers but the latter two are OpenAI-shaped, so `decode`'s seam papers over one genuinely different API. Anthropic is different in *more* ways — block-structured content, a top-level `system`, required `max_tokens`, thinking blocks that must be replayed verbatim — so the seam has to be real.

---

## D4 — Two adapters, not three

**`decode`:** `_build_model()` has three branches — `gemini`, `openrouter`, `modal`.

**Here:** two adapter modules. `providers/anthropic.py`, and `providers/_openai_compat.py` which both OpenRouter and Modal are thin configurations of.

OpenRouter is an OpenAI-compatible gateway. Modal, in the vLLM deployment this tutorial uses, serves an OpenAI-compatible `/v1`. They speak **the same wire protocol**. Their real differences collapse into six booleans:

```python
class Quirks(BaseModel):
    model_config = ConfigDict(frozen=True)

    supports_strict_tools: bool = True
    replays_reasoning: bool = False
    reports_usage_details: bool = True
    errors_are_json: bool = False
    supports_parallel_tool_calls_param: bool = True
    default_parallel_tool_calls: bool = True
```

Note that `decode` half-acknowledges this already — its `openrouter` and `modal` branches both construct `OpenAIChatModel` and differ only in the provider/client passed in. This tutorial makes the sharing explicit instead of incidental.

---

## D5 — `build_client()` returns a client, not a model

**`decode`:** `_build_model()` returns a Pydantic AI `Model`, which the `Agent` then drives.

**Here:** `build_client(settings)` returns an `LLMClient` with retry policy, timeouts, and quirks already baked in.

Subtle but load-bearing. If the factory returns a *model*, something above it still has to know how to drive that model — so provider knowledge leaks upward into the loop's neighborhood. If it returns a fully-configured *client* satisfying a one-method protocol, the loop has literally zero provider knowledge, and every test injects a fake at exactly one seam.

---

## D6 — A plain dataclass, not a generic run context

**`decode`:** tools take `ctx: RunContext[AgentDeps]` as their first parameter; Pydantic AI injects it.

**Here:** a frozen `Deps` dataclass, passed explicitly.

Keep the idea — a typed bundle of cwd, config, and hooks threaded into tool execution is right. Drop the generic: `RunContext[AgentDeps]` type-checks a field you already control, and it costs a framework import in every single tool module, which would break the "core imports no vendor" rule that chapter 11 enforces mechanically.

---

## D7 — Hand-written JSON Schema

**`decode`:** Pydantic AI builds each tool's schema from the function signature and docstring.

**Here:** a JSON Schema `dict` written by hand next to each tool.

> This is **not** "don't use pydantic" — see D15, which uses it for every data model in
> the project. The two are separable, and worth separating. Pydantic is excellent at
> modelling *your* data; `model_json_schema()` is a poor fit for generating *the schema a
> model reads*, for the three reasons below.

Three reasons, in increasing order of importance:

1. **It's a dependency you don't need.** Core stays at zero third-party imports.
2. **Generated schemas are wrong for this.** `model_json_schema()` emits `$defs` / `$ref` / `anyOf` for anything non-trivial. OpenAI strict mode rejects some of that, and several vLLM tool-call parsers choke on it — so you'd post-process the generated schema anyway, which is strictly more work than writing the dict.
3. **The schema is prompt engineering.** The description text is what the model actually reads to decide whether to call your tool. You want to control it word for word, and you want it byte-stable across runs, because tool definitions are rendered first in the prompt-cache prefix — a schema that shuffles its key order silently costs you every cache hit.

Argument validation without Pydantic turns out to be one line: `inspect.signature(handler).bind(**arguments)`.

---

## D15 — Pydantic models, not plain dataclasses

**Neither reference does this explicitly.** `decode` gets pydantic models for free
because Pydantic AI owns its message types; the notebook uses raw dicts.

**Here:** every piece of data that crosses a boundary is a `BaseModel` —
`TextPart` / `ToolCallPart` / `Message` / `Transcript` (chapter 2), `ModelRequest` /
`ModelResponse` / `Usage` / `ToolSchema` (chapter 3), `ToolSpec` / `Deps` (chapter 6),
`Quirks` (chapter 5), `RetryPolicy` (chapter 8), `Settings` (chapter 9, via
`pydantic-settings`).

Four things it buys, each replacing code you would otherwise write:

1. **Illegal values cannot be constructed.** `Field(min_length=1)` on `ToolCallPart.id`,
   `gt=0` on `max_tokens`, `min_length=1` on `ModelResponse.content`. Failure mode F8 —
   an adapter returning an empty assistant turn — stops being a check every adapter has
   to remember and becomes a property of the type.
2. **The discriminated union does real work.** `Field(discriminator="type")` on
   `ContentPart` means deserialization reads one field and goes straight to the right
   class, with errors reported against *that* class. The redundant `type` field was
   already there for a hand-rolled loader; this makes it load-bearing.
3. **Serialization is a round trip, not a project.** `model_dump_json()` and
   `model_validate_json()` replace `asdict`, a hand-written loader, and a `match` on
   `part["type"]`. Golden tests and session logs both fall out of it.
4. **`extra="forbid"` catches what nothing else does.** A provider response carrying a
   field you didn't model, or a keyword typo in a constructor, becomes an error instead
   of silence.

### What it costs, honestly

**Core now has a dependency.** Chapters 2, 3, 6, 7 were previously "zero third-party
imports". They now import pydantic, and chapter 11's seam test drops `pydantic` from its
banned list. That is a real change to a stated constraint, and the defence is that the
constraint was always aimed at the wrong target: what matters is that **core knows about
no specific LLM provider**. Pydantic has no opinion about Anthropic or vLLM, doesn't
drift with a provider's API, and doesn't make a Modal-only install pull in the Anthropic
SDK. `anthropic` and `httpx` do all three.

**A new failure mode at the seam.** `ValidationError` is pydantic's, not ours, and the
loop must never see it. Chapters 4 and 5 wrap the `ModelResponse` constructor and
re-raise as `ResponseInvalid`. That is three lines per adapter, and it is the price of
validating at the boundary rather than trusting it.

**Validation on every construction.** A long run builds thousands of parts. Pydantic v2's
validator is Rust-backed and costs single-digit microseconds per small model, against an
LLM call measured in seconds — so this is not a hot loop. `model_construct()` skips
validation if it ever becomes one, but reach for it with a measurement in hand.

### What did not change

`LLMClient` is still a `Protocol`, not a `BaseModel`. Nothing forces a client
implementation to be a pydantic model, which is what keeps chapter 11's ten-line
`FakeClient` possible.

Exceptions are still plain `Exception` subclasses. They are raised and caught, never
parsed or serialized, so validation buys nothing — and `BaseModel` doesn't inherit from
`Exception` anyway.

And **tool schemas are still hand-written dicts** (D7). Using pydantic for your own data
and using it to generate the schema a model reads are different decisions.

---

## D8 — Don't append a turn truncated mid-tool-call

**The notebook:**

```python
response = chat(messages, tools=[...])
add_assistant_message(messages, response)   # unconditional
```

**Here:** if `stop_reason` is `MAX_TOKENS` **and** the content contains a tool call, discard the whole turn and retry with more room.

When a response is cut off by `max_tokens` in the middle of a tool call, the `tool_use` block's `input` is incomplete — the model was still writing the JSON. Appending it puts a permanently malformed message into history. You cannot execute it (the arguments are partial), and if you skip it you've created an unpaired `tool_use`, which Anthropic hard-rejects on the very next request.

The transcript is append-only, so there is no un-appending. The invariant is: **append only after a response has been accepted as well-formed.**

---

## D9 — `temperature` is not a constant, and often not allowed at all

**The notebook:**

```python
def chat(messages, system=None, temperature=1.0, stop_sequences=[], tools=None):
    params = {..., "temperature": temperature, ...}
```

**Here:** `temperature: float | None = None`, and omitted from the payload entirely when `None`.

This is not a style preference. **`temperature`, `top_p`, and `top_k` were removed from Claude Opus 5, Sonnet 5, Opus 4.8, Opus 4.7, and the Fable line, and sending them returns a 400.** A direct port of the notebook's `chat()` fails against every current Claude model.

They are still accepted on Opus 4.6, Sonnet 4.6, Haiku 4.5, and older, and on every OpenAI-compatible provider — so the parameter stays in the canonical request. It just has to be genuinely optional, with `None` meaning *omit the key*, not *send a default*.

This is the single most likely thing to bite you if you copy loop code from an older tutorial.

---

## D10 — You own the transcript, and it is append-only

**`decode`:** Pydantic AI owns `ModelMessage` and its serialization; the handler carries a `list[ModelMessage]` across turns.

**Here:** your own `Transcript` over your own frozen dataclasses.

You need to own it for three practical reasons — JSONL persistence with no serialization library, golden-file tests, and the tool-call pairing invariant that `add_tool_results()` enforces.

There's a fourth reason that is not a preference but a hard API constraint. Anthropic's **preserved thinking** check means editing an earlier turn invalidates the thinking blocks that follow it; on newer models a request with edited history is rejected outright. So the harness must be **append-only**, and that is much easier to guarantee about a data structure you wrote than one a framework hands you.

`decode` runs into the consequences of not having this. It carries a `_heal_dangling_tool_calls()` method that synthesizes fake tool returns to un-brick a session whose history ended in unpaired tool calls — a repair for a state this tutorial's `add_tool_results()` makes unreachable.

---

## D11 — One sync path

**`decode`:** async throughout.

**Here:** sync.

The loop is inherently serial: request → wait → run tools → request. There is no concurrency to exploit *at the loop level*, so async buys nothing there. The one real parallelism is N tool calls in a single turn, and that is `ThreadPoolExecutor(max_workers=8)` — four lines, no coloring of anything else. Coding-agent tools are file I/O and subprocesses, so the GIL isn't the constraint.

Meanwhile `async` propagates upward through `run_tools`, `run_conversation`, the observer protocol, and every test, and drags in `pytest-asyncio` or `anyio`.

The honest counterargument is streaming to a UI — and it doesn't hold, because a **sync generator streams fine**. `complete_stream(request) -> Iterator[StreamEvent]` needs no event loop. Async is a *serving* concern (500 concurrent conversations), not an agent-loop concern, and when you get there the seam is one method, so an `AsyncLLMClient` sibling protocol is cheap. Chapter 12.

---

## D12 — Scope

Out: TUI, permission framework, sandboxing, compaction, subagents, LSP, skills, memory, streaming, async.

In, as the single exception: a `before_tool` approval hook, defaulting to allow-all. A `bash` tool with no gate whatsoever is not a pattern to internalize, and the hook is four lines.

"Out of scope" is not the same as "unanticipated." The TUI in particular needs two things that would be expensive to retrofit — a streaming path and a place to fold in mid-turn steering — so both are *shaped for* in v1 even though neither is built. Chapter 7 marks them; chapter 12 shows the attachment.

---

## D13 — No settings singleton

**`decode`:** `from decode.config.settings import settings` — a module-level instance, constructed at import, reading `.env`.

**Here:** `Settings` is a plain object you construct. `Settings.from_env()` is opt-in. `build_client(settings)` takes it as an argument.

That singleton is *correct* for an application, and `decode` is an application. This is a **library**, and a library that reads `.env` at import time is a library that surprises everyone who installs it: import order becomes load-bearing, tests need monkeypatching, and a consumer with their own config system finds yours has already run.

The rule: env reading happens in `cli.py`, once, explicitly. Everything below takes configuration as an argument.

---

## D14 — OpenTelemetry directly

**`decode`:** Opik via an OTLP exporter, plus `logfire.instrument_pydantic_ai()`.

**Here:** the OpenTelemetry API directly, as a `LoopObserver`, following the GenAI semantic conventions.

Three changes:

- **No vendor tracing layer.** Opik is an OTLP backend; if you emit standard OTLP you can point it at Opik, Jaeger, Honeycomb, Grafana, or a local collector without changing a line. `logfire.instrument_pydantic_ai()` also has nothing to instrument here, since there's no Pydantic AI.
- **`opentelemetry-api` only**, as a hard dependency. It's a no-op when no SDK is configured, so a consumer who doesn't want tracing pays nothing. SDK and exporter live in an `[otel]` extra.
- **The library never touches global tracing state.** No `set_tracer_provider()`, no reading `OTEL_*` env vars. `decode` configures its exporter programmatically for a related reason — it had to avoid clobbering a vendored OpenTelemetry SDK belonging to another dependency. The general form of that lesson: a library that hijacks the global provider breaks any application that configured one first.

Chapter 10.
