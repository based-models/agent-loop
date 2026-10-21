# Documentation review and update plan

This checklist tracks a page-by-page review against `src/agentloop`, the provider adapters,
the public exports in `src/agentloop/__init__.py`, and the tests. Update **one Markdown page
at a time** and pause for review before starting the next page. Check a page off only after
its claims have been compared with code and every executable example on that page has been
run. Use OpenAI for agent-running examples. When credentials are unavailable, exercise the
OpenAI adapter with a local HTTP transport and record that limitation here. Use the simple
caption `Output:` for documented results. Provider setup commands can be checked locally
without making a paid request.

## Review rules

- [x] Inventory the documentation, package surface, provider implementations, and test suite.
- [x] Identify the available Python 3.12+ environment (`conda` environment `agentloop`).
- [ ] Use small, generic, self-contained OpenAI examples for agent runs. Reserve `FakeClient`
  for pages that specifically explain offline testing.
- [ ] Replace placeholder output, invented JSON, and comments like “For example” with
  verified output where stable. Do not imply that local HTTP responses are live model output.
  Give each example the imports, objects, and setup it needs.
- [ ] Check package-level imports against `__all__`; use submodule imports for other names.
- [ ] After each page, run its Python examples, check links and commands as applicable, and
  record the verification before pausing for review.
- [ ] After all pages, run the repository's documentation-relevant checks and a final README
  review of the rendered PyPI description.

## Getting started and navigation

1. [ ] `docs/getting-started/hello-world.md`: revise the completed offline-first page to
   lead with OpenAI and remove the `FakeClient` example; keep provider recipes accurate.
2. [x] `docs/getting-started/installation.md`: check Python version, extras, checkout install,
   and lazy-import claims against `pyproject.toml` and package imports; verify commands.
3. [x] `docs/getting-started/configuration.md`: check all defaults and accepted environment
   variables against `Settings.from_env`, `build_client`, and provider builders; run both
   configuration examples without network calls.
4. [ ] `docs/index.md`: revise its offline-first navigation after the Hello World page is
   changed to OpenAI; links were already verified.

## Guides

5. [x] `docs/guides/agents.md`: use OpenAI for one-shot, multi-turn, and borrowed-client
   examples; check client ownership and traced-agent limits.
6. [x] `docs/guides/tools.md`: correct the `ToolCallPart` import (it is in
   `agentloop.transcript`, not the package root); run examples of tool execution,
   dependencies, and approval; verify result order in the tooling tests.
7. [x] `docs/guides/conversations.md`: supply the missing `agent` setup; run conversation,
   manual tool-result ordering, invariant, and failure examples.
8. [x] `docs/guides/providers.md`: use OpenAI for complete examples; verify provider
   constructors, transport ownership, API selection, redirects, and connection-error
   descriptions against implementation and tests.
9. [x] `docs/guides/hosted-tools.md`: separate this package's `HostedTool` behavior from
   optional `agentloop-tools` helpers; check options and provider support against adapters;
   remove stale or unverifiable experiment claims and make the example complete.
10. [x] `docs/guides/custom-providers.md`: run the `LLMClient` example and show actual output;
    verify pause, hosted-tool capability, replay, and empty-turn rules.
11. [x] `docs/guides/summaries.md`: replace the fabricated summary JSON with output from an
    OpenAI adapter run; supply missing imports and objects; check status, usage, timing,
    warnings, timeline, and failure semantics against summary/collector/loop code.
12. [x] `docs/guides/event-trace.md`: replace undefined `tools`/`client` with a runnable
    client; capture real JSONL output; check redaction and event fields.
13. [x] `docs/guides/observers.md`: run the observer examples with explicit client and usage;
    verify callback signatures, guarding, and console output.
14. [x] `docs/guides/tracing.md`: verify setup and lifecycle against `tracing.py`/`metrics.py`;
    run examples with local in-memory exporters where possible, without requiring OTLP.
15. [x] `docs/guides/retries-and-errors.md`: provide a scripted failure/retry example and
    real output; check error taxonomy, `request_sent`, and `RetryPolicy` fields.
16. [x] `docs/guides/transcript-storage.md`: create the missing `transcript` locally, perform
    a JSONL round trip in a temporary path, and show actual output.
