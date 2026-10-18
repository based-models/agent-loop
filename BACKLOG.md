# Backlog

Work that was deliberately deferred, with enough context to pick it up cold. Ordered by
priority, highest first. Last reviewed 2026-10-18 against `failure-telemetry` (`f0abed9`,
PR 11), which holds the unreleased `[Unreleased]` changelog entries.

**Release blockers** are marked **[blocks next release]**. They are defects in, or
decisions about, work that is in `[Unreleased]` and not yet on PyPI. Fixing them after
the release means shipping a public API that later has to change, or docs that are wrong
on day one.

---

## Must land before the next release (0.6.0)

Items 1–4 (Anthropic transport classification, the Anthropic `http_client` type and
ownership, `phase` / `request_sent` on body errors, and the API reference pages) landed on
`failure-telemetry`. The numbers below are kept so existing references stay valid.

Left over from item 2, optional: `pyproject.toml` still allows `anthropic>=1.0`, which
includes SDKs built on httpx rather than httpx2. `AnthropicClient` reads the expected
client class from the installed SDK, so either works, but the type hints and docs name
`httpx2.Client`. Raise the floor to the first httpx2-based SDK once that version is known.

### 5. Decide: failed turns report `0` tokens instead of "not reported" [decide before next release]

**Status:** not implemented. `TurnRecord.input_tokens` / `output_tokens` are plain
`int = 0` (`summary.py`). `_Run._add_turn` (`loop.py`) fills `0` for a call that raised
before answering. The project rule is that `None` means "not reported", never zero.

**Concrete trigger:** set `SummaryOptions(timeline=True)` and let one model call fail. The
failed `TurnRecord` reports 0 input and 0 output tokens. That reads as "measured, and
free". A consumer summing the timeline can't tell a failed call from a cheap one without
also checking `error_type`.

**What implementing it takes:**
- Make both fields `int | None`, set them to `None` when `response is None`, and update
  anything that sums them.
- Add a CHANGELOG "Changed" entry.

`TurnRecord` is in `__all__`, so this breaks code doing arithmetic on the fields. **Why it
is a release decision:** 0.6.0 is already a minor bump that changes `LoopObserver`. Making
this change in the same release costs nothing extra. Making it later costs another
breaking release. Either do it now, or record that it waits.

### 6. Release housekeeping for PR 11 [blocks next release]

- Merge Dependabot PR 10 (`actions/upload-artifact` 7.0.2 and `download-artifact` 8.0.2 in
  `release.yml`; CI green) before tagging. Otherwise the release runs on the older
  actions.

---

## High priority

### 7. A truncated tool call can repeat at full cost until the iteration limit

**Status:** bug. `loop.py` `_Run.drive`, after a turn ends with `_is_truncated_mid_call`:

```python
self.max_tokens = min(self.max_tokens * 2, MAX_TOKENS_CEILING)
```

- **At the ceiling, nothing changes and it retries anyway.** Suppose a model keeps writing
  a tool call larger than 64,000 output tokens (a big file write). Every iteration then
  discards a full 64k-token turn and asks again with the same budget, up to
  `max_iterations` (default 50). That is up to 3.2M billed output tokens for an answer
  that can never fit.
- **Above the ceiling, the budget shrinks.** A caller who passes `max_tokens=100_000` is
  cut to 64,000 on the first truncation. That makes the next truncation more likely.

**What implementing it takes:**
- Never lower a budget: `max(self.max_tokens, min(self.max_tokens * 2, CEILING))`.
- When a truncation happens at a budget that can no longer grow, stop instead of retrying
  blind. One option is a non-retryable error, or a dedicated `ConversationStatus`, that
  says "the tool call does not fit in max_tokens".
- Add a test with `FakeClient` that scripts repeated truncations at the ceiling.

### 8. An empty turn is retried as if it were transient

**Status:** design gap. `ModelResponse.content` requires `min_length=1`, so an empty
assistant turn becomes `ResponseInvalid`. That error is `retryable = True`, so
`RetryingClient` re-sends the identical request.

Two real cases where a retry is wasted money:

