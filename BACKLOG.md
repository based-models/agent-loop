# Backlog

Work that was deliberately deferred, with enough context to pick it up cold.

## Documentation for agentloop 0.2 (hosted tools, Responses, `Origin`)

**Status:** not done. The 0.2 code landed without doc changes; `docs/` still describes
0.1. Everything below is user-facing behaviour that changed or is new. The design
rationale is in `~/projects/agentloop-tools/DESIGN.md`; lift from it, don't restate it.

**Stale pages (wrong today):**

- `docs/api/agentloop.md`: `__version__` is `"0.2.0"`. New exports: `HostedTool`,
  `HostedToolPart`, `Source`, `Citation`, `Origin`, `join_text`.
- `docs/api/transcript.md` also: `final_text()` now joins text parts with `join_text`, which
  adds a space between separate blocks but not across Anthropic's citation splits or
  before closing punctuation. It used to concatenate with no separator.
- `docs/api/providers/openai.md`: `build_openai_client(..., api="responses")` is new, and
  Responses is now the default. `api="completions"` keeps Chat Completions. The returned
  client gains `api` and `hosted_tools`. `QUIRKS`' `max_completion_tokens` applies to
  Completions only (Responses sends `max_output_tokens`).
- `docs/api/providers/openrouter.md`, `modal.md`: `OpenAICompatClient(..., api=...)`,
  `api="completions"` for both, `hosted_tools` empty. OpenRouter now replays
  `reasoning_details` (see `Origin` below).
- `docs/api/providers/anthropic.md`:
  - `HOSTED_TOOLS`, `API = "messages"`;
  - `WEB_SEARCH_BASIC` (`web_search_20250305`, the DEFAULT) / `WEB_SEARCH_FILTERED`
    (`web_search_20260209`, only when the option `dynamic_filtering` is `True`);
  - `hosted_tool_to_wire`;
  - `pause_turn` maps to `PAUSED` (no longer `UNKNOWN`);
  - assistant turns replay from `origin.raw`.
- `docs/api/provider.md`:
  - `StopReason.PAUSED`;
  - `HostedTool`;
  - `ModelRequest.hosted_tools`, `ModelResponse.origin`;
  - `supported_hosted_tools(client)`;
  - the optional `hosted_tools` attribute a client may expose.
- `docs/api/transcript.md`:
  - `ContentPart` now includes `HostedToolPart`;
  - `TextPart.citations`; `Source`, `Citation`, `Origin`;
  - `Message.origin`;
  - `add_assistant_message(parts, origin=None)` and `extend_last_assistant`;
  - the new `check_invariants` rule (hosted parts only in assistant messages).
- `docs/api/agent.md`: `Agent(tools: Sequence[ToolSpec | HostedTool])`. Construction raises
  `ConfigurationError` for a hosted tool the client cannot run, and for a `ToolSpec` named
  like a hosted tool.
- `docs/api/loop.md`: `run_conversation(..., hosted_tools=())`, and the PAUSED
  continuation (no user message; it joins the same assistant turn; counts as an iteration).
- `docs/api/config.md`, `docs/getting-started/configuration.md`: `OPENAI_API`
  (`responses` | `completions`, default `responses`) and `Settings.openai_api`.
- `docs/api/tracing.md`: the `agentloop.hosted_tool.calls` span attribute (a count only).
- `docs/api/errors.md`, `docs/guides/retries-and-errors.md`: an OpenAI 429 whose error
  `type`/`code` is `insufficient_quota`/`credit_balance_exhausted` is now
  `InsufficientCredits` (not retried), not `RateLimited`.
- `docs/guides/custom-providers.md`: `StopReason.PAUSED`. Optionally, how a custom client
  opts into hosted tools (`hosted_tools` attribute) and exact replay (`ModelResponse.origin`).

**New content needed:**

- `docs/guides/providers.md`: Chat Completions vs Responses. What differs, which
  providers speak which, why Responses runs **stateless** (`store: false`, full transcript
  resent, item ids stripped on replay), and how to switch (`OPENAI_API`).