17. [x] `docs/guides/installing.md`: reconcile with the getting-started install page and
    release metadata; check version pinning guidance and verification commands.

## Maintainer documentation

18. [x] `RELEASING.md`: move release instructions out of user documentation; compare every
    documented command and release claim with `scripts/`, package metadata, and CI
    configuration; avoid running publish steps.

## Complete examples

19. [x] `docs/examples/minimal-agent.md`: show a direct OpenAI agent run and verify its code
    through the provider adapter.
20. [x] `docs/examples/tool-enabled-agent.md`: replace the simulated weather forecast with
    a generic local tool; use OpenAI and verify the tool-call path.
21. [x] `docs/examples/custom-observer.md`: fix undefined `tools`/`client`, account for
    optional token values correctly, and capture actual observer/JSONL output.
22. [x] `docs/examples/traced-agent.md`: remove dependence on an external collector for the
    runnable example and verify produced spans locally.
23. [x] `docs/examples/custom-provider.md`: run the complete fixed client and show its output;
    verify ownership guidance.
24. [x] `docs/examples/index.md`: check descriptions and links after examples are updated.

## API reference

25. [x] `docs/api/agentloop.md`: reconcile the export list with `__all__`, including
    `AttemptRecord` and `FailureCount`; run the import example.
26. [x] `docs/api/agent.md`: replace the `yfinance` example with a generic OpenAI tool
    example; update the documented `tool()` signature to include `timeout_seconds` and
    `parallel_safe`; verify agent methods, client ownership, and output.
27. [x] `docs/api/config.md`: verify fields, defaults, constructors, optional transport, and
    executable settings example.
28. [x] `docs/api/errors.md`: verify the hierarchy and structured error fields; make the
    catch example self-contained and run it.
29. [x] `docs/api/loop.md`: verify defaults, status and failure behavior, and observer example;
    run the low-level example.
30. [x] `docs/api/observer.md`: verify all optional hooks and callback signatures; run the
    subclass example.
31. [x] `docs/api/summary.md`: verify every field and status against models; run any example.
32. [x] `docs/api/events.md`: verify constructor, event names, data policy, and sample output.
33. [x] `docs/api/metrics.md`: verify instruments and attribute names against implementation.
34. [x] `docs/api/provider.md`: verify request/response types, usage and hosted-tool semantics;
    run the model example.
35. [x] `docs/api/retry.md`: verify policy defaults/fields and retry behavior; run example.
36. [x] `docs/api/tooling.md`: verify schemas, registration, execution, timeouts, and concurrency;
    run the `ToolSpec` example.
37. [x] `docs/api/tracing.md`: verify OTel setup, parameters and content capture; run example.
38. [x] `docs/api/transcript.md`: add missing imports to snippets; check current part fields,
    invariant rules, JSONL behavior, and actual output against `transcript.py`.
39. [x] `docs/api/providers/anthropic.md`: check constructor, options, wire translation,
    hosted search, and credentials against adapter code; run translation examples offline.
40. [x] `docs/api/providers/openai.md`: check Responses/Completions behavior and constructor;
    run offline translation examples.
41. [x] `docs/api/providers/openrouter.md`: check defaults, request translation and constructor;
    run the provider example and an offline translation check.
42. [x] `docs/api/providers/modal.md`: check endpoint, authentication and constructor;
    run offline examples.
43. [x] `docs/api/providers/index.md`: check provider table and links.
44. [x] `docs/api/index.md`: check module coverage and navigation once references are current.

## PyPI README

45. [x] `README.md`: check every claim and configuration default against the finished pages
    and implementation; use OpenAI for agent-running examples and validate them through the
    adapter; include verified output where stable. Keep the documentation placeholder until
    the website includes these docs.

## Verified pages

Record each completed page here with the exact command or method used and the observed
output. Do not mark a page complete based on syntax inspection alone.

- `docs/getting-started/hello-world.md`: ran the exact offline Python block with
  `conda run -n agentloop python /tmp/verify_agentloop_hello.py`; output was
  `Hello, world!` and `model requests: 1`. Compiled the live Python block and
  constructed/closed agents with `Agent.from_env` for Anthropic, OpenAI, OpenRouter,
  and Modal using local dummy settings, without making provider requests. All four
  constructors succeeded with the documented default models. A live model reply
  requires a real credential or deployed endpoint and is intentionally not shown
  as fixed output.