- **Completions, `finish_reason: "length"` with empty content.** A reasoning model spends
  the whole budget on hidden reasoning. With no readable reasoning there is no
  `ThinkingPart`, so the turn is empty. It is retried `max_attempts` times at the same
  `max_tokens`, every attempt billed, and then the conversation fails. The loop's
  `max_tokens` doubling (item 7) never runs, because the loop never sees a response.
- **Anthropic, an empty `end_turn` right after tool results.** This is a known Claude
  behaviour, and re-sending the same request does not reliably change it.

**What implementing it takes:** treat "empty and truncated" as truncation, not
invalidity. Give the loop a way to see it, either a distinct non-retryable error or
`ModelResponse` allowing empty content when `stop_reason` is `MAX_TOKENS`, so it can raise
the budget or report `TRUNCATED`. Decide separately whether an empty `END_TURN` should
end the conversation with empty text or be retried once with a nudge.

### 9. Tool names the model made up reach summaries, metrics and span attribute keys

**Status:** breaks the "content-free" and "low-cardinality metrics" invariants (CLAUDE.md).
`SummaryCollector.on_tool_end`, `RepeatTracker`, `MetricsObserver` (`tool` attribute) and
`OTelObserver.on_conversation_end` (`agentloop.tool.calls.{name}` attribute **keys**) all
take the name from `call.tool_name`. Those names are whatever the model wrote, including
calls to tools that are not registered. `_execute` answers those with "Unknown tool", but
the name has already been counted.

**Concrete trigger:** a model invents a new tool name each run (common with small
open-weight models on Modal or OpenRouter). Each name becomes a new metric series and a
new span attribute key, and model-written text ends up in a "content-free" summary.

**What implementing it takes:** in `_run_one` / the collector, report unregistered names
under one fixed bucket such as `"<unregistered>"`. The executor still answers the model
with the real name. Apply the same mapping in `RepeatTracker`'s reported name, the metrics
and the root span. Add a test asserting that a hallucinated name never appears in a
summary or metric.

### 10. No timeout on individual tool-handler execution

**Status:** not implemented. `run_conversation`'s `deadline_seconds` is checked only
*between* model calls (`loop.py`). Neither `tooling.run_tools`'s single-call path nor its
`ThreadPoolExecutor` path (`future.result()`, no `timeout=`) limits how long a tool
handler may run.

**Concrete trigger:** register a tool whose handler makes a network call with no timeout of
its own, to an endpoint that never responds. The whole agent loop hangs forever, even with
`deadline_seconds` set. The deadline only fires before the *next* model call, and that
call never comes.

**What implementing it takes:** a per-tool or per-call timeout, probably a `ToolSpec` field
that `run_tools` passes to `future.result(timeout=...)`, with an equivalent guard on the
single-call path. It needs a decision on what happens to a handler thread still running
after its timeout fires: Python cannot kill a thread.

### 11. `OTelObserver` has no `redact`, unlike `EventTraceObserver`

**Status:** not implemented. `EventTraceObserver(capture_content=True)` refuses to start
without a `redact` function, or an explicit `allow_unredacted=True`, and redacts every
captured string before clipping it. `OTelObserver(capture_content=True)` takes no `redact`.
System instructions, tool arguments and results, and error and retry messages go to the
exporter as written. `ERROR_DETAIL_CHARS` (200) limits their size, not their content.
`Agent(trace=True)` cannot enable capture at all, since `_with_tracing` does not pass
`capture_content` through.

**Concrete trigger:** capture is on to debug a tool, and a tool result or a provider's 400
body quotes an API key or a customer record. The event trace would redact it; the span
exports it.

**What implementing it takes:**
- Add `redact: Callable[[str], str] | None` and `allow_unredacted` to `OTelObserver`, with
  the event trace's rules: capture without either raises `ConfigurationError`; redact
  before clipping; a redactor that raises drops the attribute instead of writing it.
- Decide whether `Agent(trace=...)` should accept capture options or keep capture off by
  design.
- Optionally move the redaction helpers in `events.py` to a shared module, so the two
  observers cannot drift.

### 12. Documentation still describing 0.1 behaviour (hosted tools, Responses, `Origin`)

