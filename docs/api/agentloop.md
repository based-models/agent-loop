# `agentloop`

The package root collects common imports. `Agent` runs conversations, `tool` defines local
Python tools, `Transcript` holds messages, and `ConversationSummary` reports how a run ended.
Import these names directly from `agentloop`:

```python
from agentloop import Agent, ConversationSummary, Transcript, tool
```

It re-exports:

- Agent layer: `Agent`, `Deps`, `tool`.
- Configuration: `Settings`, `build_client`.
- Errors: `AgentLoopError`, `ToolError`.
- Loop: `run_conversation`, `run_conversation_result`, `ListObserver`.
- Observers: `LoopObserver`, `NullObserver`, `ConsoleObserver`, `MultiObserver`.
- Reporting: `AttemptRecord`, `ConversationContext`, `ConversationResult`,
  `ConversationStatus`, `ConversationSummary`, `FailureCount`, `SummaryCollector`,
  `SummaryOptions`, `TurnRecord`, and `EventTraceObserver`.
- Hosted tools: `HostedTool`, `HostedToolPart`, `Source`, `Citation`.
- Tools: `Registry`, `ToolDeps`, `ToolSpec`, `allow_all`.
- Transcripts: `Transcript`, `Origin`, `final_text`, `join_text`.

`__version__` is the package's declared version string.

This list is an import map; the [API index](index.md) links to method and field details.
Custom providers use `LLMClient`, `ModelRequest`, and `ModelResponse` from
[`agentloop.provider`](provider.md). Provider-specific clients, the full error hierarchy,
retry configuration, OpenTelemetry setup, and metrics remain in their defining modules.
Importing `agentloop` does not load optional provider SDKs or the OpenTelemetry SDK.