- `docs/getting-started/installation.md`: ran the documented `python -c` import check
  with `conda run -n agentloop`; output was `agentloop ready`. Checked Python 3.12.14,
  package version 0.5.0, `requires-python`, and all extras against `pyproject.toml`.
  A fresh import confirmed `agentloop` does not load `anthropic`, `httpx`, or
  `opentelemetry.sdk`. A local editable-install dry run could not complete because
  this existing conda environment lacks the `hatchling` build backend and network
  installation is unavailable in the sandbox; installation commands are therefore
  checked against package metadata, not claimed as executed.
- `docs/getting-started/configuration.md`: extracted and ran both Python blocks with
  `conda run -n agentloop python /tmp/verify_agentloop_configuration.py`. They printed
  `openai example-model`, `max attempts: 2`, and `openai example-model 2`, matching
  the page. Compared the environment table with every key read by `Settings.from_env`;
  all 14 variables are listed. Client construction and close made no provider request.
- `docs/index.md`: checked the provider list against `Settings` and the offline example
  against the shipped `FakeClient`; resolved all 19 relative Markdown links with a local
  link check. This navigation page contains no executable example.
- `docs/guides/agents.md`: after the OpenAI revision, extracted and ran all three Python
  blocks with `conda run -n agentloop python /tmp/verify_agentloop_agents_openai.py`.
  A local `httpx.MockTransport` supplied four Responses API replies; the OpenAI adapter
  sent four `/v1/responses` requests, and the second conversation request contained the
  earlier turn. The project `.env` contains an OpenAI key, though it is not exported to the
  process by default. Loaded it without printing its value and ran the same three blocks
  against live OpenAI with `conda run -n agentloop python /tmp/verify_agentloop_agents_live.py`;
  all four requests succeeded. The page now shows the actual live output with the `Output:`
  caption. The prior `tests/test_agent.py` run passed all 29 tests.
- `docs/guides/tools.md`: extracted and ran both complete Python blocks against live
  OpenAI with `conda run -n agentloop python /tmp/verify_agentloop_tools_live.py`, loading
  the project `.env` key without printing it. The arithmetic tool ran once and returned
  `27 plus 15 is 42.` The public note was read from application deps; the private call
  was denied before its handler ran and was counted in both `tool_calls` and
  `tool_errors`. The page contains the actual output. Ran
  `conda run -n agentloop python -m pytest -q tests/test_tooling.py` (25 passed),
  including the executor's ordering and failure behavior. After simplifying `add`, ran
  just that Python block again with
  `conda run -n agentloop python /tmp/verify_agentloop_tools_add_live.py`; it returned
  the same documented output.
- `docs/guides/conversations.md`: ran its OpenAI conversation block with
  `conda run -n agentloop python /tmp/verify_agentloop_conversations_live.py` using the
  project `.env` key without printing it; the reply and `messages: 2` output are on the
  page. Ran the manual result-order and invariant blocks with
  `conda run -n agentloop python /tmp/verify_agentloop_conversations_local.py`; their
  output matches the page. Ran `conda run -n agentloop python -m pytest -q
  tests/test_transcript.py` (37 passed).
- `docs/guides/providers.md`: extracted and ran all three Python blocks with
  `conda run -n agentloop python /tmp/verify_agentloop_providers_live.py`, using the
  project `.env` OpenAI key without printing it. They produced `Hello`,
  `openai gpt-5.1 responses`, and `ready` with `HTTP client closed by agent: False`,
  matching the page. Removed the unconfigured `truststore`/proxy snippet and checked
  the transport and Responses claims against the adapters. Ran the transport, OpenAI
  Responses, and OpenAI compatibility test files together (96 passed).
