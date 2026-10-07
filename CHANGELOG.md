# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/);
while `0.x`, a minor bump may break the public API (`agentloop.__all__`).

## [Unreleased]

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
