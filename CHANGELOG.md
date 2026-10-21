# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/);
while `0.x`, a minor bump may break the public API (`agentloop.__all__`).

## [Unreleased]

## [0.6.1]

### Changed
- README: the Documentation section links to the published docs and no longer hard-codes which
  agentloop versions agentloop-tools supports (the PyPI page freezes the README at release time).
  No code changes.

## [0.6.0]

### Added
- Transport failures say what failed and whether the request was sent. `ProviderError` gains
  `phase` (`FailurePhase`: connect, tls, proxy, send, read, status, redirect, body),
  `request_sent` (`True`, `False`, or `None` when the adapter cannot tell; `False` means
  nothing left the machine, so nothing was billed) and `redirect_host`. Adapters classify the
  HTTP exception chain once, `httpx` or (under the Anthropic SDK) `httpx2`, so callers no
  longer walk `__cause__`. An error read from a response the server sent (an error inside a
  200, a failed Responses body, a shape that cannot be normalized, Anthropic's 200 context
  overflow) has `phase=body` and `request_sent=True`.
- `ProviderUnreachable` (a `ProviderUnavailable` subclass) for TLS verification, DNS and proxy
  failures, and `UnexpectedRedirect` for a 3xx. Neither is retryable.
- `http_client=` on `OpenAICompatClient`, `build_openai_client`, `build_openrouter_client`,
  `build_modal_client`, `AnthropicClient`, `build_client` and `Agent.from_env`: pass an
  `httpx.Client` for a custom trust store, proxy or transport, or an `httpx2.Client` for
  Anthropic, whose SDK is built on httpx2. A client from the wrong package raises
  `ConfigurationError`. It stays yours to close: closing the agent or the provider client
  leaves it open. See `docs/guides/providers.md`.
- Per-attempt records: `AttemptRecord`, as `attempt_log` on `ModelResponse` (empty when the
  first attempt succeeded) and on `ProviderError`. Names, statuses and ids only, no message text.
- Summary: `failed_attempts` (`FailureCount` per error type, phase and send state),
  `error_phase`, `error_request_sent`, `error_request_id` and `error_attempts_sent` (attempts
  that may have reached the provider; `0` means nothing was billed). `TurnRecord.attempt_log`
  with the timeline.
- `LoopObserver.on_model_error(error)`: once per model call that failed for good, including the
  final attempt that `on_retry` never reports. Optional: observers that lack it are skipped.
  Observers that subclass `NullObserver` are unaffected; a hand-written class satisfying the
  Protocol structurally should add it to keep type-checking.
- Event trace: a `model_error` event, and `phase` / `request_sent` on `retry`. Metrics: the
  `agentloop.provider.errors` counter (`error_type`, `phase`, `request_sent` of `true`,
  `false` or `unknown`). Tracing: `agentloop.attempts`, `gen_ai.response.id`, and
  `agentloop.error.*` on the model and root spans.
- `ToolSpec.timeout_seconds` (and `@tool(timeout_seconds=...)`): a call that runs longer gets
  an error result and the loop moves on. The handler's thread is abandoned, not killed.
  Default `None` (no limit).
- `OTelObserver(redact=..., allow_unredacted=...)`, with the event trace's rules.
- `ConversationStatus.TOKEN_LIMIT` and `LoopBudgetExceeded(kind="max_tokens")`.

- `ToolSpec.parallel_safe` (and `@tool(parallel_safe=...)`): `False` makes a turn that calls
  the tool run all its calls sequentially, in call order.
