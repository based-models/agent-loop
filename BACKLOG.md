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

Nothing open. Items 1–6 landed: 1–4 (Anthropic transport classification, the Anthropic
`http_client` type and ownership, `phase` / `request_sent` on body errors, the API reference
pages) in PR 11; 5 (a failed turn's tokens are `None`, not `0`) and 6 (Dependabot PR 10 and
the `main` merge) after it. The numbers below are kept so existing references stay valid.

Left over from item 2, optional: `pyproject.toml` still allows `anthropic>=1.0`, which
includes SDKs built on httpx rather than httpx2. `AnthropicClient` reads the expected
client class from the installed SDK, so either works, but the type hints and docs name
`httpx2.Client`. Raise the floor to the first httpx2-based SDK once that version is known.

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
- **Companion package version compatibility.** `based-models-agentloop-tools` 0.1.0 pins
  `based-models-agentloop>=0.4,<0.5`, while this checkout is 0.5.0. Update the companion
  package's dependency range after verifying it against 0.5.0 so both can be installed
  together by pip.
- **Public documentation hosting.** Add the `docs/` content to the website, then replace
  the PyPI README's documentation placeholder with a link to the published pages.

---

## Medium priority

Items 13, 15, 16, 17 and 18 landed on `feature/medium-priority` (PR pending). Item 14 was checked live
(below). Left over:

### 13. Resuming a transcript that ends mid-turn: the paused case

Unanswered tool calls are now run before the first model call, and `check_invariants()` runs
once up front. Still open: a turn that paused (`StopReason.PAUSED`) and then hit a provider
error. The new `_Run` starts with `continuing=False`, so the continuation becomes a second
consecutive assistant message instead of extending the first. `Message` would have to record
that the turn is open, for example in `Origin`.

### 14. Responses reasoning replay without `include` (checked live, 2026-10-18)

Confirmed for `o4-mini`: a tool-using turn with `store: false` and no `include` returned a
`reasoning` item with a non-empty `encrypted_content`, and replaying the transcript with it was
accepted. The `_openai_responses.py` docstring is right for that model; no `include` is needed.
Not covered: `gpt-5.5` (the `.env` model) produced no reasoning item at its default effort
(0 reasoning tokens), so it did not exercise replay. Recheck if a request option for reasoning
effort is added. Item 21's fixture for a Responses reasoning item is still unrecorded.

### 17. Model-aware provider `Quirks`: what is left

`providers/openai.py` now picks `Quirks` by model-id prefix (`MODEL_QUIRKS`, `quirks_for_model`),
and `build_openai_client(quirks=...)` overrides it. The table holds only the o-series
(`o1`, `o3`, `o4`). Not done: other OpenAI families that reject parameters (add rows as they
are confirmed live), and OpenRouter and Modal, which stay provider-wide because their models
vary too much for one table.

### 18. Tracing setup warnings: what is left

A second `configure_tracing()` and a traced run with no provider now log a warning. It warns
rather than raises, and the check uses `ProxyTracerProvider` / `NoOpTracerProvider` from the
OpenTelemetry API. It is untested against older SDK versions than the one in the dev
environment.

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