**Status:** partly done. Already written: the `docs/api/agentloop.md` exports,
`docs/api/config.md` / `getting-started/configuration.md` for `OPENAI_API`, and the
`provider.md` coverage of `PAUSED` / `HostedTool` / `hosted_tools` / `origin`. Also
`transcript.md`'s `HostedToolPart` / `citations` / `Source` / `Citation` /
`Message.origin`, and the agentloop-tools README.

**Stale pages (wrong today):**

- `docs/api/transcript.md`:
  - `final_text` is still described as "Concatenates". It now uses `join_text`, which
    puts a space between separate blocks, but not across Anthropic's citation splits or
    before closing punctuation.
  - Missing: `add_assistant_message(parts, origin=None)`, `extend_last_assistant`, and the
    `check_invariants` rule that hosted parts appear only in assistant messages.
- `docs/api/provider.md`: `supported_hosted_tools(client)`, and the optional
  `hosted_tools` attribute a client may expose.
- `docs/api/agent.md`: a `ToolSpec` named like a hosted tool raises `ConfigurationError`.
- `docs/api/loop.md`: the `PAUSED` continuation. It sends no user message, joins the same
  assistant turn, and counts as an iteration.
- `docs/api/providers/openrouter.md`, `modal.md`: `api="completions"` and empty
  `hosted_tools` for both. OpenRouter replays `reasoning_details` from `Origin`.
- `docs/api/providers/anthropic.md`:
  - `HOSTED_TOOLS` and `API = "messages"`.
  - `WEB_SEARCH_BASIC` (`web_search_20250305`) is the default. `WEB_SEARCH_FILTERED`
    (`web_search_20260209`) is sent only when `dynamic_filtering=True`.
  - `hosted_tool_to_wire`.
  - `pause_turn` maps to `PAUSED`.
  - Assistant turns are replayed from `origin.raw`.
- `docs/api/tracing.md`: the `agentloop.hosted_tool.calls` span attribute (a count only).
- `docs/api/errors.md`, `docs/guides/retries-and-errors.md`: an OpenAI 429 whose
  `type`/`code` is `insufficient_quota` / `credit_balance_exhausted` is
  `InsufficientCredits` (not retried).
- `docs/guides/custom-providers.md`: `StopReason.PAUSED`. Also how a custom client opts in
  to hosted tools (the `hosted_tools` attribute) and to exact replay
  (`ModelResponse.origin`).

**New content needed:**

- `docs/guides/providers.md`: Chat Completions vs Responses.
  - What differs, and which providers speak which.
  - Why Responses runs **stateless**: `store: false`, the full transcript resent, item ids
    stripped on replay.
  - How to switch: `OPENAI_API`.
- `docs/guides/conversations.md` / `transcript-storage.md`: what `Message.origin` is, why
  it can be large (encrypted search results), and that JSONL keeps it so a resumed
  conversation replays exactly.