- `docs/guides/hosted-tools.md`: removed package-external policy details and stale
  experiment claims, then ran its OpenAI Responses web-search block with
  `conda run -n agentloop python /tmp/verify_agentloop_hosted_live.py` using the project
  `.env` key without printing it. The provider returned one hosted search call, 12
  sources, and no citations for this answer; the output is on the page. Compared the
  option table with both adapters and ran the OpenAI Responses and Anthropic test files
  (83 passed). Clarified the contract with the separate `agentloop-tools` package:
  `native_web_search()` returns this package's `HostedTool`. Importing its factory from
  the sibling source confirmed the type and options. Moved the companion package's
  version compatibility issue to `BACKLOG.md`. Corrected the execution attribution, documented
  mutually exclusive domain filters and hosted/local tool name collisions, and clarified
  Anthropic model support and the Chat Completions error paths. Checked those claims against
  source and ran the agent, OpenAI Responses, and Anthropic test files (112 passed).
- `docs/guides/custom-providers.md`: kept the deliberately minimal custom `EchoClient` to
  demonstrate the `LLMClient` protocol, combined its two code blocks into one complete
  example, and ran the block in the `agentloop` conda environment. It printed `Echo: hello`,
  as documented. Checked pause, hosted capability, replay, stop reasons, empty turns, and
  client ownership against the provider, loop, transcript, and agent code. Ran the agent,
  loop, and provider tests (69 passed).
- `docs/guides/summaries.md`: replaced fabricated JSON and incomplete snippets with live
  OpenAI examples showing a selected summary, an observer callback, and an optional timeline.
  Ran its Python blocks with `conda run -n agentloop python
  /tmp/verify_agentloop_summaries_live.py` using the project `.env` key without printing it;
  the actual output is on the page. Checked fields and failure behavior against the summary,
  collector, loop, and observer code; ran summary and loop tests (94 passed). Rebuilt the page
  as three independent OpenAI examples after feedback: first the result summary, then an
  observer, then `SummaryOptions`. Ran all three code blocks and updated their output.
- `docs/guides/event-trace.md`: replaced undefined `tools` and `client` with an OpenAI run
  that writes JSONL to a temporary file and prints selected fields from its four records.
  Added a separate content-capture example with an application redactor. Ran both Python
  blocks with `conda run -n agentloop python /tmp/verify_agentloop_event_trace_live.py`
  using the project `.env` key without printing it; their output is on the page. Checked
  event fields and redaction behavior against `events.py`; ran event trace tests (20 passed).
- `docs/guides/observers.md`: made the custom usage observer and combined built-in observer
  examples complete OpenAI runs. Ran both Python blocks with `conda run -n agentloop python
  /tmp/verify_agentloop_observers_live.py` using the project `.env` key without printing it;
  the response count, token usage, console text, and recorded events match the page. Checked
  callback signatures, optional hooks, and guarding against `observer.py`; ran agent, loop,
  and summary tests (123 passed). After feedback, kept the usage observer simple and printed
  the input prompt and returned answer beside the provider's input/output token counts;
  reran both blocks and confirmed the displayed values. Then changed the first prompt to
  the one-digit week question, printed `response.usage`, and confirmed its 19 input and 11
  output tokens against the raw OpenAI response body from the same request. Reran the updated
  guide blocks; the documented output matches.
- `docs/guides/tracing.md`: replaced collector-dependent examples with complete OpenAI runs
  using in-memory span and metric exporters. Ran both Python blocks with `conda run -n
  agentloop python /tmp/verify_agentloop_tracing_live.py` using the project `.env` key without
  printing it; the recorded span operations and metric names match the page. Checked provider
  setup, content capture, lifecycle, instruments, and metric attributes against source; ran
  tracing and metrics tests (41 passed).
- `docs/guides/retries-and-errors.md`: replaced incomplete snippets with one runnable example
  that injects a retryable connection failure, then lets the OpenAI client answer. Ran its
  Python block with `conda run -n agentloop python /tmp/verify_agentloop_retries_live.py`
  using the project `.env` key without printing it; the retry log, answer, attempt count,
  summary retry count, and send state match the page. Checked error types, send-state phases,
  and retry policy against source; ran retry, transport error, and summary tests (115 passed).
  Reworked the guide after feedback to introduce retryable errors and policy before the
  example, then explain the recorded attempts and three send states. Reran the shortened
  example; its output still matches.
