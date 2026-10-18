# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/);
while `0.x`, a minor bump may break the public API (`agentloop.__all__`).

## [Unreleased]

### Added
- Transport failures say what failed and whether the request was sent. `ProviderError` gains
  `phase` (`FailurePhase`: connect, tls, proxy, send, read, status, redirect, body),
  `request_sent` (`True`, `False`, or `None` when the adapter cannot tell; `False` means
  nothing left the machine, so nothing was billed) and `redirect_host`. Adapters classify the
  `httpx` exception chain once, so callers no longer walk `__cause__`.
- `ProviderUnreachable` (a `ProviderUnavailable` subclass) for TLS verification, DNS and proxy
  failures, and `UnexpectedRedirect` for a 3xx. Neither is retryable.
- `http_client=` on `OpenAICompatClient`, `build_openai_client`, `build_openrouter_client`,
  `build_modal_client`, `AnthropicClient`, `build_client` and `Agent.from_env`: pass an
  `httpx.Client` for a custom trust store, proxy or transport. It stays yours to close. See
  `docs/guides/providers.md`.
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

### Changed
- TLS verification, DNS and proxy failures are no longer retried: they fail the same way every
  time. They raise `ProviderUnreachable`, still a `ProviderUnavailable`, with `retryable = False`.
- A 3xx is reported as `UnexpectedRedirect` (with the target host only), not as a retryable
  `ResponseInvalid` claiming "a 200". Redirects are still never followed.
- A non-JSON body now reports its real status in `ResponseInvalid.status_code` and the message.
- `OpenAICompatClient` sends its URL and headers per request instead of configuring them on the
  `httpx.Client`; code that replaced the private `_http` attribute still works.

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