- A **hosted tools / web search** guide:
  - `native_web_search(...)` from agentloop-tools: its options and each provider's handling
    (OpenAI `search_context_size`; Anthropic `dynamic_filtering`, below).
  - The option policy: unknown and restriction options raise; tuning options are logged.
  - Approval is per request, not per search.
  - Native and client-side search are separate choices.
  - **Sources vs citations: the two evidence readers mean different things.** Say so
    plainly, with this table:

    | | `sources(transcript)` | `citations(transcript)` |
    |---|---|---|
    | What it is | Every page the provider's search returned (`Source(url, title)`), deduplicated | A claim in the model's text tied to a page (`Citation`) |
    | Comes from | `HostedToolPart.sources` (search result blocks / `web_search_call.action.sources`) | `TextPart.citations` |
    | Anthropic, basic search (default) | Filled | Filled, with `cited_text` (the quoted source text) |
    | Anthropic, `dynamic_filtering=True` | Filled | **Always empty** (see below) |
    | OpenAI Responses | Filled (needs the `include` agentloop sends) | Filled, with `start`/`end` (a span of the answer), no `cited_text` |
    | Use it for | "What did it look at?" A reading list, an audit trail | "Where did this sentence come from?" Footnotes, fact-checking |

    A source is not evidence that the answer used the page; a citation is. Neither one is
    readable page text: Anthropic encrypts result content, and OpenAI returns only URLs.
  - **`dynamic_filtering` (Anthropic only; default `False`).**
    - *What it does:* `True` sends `web_search_20260209`. Claude writes and runs a Python
      program, in an auto-provisioned code-execution sandbox, that calls the search,
      filters the results and prints what it keeps. Claude reads only that output.
      `False` sends `web_search_20250305`, and results go straight into the model's
      context.
    - *Why citations disappear:* a citation must point at a search-result block in the
      model's context, and with filtering the results go to the program instead.
      Measured live in September 2026 (`claude-opus-5`, 2 questions × 3 variants):
      filtering on gave 0 citations every time; basic search gave 2–3;
      `web_search_20260209` with `allowed_callers: ["direct"]` gave 2–5. So the cause is
      the code path, not the tool version.
    - *Examples to show:* one response of each. Use the frozen captures in
      `agentloop-tools/experiments/examples/citations/`, for example
      `shannon_A_dynamic.json` next to `shannon_B_basic.json`. `experiments/` is
      gitignored, so on a fresh clone rerun `experiments/citations_experiment.py` first
      (6 requests, a few cents).
    - *Cost:* it did NOT save tokens on single simple searches (13,300 vs 12,827 input
      tokens). Recommend it only for search-heavy requests that don't need citations.
    - *Constraints:* Claude 4.6+ only; not ZDR-eligible; the response carries extra
      `server_tool_use` / `code_execution_tool_result` blocks, surfaced as a
      `HostedToolPart` of kind `code_execution`.
    - *OpenAI:* has no equivalent. `dynamic_filtering=True` logs a warning and is ignored.
  - **Domain policy (`agentloop_tools.web.DomainPolicy`).** It prevents and reports, and
    deliberately does nothing else.
    - *Modes:* `DomainPolicy.block([...])` or `DomainPolicy.allow([...])`, not both.
    - *Matching:* a rule covers its host and every subdomain, but not a longer label
      (`europa.eu` covers `eur-lex.europa.eu`, not `noteuropa.eu`). A rule may add a path
      prefix in which `*` matches within the path.
    - *Prevent:* `policy.native_web_search(**options)` sends the list to the provider.
      Passing a domain option there as well is an error.
    - *Report:* `policy.audit(transcript, since=n)` returns `Violation(url, where,
      message_index, rule)`. `where` is `"source"`, `"citation"` or `"text"`. To audit one
      turn, pass the `len(transcript.messages)` taken before `converse`.
    - *Text scanning:* full URLs in both modes, with balanced parentheses kept. Bare host
      mentions are checked in block mode only.
    - *The hard limit:* with native search, no library can stop the model READING a page.
      The policy prevents what the provider can and reports the rest. Only client-side
      search (item 22) filters before the model sees anything.
    - *Live evidence (September 2026):*
      - `block(["wikipedia.org"])` held on both providers (0 leaked sources out of 10
        and 31).
      - Asked for Wikipedia's URL, Claude wrote 4 Wikipedia links from memory, which the
        audit reported as `"text"` violations.
      - `allow(["python.org"])` on OpenAI returned 27 sources, all on the list.

**Verify while writing:** each example runs.

---

## Medium priority

### 13. Resuming a transcript that ends mid-turn sends an invalid request

**Status:** gap. `Agent.converse` / `run_conversation` send `transcript.messages` as they
are and never call `check_invariants()`. `from_jsonl` deliberately loads mid-turn logs, so
this is a supported state.

**Concrete triggers:**
- The process dies while tools run, and the saved transcript ends with an assistant message
  whose tool calls have no results. Resuming sends unanswered calls, every provider rejects
  them with a 400, and that `BadRequestError` is not retried.
- A turn paused (`StopReason.PAUSED`) and then hit a provider error. The new `_Run` starts
  with `continuing=False`, so the continuation becomes a second consecutive assistant
  message instead of extending the first.

**What implementing it takes:** a preflight in `drive()`. Pending tool calls should be
either executed before the first model call (a natural resume) or rejected with a
`TranscriptError` that says how to fix it. That is an API decision. For the paused case,
`Message` would have to record that the turn is open, for example in `Origin`.

### 14. Verify Responses reasoning replay without `include: ["reasoning.encrypted_content"]`