- `docs/guides/transcript-storage.md`: replaced the undefined transcript with a complete
  two-message JSONL round trip in a temporary directory. Ran the example in the `agentloop`
  conda environment; its serialized lines and equality result match the page. Checked
  serialization, parsing, invariant checks, and origin replay against `transcript.py`.
- `docs/guides/installing.md`: updated the version range to `0.5`, aligned extras and
  Python requirements with `pyproject.toml` and the getting-started guide, and added a
  runnable import check. Ran the import command outside the source tree in the `agentloop`
  conda environment; its output matches the page. Parsed the example requirement to verify
  its syntax. Checked the 0.5.0 release files and provenance on PyPI and the repository's
  release workflow. A live GitHub release query was unavailable from this environment.
- `RELEASING.md`: moved the maintainer release procedure out of the user documentation and
  removed its navigation link. Matched the tag trigger, job sequence, publisher environment
  names, version checks, and release artifacts to `.github/workflows/release.yml`. Replaced
  stale pending-publisher and GitHub plan claims, aligned the local checks with
  `scripts/check.sh`, and corrected the `test-wheel.sh` description and yanking guidance.
  Ran `conda run -n agentloop bash scripts/check.sh`: ruff and mypy passed, 586 tests passed
  with 94.81% coverage, and the import check passed. Shell syntax checks passed. Attempted
  `scripts/test-wheel.sh` in a fresh temporary clone; build isolation could not install
  `hatchling` because PyPI network access was unavailable. No tag or publish step was run.
- `docs/examples/minimal-agent.md`: replaced the incomplete provider setup and larger
  one-off script with a short OpenAI agent example. Ran the exact Python block through the
  OpenAI provider in the `agentloop` conda environment; the output was `4`, as shown on the
  page. Checked `Agent.from_env()`, `run()`, and context-manager ownership against `agent.py`.
- `docs/examples/tool-enabled-agent.md`: replaced the fictional weather forecast with a
  small `count_letters` tool and removed the observer from the example. Ran the OpenAI agent
  in the `agentloop` conda environment; it returned `5` and recorded one `count_letters`
  call, matching the page. Checked decorator schema requirements, tool dispatch, and
  `run_result()` summary fields against `agent.py`, `tooling.py`, and the loop.
- `docs/examples/minimal-agent.md`, `docs/examples/tool-enabled-agent.md`, and
  `docs/guides/tracing.md`: added a provider-support sentence before their OpenAI install
  instructions. The examples and their recorded output were unchanged.
- `docs/examples/custom-observer.md`: replaced the undefined `tools` and `client` with
  complete OpenAI examples for a custom token counter and an in-memory JSONL trace. Ran
  both blocks in the `agentloop` conda environment; the 19 input tokens, 11 output tokens,
  and four trace events match the page. Checked callback signatures and usage field types
  against `observer.py` and `provider.py`.
- `docs/examples/traced-agent.md`: replaced the external OTLP collector with an in-memory
  tracer provider. Ran the OpenAI example in the `agentloop` conda environment and verified
  that the `chat` span is a child of the `invoke_agent` span; the output matches the page.
  Checked `trace=True` setup and provider ownership against `agent.py` and `tracing.py`.
- `docs/examples/custom-provider.md`: ran the exact `FixedClient` Python block in the
  `agentloop` conda environment; it printed the documented response. Checked the
  `LLMClient` protocol and borrowed-client ownership against `provider.py` and `agent.py`,
  and confirmed that closing a directly constructed agent leaves a closable client open.
- `docs/examples/index.md`: updated the summaries of all five example pages and clarified
  which examples call OpenAI. Checked that each relative Markdown link resolves.
- `docs/api/agentloop.md`: added the two missing root exports, `AttemptRecord` and
  `FailureCount`, and checked the list against all 37 names in `__all__`. Ran the import
  example in the `agentloop` conda environment and verified that a bare import leaves
  `anthropic`, `httpx`, and `opentelemetry.sdk` unloaded.
- `docs/api/agent.md`: replaced the stock-price example with a small `read_note` tool that
  shows model-supplied arguments and application-supplied `Deps`. Ran the exact Python block
  with OpenAI in the `agentloop` conda environment; it printed `Hello from the agent loop.`
  and `{'read_note': 1}`, matching the page. Updated the `tool()` signature and checked
  defaults, tool validation, client ownership, and agent methods against the source.