- A **hosted tools / web search** guide:
  - `native_web_search(...)` from agentloop-tools, with its options and each provider's
    handling (OpenAI `search_context_size`; Anthropic `dynamic_filtering`, see below);
  - the option policy (unknown and restriction options raise; tuning is logged);
  - **Sources vs citations: the two evidence readers mean different things.** Say so
    plainly, with a table:

    | | `sources(transcript)` | `citations(transcript)` |
    |---|---|---|
    | What it is | Every page the provider's search returned (`Source(url, title)`), deduplicated | A claim in the model's text tied to a page (`Citation`) |
    | Comes from | `HostedToolPart.sources` (search result blocks / `web_search_call.action.sources`) | `TextPart.citations` |
    | Anthropic, basic search (default) | Filled | Filled, with `cited_text` (the quoted source text) |
    | Anthropic, `dynamic_filtering=True` | Filled | **Always empty** (see below) |
    | OpenAI Responses | Filled (requires the `include` agentloop sends) | Filled, with `start`/`end` (a span of the answer), no `cited_text` |
    | Use it for | "What did it look at?" A reading list, an audit trail | "Where did this sentence come from?" Footnotes, fact-checking |

    A source is not evidence that the answer used the page; a citation is. Neither is
    readable page text: Anthropic encrypts result content, and OpenAI returns only URLs.
  - **The `dynamic_filtering` flag (Anthropic only; default `False`).** Explain what it
    does, and the trade-off:
    - *What it does:* `True` sends `web_search_20260209`. Claude writes and runs a Python
      program (in an auto-provisioned code-execution sandbox) that calls the search,
      filters the results, and prints what it keeps. Claude reads only that printed
      output. `False` (the default) sends `web_search_20250305`: results go straight
      into the model's context as search-result blocks.
    - *Why citations vanish when it is on:* a citation must point at a search-result
      block in the model's context, and with filtering on the results go to the program,
      not the model. Measured live (Sept 2026, `claude-opus-5`, 2 questions × 3 variants):
      filtering on gave 0 citations in every response; basic search gave 2–3;
      `web_search_20260209` with `allowed_callers: ["direct"]` (new version, no code in
      the path) gave 2–5. It is the code path, not the tool version. The answer text still
      arrives split where citations would sit, each block with `citations: []`, and the
      model writes a "Sources:" list in prose instead. Show one example response of each
      (use the frozen captures in `agentloop-tools/experiments/examples/citations/`,
      described in the README there, e.g. `shannon_A_dynamic.json` against
      `shannon_B_basic.json`. The whole `experiments/` folder is gitignored, so these
      exist only on the machine that ran the experiment. On a fresh clone, rerun
      `experiments/citations_experiment.py` first (it writes to
      `experiments/citation_runs/`; 6 requests, a few cents).
    - *Cost:* dynamic filtering did NOT save tokens on single, simple searches (input
      13,300 vs 12,827). Anthropic pitches it for search-heavy requests; recommend it
      only there, when citations are not needed.
    - *Constraints when on:* Claude 4.6+ only; not ZDR-eligible (code execution);
      response carries extra `server_tool_use` / `code_execution_tool_result` blocks
      (kept in `origin.raw`, surfaced as a `HostedToolPart` of kind `code_execution`).
    - *OpenAI:* has no such option; setting `dynamic_filtering=True` logs a warning and
      is ignored.
    - *Changelog note:* the default was `True` before this landed and was flipped to
      `False` because it silently disabled citations.
  - approval being per request, not per search;
  - native vs client search as separate choices.
  - **Domain policy (`agentloop_tools.web.DomainPolicy`).** A blocklist or allowlist
    that does two things, prevent and report, and deliberately nothing else:
    - *Modes:* `DomainPolicy.block([...])` or `DomainPolicy.allow([...])`, one or the
      other, as both providers require.
    - *Matching:* a rule covers its host and every subdomain but not a longer label
      (`europa.eu` covers `eur-lex.europa.eu`, not `noteuropa.eu`), plus an optional path
      prefix where `*` matches within the path. The same rules as the providers.
    - *Prevent:* `policy.native_web_search(**options)` sends the list to the provider:
      `blocked_domains` / `allowed_domains` on Anthropic, `filters.*` on OpenAI.
      Passing a domain option there as well is an error (one source of truth).
    - *Report:* `policy.audit(transcript, since=n)` returns `Violation(url, where,
      message_index, rule)` for every place an assistant turn reached a domain the policy
      does not permit. `where` is `"source"` (the provider filter leaked), `"citation"`
      or `"text"`. `rule` is the matched blocklist entry, or `None` for "not on the
      allowlist". To audit one turn, pass `since=len(transcript.messages)` taken before
      `converse`.
    - *Text scanning boundaries:* full URLs in both modes (markdown links included, and
      balanced parentheses kept, e.g. Wikipedia's `.../Python_(programming_language)`).
      Bare host mentions ("see wikipedia.org") in block mode only. Allow mode checks full
      URLs only, because a generic host pattern would flag `config.py`.
    - *The hard limit, stated plainly:* with native search the model reads the results
      inside the provider's response, so no library can stop it READING a page. The
      policy prevents what the provider can and reports the rest. The only true pre-model
      filter is client-side search (backlogged).
    - *What it does NOT do:* no hiding, no redaction, no transcript edits, no retry or
      raise. Handling a violation (log, raise, re-ask, warn the user) is the developer's
      choice. Show one example of each.
    - *Live evidence (Sept 2026), for the examples:*
      - `block(["wikipedia.org"])` held on both providers (0 leaked sources out of 10 and
        31);
      - asked for Wikipedia's URL, Claude wrote 4 Wikipedia links from its own knowledge,
        which the audit reported as `"text"` violations;
      - `allow(["python.org"])` on OpenAI returned 27 sources, all on-list.
- `docs/guides/conversations.md` / `transcript-storage.md`: what `Message.origin` is, why
  it can be large (encrypted search results), and that JSONL keeps it so a resumed
  conversation replays exactly.
- `agentloop-tools` has no user docs beyond `DESIGN.md`: a README with install and a
  `native_web_search` example.

**Verify while writing:** each example runs. The behaviour itself was verified live
against all three providers; see "Live verification" in `agentloop-tools/DESIGN.md` for
what was confirmed (and the one thing, `pause_turn`, that was not triggered).

**Why it was deferred:** the code was reviewed and live-tested first (September 2026);
docs were split out on request.

## Client-side web search (agentloop-tools)

**Status:** not implemented. Native search (the provider runs it) shipped in agentloop 0.2
as `HostedTool` + `agentloop_tools.native_web_search`. Client-side search is the OTHER
first-class option, chosen explicitly by the developer and never a fallback for native:
the app's own code calls a search API, so it works on every provider (OpenRouter, Modal,
Completions), results are visible and loggable, and each call goes through `before_tool`.

**What implementing it takes** (all in `agentloop-tools`, a `[web]` extra pulling httpx):

1. `web_search(backend, *, http, name="web_search", allowed_domains=(),
   blocked_domains=(), max_results=5) -> ToolSpec`. Strict schema `{query, max_results}`;
   numbered hits (`[n] title / url / snippet`); domain filters applied to the hits, with the
   same semantics as native; a backend failure raises `ToolError`. `name=` lets it sit
   beside hosted `web_search` on Anthropic (`Agent` rejects the clash otherwise).
   **Take a `policy: DomainPolicy | None` and drop every hit where
   `policy.permits(hit.url)` is false BEFORE formatting the result.** This is the one
   place a blocklist or allowlist becomes a guarantee: the model never sees the page.
   (`DomainPolicy` already exists in `agentloop_tools.web`; reuse its matching, don't
   re-implement it.)
2. `SearchBackend` protocol + `SearchHit(title, url, snippet, published)`, with one
   concrete backend (choice open: Brave, Tavily, Exa). It receives the app's
   `httpx.Client` and API key and never reads the environment.
3. `web_fetch(http, *, max_bytes, allowed_domains=()) -> ToolSpec`: http(s) only; an SSRF
   guard (refuse loopback, private, link-local and cloud-metadata addresses AFTER DNS
   resolution); a content-type check; stdlib `html.parser` HTML to text; paging with
   `offset` and a "N more lines; use offset=..." hint; a byte cap.
4. `_text.py`: shared line numbering, truncation hints, byte cap, `errors="replace"`
   decoding. The coding family will need the same.
5. Tests: `httpx.MockTransport` only; strict-dialect schemas; handlers total through
   `run_tools`; the SSRF guard against `127.0.0.1`, `169.254.169.254` and a DNS name that
   resolves privately.

**Why it was deferred:** native search came first (September 2026); client search is
independent of it.

## Web search over Chat Completions (`gpt-5-search-api`)

**Status:** not implemented. The Completions codec refuses `hosted_tools` with a
`BadRequestError` pointing at `api="responses"`. Chat Completions has no hosted tools;
OpenAI's search there is a dedicated model (`gpt-5-search-api`) driven by a
`web_search_options` request field, with `url_citation` annotations on the message.

**What implementing it takes:** map `HostedTool("web_search")` to `web_search_options`
(`user_location`, `search_context_size`) when the configured model is a search model;
parse `message.annotations` into `TextPart.citations`; set the Completions codec's
`hosted_tools` per model rather than per codec. Domain filters have no Completions
equivalent, so they must raise (a dropped restriction widens access).

**Why it was deferred:** Responses is OpenAI's current API and supports search on its
general models; this path only matters for a Completions-only deployment.

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