**Status:** needs a live check. The docstring in `_openai_responses.py` says the encrypted
reasoning content "`store: false` returns by default". OpenAI's API reference documents it
as an opt-in `include` value for stateless use. If it is opt-in, reasoning items are
replayed without their encrypted content. Reasoning models then lose their chain of
thought across tool calls, or the request is rejected.

**What implementing it takes:** run one tool-using turn on a reasoning model live and check
`output[*].encrypted_content`. If it is missing, add `"reasoning.encrypted_content"` to
`include` whenever the model may reason, and save the captured body as a fixture (item 21).

### 15. No way to mark a tool as unsafe to run in parallel

**Status:** not implemented. `run_tools` always runs a turn with two or more tool calls in
parallel, on a fixed `ThreadPoolExecutor(max_workers=8)` (`tooling.py`). There is no
`ToolSpec` flag for a handler that isn't thread-safe: a shared DB cursor, a non-atomic
shared counter, an SDK client not documented as thread-safe.

**Concrete trigger:** two tools that both mutate the same plain `list` or
`sqlite3.Connection` without a lock, called by the model in one turn. Corruption or
exceptions show up only intermittently, when a model happens to call them together.

**What implementing it takes:** a `ToolSpec.parallel_safe: bool = True` (or similar) that
`run_tools` checks. Any turn that contains an unsafe call runs sequentially.

### 16. No per-provider timeout override through `Settings` / `build_client()`

**Status:** not implemented. Timeouts are hard-coded per adapter (`anthropic.py`'s
`DEFAULT_TIMEOUT_SECONDS`, the `TIMEOUT_SECONDS` dicts in `openrouter.py`, `modal.py` and
`openai.py`). Someone who needs a longer read timeout must call `build_<provider>_client()`
directly and give up `Settings.from_env()`. Item 2's `http_client` does not cover the
Anthropic path, and its timeout heuristic is fragile (item 23).

**What implementing it takes:** an optional `timeout_seconds` (or a richer shape) on
`Settings`, passed through each `_build_*` helper in `config.py` to the provider builder's
`timeout=`.

### 17. Model-aware provider `Quirks`

**Status:** not implemented. `Quirks` is chosen once per *provider*, not per *model*.
`providers/openai.py` sends `parallel_tool_calls` on every request. OpenAI's reasoning
models reject that parameter and its non-reasoning chat models accept it, and the only way
around it is two hand-built clients.

**What implementing it takes:** a second selection axis, probably a small `model -> Quirks`
override table (regex or prefix match) that `build_openai_client(model=...)` checks before
falling back to the provider default. It needs real design work: what to match on, what
happens when nothing matches, and how Modal's provider-wide quirks interact.

### 18. Tracing setup fails silently (`configure_tracing()` twice; `trace=True` without it)

**Status:** not implemented. Two symptoms with one cause:

- OpenTelemetry's global `trace.set_tracer_provider()` takes effect only once per process.
  A second `configure_tracing()` returns a provider that looks live but receives no spans.
  The real first provider may then never be flushed.
- `Agent(trace=True)` without `configure_tracing()` throws every span away, and nothing
  tells the user.

**What implementing it takes:** detect reliably whether the global provider is still the
default proxy or no-op one. Then warn (or raise) on a second install, and log one warning
the first time `OTelObserver.conversation()` runs with no provider installed. The
detection has to be tested across the OpenTelemetry SDK versions the package supports.

---

## Low priority

### 19. Inconsistent error type for missing credentials

`config.py` raises `AuthError` (a `ProviderError`, implying the provider was contacted) for
a missing `OPENROUTER_API_KEY` / `OPENAI_API_KEY`. For a missing `MODAL_ENDPOINT_URL` it
raises `ConfigurationError`. Nothing was sent in either case. Raising `ConfigurationError`
for both matches its docstring ("before any provider is touched"). This is a behaviour
change for anyone catching `AuthError`, so it needs a CHANGELOG entry.

### 20. `Retry-After` longer than `max_delay` guarantees a wasted attempt

`RetryingClient._delay_for` caps a server's `Retry-After` at `max_delay` (30s), retries
early, and is likely to be rate-limited again. When `retry_after > max_delay`, it would be
better to stop and raise at once, with `retry_after` on the error, so the caller can
reschedule. HTTP-date `Retry-After` values are ignored (`parse_retry_after`).