- `Settings.timeout_seconds` / `LLM_TIMEOUT_SECONDS`, and `timeout_seconds=` on
  `build_openai_client`, `build_openrouter_client` and `build_modal_client`: replaces the read
  timeout of the OpenAI-compatible providers (Anthropic: the SDK's overall timeout). An explicit
  timeout now wins over a supplied `http_client`'s, so the "is it 5 seconds?" guess only applies
  when none was asked for.
- `build_openai_client(quirks=...)` and `providers.openai.MODEL_QUIRKS` / `quirks_for_model`: the
  o-series reasoning models no longer receive `parallel_tool_calls`, which they reject.

### Changed
- **Breaking:** the default provider is now `openrouter`, not `anthropic`, for `Settings.provider`
  and `Settings.from_env` when `LLM_PROVIDER` is unset. Set `LLM_PROVIDER=anthropic` (or
  `Settings(provider="anthropic")`) to keep the old behavior; otherwise the missing
  `OPENROUTER_API_KEY` raises `AuthError`.
- Resuming a transcript that ends in unanswered tool calls (the process died while tools ran)
  runs those calls before the first model call instead of sending an invalid request. Any other
  invalid transcript raises `TranscriptError` before a model call, not a provider 400.
- `configure_tracing()` logs a warning when a provider is already installed (it cannot replace
  it, so the one it returns receives no spans), and a traced conversation logs one warning when
  no provider is installed at all.
- `OTelObserver(capture_content=True)` now needs `redact` or `allow_unredacted=True`, else it
  raises `ConfigurationError`. Captured text is redacted, then clipped; a redactor that raises
  drops the attribute. Exceptions are recorded as an `exception` event with a redacted message
  and no stack trace.
- A turn truncated mid-tool-call no longer lowers a `max_tokens` above the 64,000 ceiling, and
  when the budget cannot grow the run stops with `LoopBudgetExceeded(kind="max_tokens")`
  (status `token_limit`) instead of re-billing the same cut-off up to `max_iterations`.
- `ModelResponse.content` may be empty when `stop_reason` is `MAX_TOKENS`. The loop treats such
  a turn (a reasoning model that spent the budget thinking) as a truncation and raises the
  budget, instead of the adapter raising a retryable `ResponseInvalid`. Other empty turns are
  still `ResponseInvalid`.
- Calls to tools that are not registered are reported to observers, summaries, metrics, spans
  and the event trace under `"<unregistered>"`, not the model's invented name. The model still
  receives the real name in its "Unknown tool" result.
- TLS verification, DNS and proxy failures are no longer retried: they fail the same way every
  time. They raise `ProviderUnreachable`, still a `ProviderUnavailable`, with `retryable = False`.
- A 3xx is reported as `UnexpectedRedirect` (with the target host only), not as a retryable
  `ResponseInvalid` claiming "a 200". Redirects are still never followed.
- A non-JSON body now reports its real status in `ResponseInvalid.status_code` and the message.
- `OpenAICompatClient` sends its URL and headers per request instead of configuring them on the
  `httpx.Client`; code that replaced the private `_http` attribute still works.
- `TurnRecord.input_tokens` and `output_tokens` are `int | None`, and `None` (not `0`) for a
  call that raised before answering: there was no usage to read, and `0` read as "measured,
  and free". Code that sums the timeline must skip `None`.

### Fixed
- `TurnRecord.attempts` reported `1` for a call that failed after retries.

## [0.5.0]

### Added
- Conversation summaries: every run now produces a `ConversationSummary` with status,
  (`ConversationStatus`), iterations against the limit, elapsed time, tool calls and errors
  by name, provider retries, summed usage and cost, and optional caller-supplied
  `correlation_id`/`parent_id`. It excludes prompts, responses, tool arguments, tool results,
  and exception messages. See `docs/guides/summaries.md`.
- `LoopObserver.on_conversation_end(summary)`: fired once per conversation on success AND
  failure, inside `conversation()`. Optional: observers that lack it are skipped.
- `run_conversation_result`, `Agent.run_result` and `Agent.converse_result` return a
  `ConversationResult` (transcript + summary). An `AgentLoopError` raised out of a run
  carries the summary as `.summary`.
- `SummaryCollector`, which gathers usage, retries, and tool activity for a summary.
- `LoopBudgetExceeded.kind` / `.limit` / `.actual`, so iteration and deadline exhaustion
  are told apart without parsing the message.
- `OTelObserver` writes the summary onto the root span (`agentloop.conversation.*`).
- Where the time went: `model_seconds`, `tool_seconds`, `retry_wait_seconds` and per-tool
  `tool_duration_seconds` on the summary; `cache_hit_rate` where the provider reports cache
  tokens (`Usage.input_includes_cached` says how to read `input_tokens`).
- Repeated-tool-call detection: `max_repeated_tool_calls` and `stalled` on the summary, and an
  `on_stall(tool_name, count)` event, when `SummaryOptions.stall_threshold` is set. Flag
  only: status and behavior are unchanged, and only counts are kept.
- Budget warnings: `on_budget_warning(kind, used, limit)` at the fractions in
  `SummaryOptions.warn_at`, once each. Off until configured.
- A per-turn `timeline` of `TurnRecord`s, opt-in through `SummaryOptions.timeline`.
- `SummaryOptions`, one frozen object for these optional settings, accepted by
  `run_conversation*` and `Agent(summary_options=...)`.
- `on_conversation_start(context)`, so observers can add identifiers to later records.
- `EventTraceObserver`: an ordered JSONL event trace written through a function you supply.
  It contains metadata only by default. Capturing content requires a `redact` function; the library ships no
  redaction patterns and never opens a file.
- `agentloop.metrics.MetricsObserver` and `Agent(metrics=True)`: OpenTelemetry counters and
  histograms with a limited set of reusable attributes (never conversation or correlation IDs).

### Changed
- `RetryingClient` stamps `ModelResponse.attempts` (new, default 1) and
  `ProviderError.attempts`, so retries are counted without wiring an observer into the
  client. A response that needed a retry is now a copy of the inner client's response.
- `ModelResponse.retry_wait_seconds` and `ProviderError.retry_wait_seconds` (default 0), set
  by `RetryingClient`.
- `LoopObserver` gains `on_conversation_start`, `on_conversation_end`, `on_budget_warning`
  and `on_stall`, all optional (an observer that lacks one is skipped). Observers that subclass `NullObserver` are
  unaffected; a hand-written class satisfying the Protocol structurally should add it to
  keep type-checking.

## [0.4.0]

### Changed
- Published on PyPI as `based-models-agentloop` (import name `agentloop`), under the
  Apache-2.0 license. Installing from git is no longer required.
- Release pipeline: build once, publish to PyPI with Trusted Publishing (no API tokens),
  then attach the same files to the GitHub release.
- Comments, docstrings and tests no longer point at external course material.

## [0.3.0]

### Changed
- Distribution renamed to `based-models-agentloop` (import name is still `agentloop`).
  The name `agentloop` on PyPI belongs to an unrelated project. Reinstall under the new name.

### Added
- `agentloop.providers.fake.FakeClient`: a scripted `LLMClient` for tests.
- Architecture tests (`tests/test_layering.py`), cross-adapter translation tables, and a
  golden transcript test.
- CI, a wheel-in-a-bare-venv check (`scripts/test-wheel.sh`), and a tag-driven release.

## [0.2.0]

### Added
- Hosted tools and native web search, the OpenAI Responses API (now the OpenAI default),
  `Origin` for exact replay, `StopReason.PAUSED`.
