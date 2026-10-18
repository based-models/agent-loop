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

Items 7–12 (truncated-call retry cost, empty truncated turns, made-up tool names in telemetry,
tool-handler timeouts, `OTelObserver` redaction, and the stale 0.1-era docs) landed on
`failure-telemetry`. The numbers stay so references remain valid.

Left over:

- **Empty `END_TURN` (item 8).** An empty non-truncated turn is still `ResponseInvalid` and is
  retried verbatim. Decide whether it should end the conversation with empty text or be retried
  once with a nudge.
- **Hung tool threads (item 10).** A timed-out handler's thread is abandoned, not killed. A tool
  that holds a lock or connection keeps it. `ToolSpec.timeout_seconds` defaults to `None`.
- **Shared redaction helpers (item 11).** `events.py` and `tracing.py` each implement
  redact-then-clip; move them to one module so they cannot drift.
- **Hosted-tools guide examples (item 12).** The `agentloop-tools` parts of
  `docs/guides/hosted-tools.md` (`native_web_search`, `DomainPolicy`) were written from this
  backlog and not run here.

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