### 21. Recorded wire-body fixtures

`tests/fixtures/` does not exist. The adapters' inline tests cover the known quirks. Add
real captured bodies as they turn up: error-in-200, a plain-text 401, missing usage
details, an `httpx2` connection failure, and a Responses reasoning item (item 14).

### 22. Client-side web search (implemented in agentloop-tools)

**Status:** not implemented. Recorded here because it is the counterpart to this
package's hosted tools.

Native search, where the provider runs the search, shipped in 0.2. Client-side search is
the other explicit option: the app's own code calls a search API, so it works on every
provider, its results can be logged, and each call goes through `before_tool`.

**What implementing it takes** (a `[web]` extra in agentloop-tools):
1. `web_search(backend, *, http, name="web_search", policy: DomainPolicy | None = None,
   max_results=5) -> ToolSpec`.
   - A strict `{query, max_results}` schema, and numbered hits (`[n] title / url /
     snippet`).
   - A backend failure raises `ToolError`.
   - Drop every hit where `policy.permits(hit.url)` is false BEFORE formatting. This is the
     only place a domain policy becomes a guarantee.
   - Reuse `DomainPolicy`'s matching rather than re-implementing it.
   - `name=` lets it sit beside hosted `web_search`.
2. A `SearchBackend` protocol and `SearchHit(title, url, snippet, published)`, with one
   concrete backend (Brave, Tavily or Exa). It receives the app's HTTP client and key and
   never reads the environment.
3. `web_fetch(http, *, max_bytes, allowed_domains=()) -> ToolSpec`.
   - http(s) only.
   - An SSRF guard that refuses loopback, private, link-local and cloud-metadata addresses
     AFTER DNS resolution.
   - A content-type check, and stdlib `html.parser` HTML to text.
   - `offset` paging with a "N more lines" hint, and a byte cap.
4. Tests using `httpx.MockTransport` only, including the SSRF guard against `127.0.0.1`,
   `169.254.169.254` and a name that resolves to a private address.

### 23. `OpenAICompatClient` guesses whether a supplied client chose its own timeout

`chose_own = http_client.timeout != httpx.Timeout(5.0)`: a caller who deliberately sets
exactly 5 seconds gets the adapter's timeout instead. An explicit `timeout=` argument (item
16) would make the guess unnecessary.

### 24. Web search over Chat Completions (`gpt-5-search-api`)

**Status:** not implemented. The Completions codec refuses `hosted_tools` with a
`BadRequestError` that points at `api="responses"`. On Chat Completions, OpenAI's search is
a dedicated model driven by `web_search_options`, which returns `url_citation`
annotations.

**What implementing it takes:**
- Map `HostedTool("web_search")` to `web_search_options` when the model is a search model.
- Parse `message.annotations` into `TextPart.citations`.
- Set the codec's `hosted_tools` per model (item 17).
- Domain filters have no equivalent there, so they must raise.

Only matters for a deployment limited to Completions.

### 25. `async` support (`AsyncLLMClient`, `Agent.arun()`)

**Status:** deliberately not built. `client.complete()` spends its time blocked on a socket,
and both SDKs release the GIL while they wait. `agent.run` from N threads, or
`await asyncio.to_thread(agent.run, prompt)`, covers concurrency today with no changes.
`async`/`await` only pays off at four-figure concurrent conversations per process.

**If that threshold is reached:** the canonical types and every adapter's translation
functions do no I/O and can be reused as they are. Observers need no async variants. New
async twins are needed for:
- `LLMClient`;
- `AnthropicClient` (`anthropic.AsyncAnthropic`);
- one `AsyncOpenAICompatClient` (`httpx.AsyncClient`);
- `RetryingClient` (`asyncio.sleep`);
- `run_tools` (`asyncio.gather` / `TaskGroup`, keeping the guarantee that every call gets
  a result);
- `run_conversation`, `Agent.arun` / `aconverse`, and `build_async_client`.

The real cost is doubling the provider, loop and tool test surface, plus a new dev
dependency (`pytest-asyncio` or `anyio`).