- `docs/api/config.md`: checked every setting and default against `config.py`, made the
  settings example self-contained, and showed client construction and cleanup. Ran the
  exact Python block in the `agentloop` conda environment; its output matches the page.
  Clarified attempt and timeout behavior, credential errors, Pydantic validation, and
  ownership of an optional HTTP transport against the provider implementations.
- `docs/api/errors.md`: verified the exception hierarchy, retryable classes, budget kinds,
  and provider error fields against `errors.py`, `retry.py`, and the loop. Replaced the
  undefined-agent example with a real missing-credential failure from `build_client()`.
  Ran the exact Python block in the `agentloop` conda environment; it raised `AuthError`
  and printed the metadata shown on the page.
- `docs/api/loop.md`: checked constants, signatures, transcript updates, truncation,
  deadlines, summaries, and `ListObserver` against `loop.py`. Replaced undefined-client
  and empty-observer snippets with one complete low-level OpenAI example. Ran the exact
  Python block in the `agentloop` conda environment; it printed `7` and the request and
  response event names shown on the page.
- `docs/api/observer.md`: checked callback signatures, optional hooks, guarded callback
  behavior, and tool event timing against `observer.py` and `tooling.py`. Replaced the
  unconnected `RetryCounter` snippet with a complete OpenAI `ResponseCounter` example.
  Ran its exact Python block in the `agentloop` conda environment; it printed `7` and one
  response, matching the page.
- `docs/api/summary.md`: expanded the status table, distinguished statuses from the
  `succeeded` and `budget_exhausted` properties, and documented every public field of
  `ConversationSummary`, `TurnRecord`, and `ConversationContext`. Checked field and status
  coverage against the live models. Ran the exact OpenAI example in the `agentloop` conda
  environment; it printed `7`, `completed`, `True`, and `1`, matching the page.
- `docs/api/events.md`: verified the constructor, record keys, event names, content policy,
  redaction order, and concurrency rule against `events.py`. Added a complete metadata-only
  OpenAI trace example and ran its exact Python block in the `agentloop` conda environment;
  the event list and `1 False` request check match the page.
- `docs/api/metrics.md`: documented all 12 instruments with their exact attribute sets and
  corrected when stall and budget-warning counters record. Verified each instrument and
  attribute set with a `MetricsObserver`, a complete sample `ConversationSummary`, and an
  in-memory OpenTelemetry meter in the `agentloop` conda environment. Confirmed the `otel`
  extra. Added a complete OpenAI example on the API page that passes a `Meter` from
  `metrics.get_meter("agentloop")` to `MetricsObserver`. Ran its exact Python block; it
  printed `7` and `completed 1`, matching the page.
- `docs/api/provider.md`: checked all fields of `ToolSchema`, `ModelRequest`, `Usage`,
  `ModelResponse`, `AttemptRecord`, and `HostedTool` against the live Pydantic models.
  Clarified empty truncated responses, retry metadata, and hosted-tool support. Replaced
  the placeholder response with a complete direct OpenAI `ModelRequest` example and ran
  its exact Python block in the `agentloop` conda environment; it printed `7` and
  `end_turn`, matching the page.
- `docs/api/retry.md`: checked policy fields and defaults, delay calculation, observer
  callback timing, delegated client properties, and attempt logging against `retry.py`.
  Replaced the undefined-client snippet with an OpenAI client wrapped by a one-time
  retryable failure. Ran the exact Python block in the `agentloop` conda environment; it
  printed the documented retry warning, answer, attempt count, and retry count.
- `docs/api/tooling.md`: checked `ToolSpec` fields, schema use, registry behavior,
  sequential and parallel execution, timeouts, and error results against `tooling.py`.
  Replaced the incomplete `ping` snippet with a self-contained `ToolSpec`, `Registry`,
  and `run_tools` example. Ran the exact Python block in the `agentloop` conda
  environment; it printed `hello` and `False`, matching the page.
- `docs/api/tracing.md`: checked span creation, observer callbacks, content capture,
  redaction and error-detail clipping, concurrency, and `configure_tracing()` against
  `tracing.py`. Replaced the constructor-only snippet with an in-memory OpenTelemetry
  example using a direct `OTelObserver` and OpenAI. Ran its exact Python block in the
  `agentloop` conda environment; it printed `ready` and the `chat` and `invoke_agent`
  span operations shown on the page. Added a separate `configure_tracing()` collector
  example, ran its exact Python block with OpenAI and a temporary local OTLP receiver,
  and verified that it printed `ready` and exported spans to `/v1/traces`.
- `docs/api/transcript.md`: checked content-part fields, `Origin`, message and transcript
  behavior, invariants, JSONL serialization, and text extraction against `transcript.py`.
  Replaced incomplete snippets with one self-contained round-trip example. Ran its exact
  Python block in the `agentloop` conda environment; it printed `Hi!`, two messages, and
  two JSONL lines, matching the page.
- `docs/api/providers/anthropic.md`: checked the constructor, credential handling, HTTP
  client ownership, hosted web search options, and translation helpers against the
  Anthropic adapter. Replaced the undefined-request snippet with a self-contained
  offline `ModelRequest` example. Ran its exact Python block in the `agentloop` conda
  environment; the model, prompt text, and hosted search tool type match the page.
- `docs/api/providers/openai.md`: checked the constructor, defaults, API paths, hosted
  tool support, and timeout behavior against the OpenAI and shared adapters. Replaced
  the undefined-request snippet with a complete example that calls both OpenAI APIs.
  Ran its exact Python block in the `agentloop` conda environment; both returned
  `ready`, matching the page. Offline translation checks confirmed `store: false`,
  token-limit fields, and omission of `parallel_tool_calls` without tools. The live
  check revealed that Chat Completions rejected that parameter without tools, so the
  adapter now omits it in that case. The targeted adapter suite passed (43 tests).
- `docs/api/providers/openrouter.md`: checked its constructor, headers, timeouts, Chat
  Completions format, hosted-tool support, and reasoning replay against the adapter.
  Replaced the placeholder construction snippet with a complete OpenRouter request and
  output. Ran its exact Python block in the `agentloop` conda environment; it printed
  `openrouter completions` and `ready`, matching the page. An offline translation check
  confirmed the default model, `max_tokens` field, and omission of `parallel_tool_calls`
  when no tools are present.
- `docs/api/providers/modal.md`: checked the constructor, endpoint path, proxy headers,
  timeout defaults, and vLLM quirks against the adapter. Replaced placeholder credentials
  with a runnable client configuration example. Ran its exact Python block in the
  `agentloop` conda environment; it printed the documented client API, default model,
  and empty hosted-tool set. Offline checks confirmed the request path, headers,
  `max_tokens` field, and absence of `parallel_tool_calls` without tools. A live request
  reached the configured Modal endpoint, but it returned `no upstreams available`, so
  no model output was available to document.
- `docs/api/providers/index.md`: added a provider comparison table and links to client
  selection, extension, and API details. Checked all seven relative links on disk.
  Constructed direct OpenAI, OpenRouter, and Modal clients offline in the `agentloop`
  conda environment, and checked Anthropic's class capability; API mode and hosted
  search support matched every table row.
- `docs/api/index.md`: checked the table against every top-level module in
  `src/agentloop` and added `collector` and `stall` navigation. Verified all 20 relative
  links on disk and confirmed that all 17 top-level modules, including the package root
  and providers package, are represented. This index has no executable example.
- `README.md`: reconciled provider support, configuration defaults, tool behavior, hosted
  search, summaries, tracing, and event capture with the implementation and completed
  documentation pages. Replaced incomplete examples with six self-contained Python blocks
  and captured their actual output in the `agentloop` conda environment. Five used live
  OpenAI calls; the missing-credential example ran offline. The tool run recorded one
  `add` call, and the hosted search run recorded a `HostedToolPart`. Kept the
  documentation placeholder at the user's request until the website includes these docs.
  Revised the reasons list to cover local tool approval, hosted search domain restrictions,
  and content redaction, with each bullet at two lines or fewer.
  Rendered the Markdown with `markdown_it` (four tables and 15 code blocks) and confirmed
  no em dashes remain.
