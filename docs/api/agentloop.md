# `agentloop`

The package root is the recommended import surface for ordinary agents:

```python
from agentloop import Agent, ConsoleObserver, ToolError, tool
```

It re-exports:

- Agent layer: `Agent`, `Deps`, `tool`.
- Configuration: `Settings`, `build_client`.
- Errors: `AgentLoopError`, `ToolError`.
- Loop: `run_conversation`, `run_conversation_result`, `ListObserver`.
- Observers: `LoopObserver`, `NullObserver`, `ConsoleObserver`, `MultiObserver`.
- Reporting: `ConversationContext`, `ConversationResult`, `ConversationStatus`,
  `ConversationSummary`, `SummaryCollector`, `SummaryOptions`, `TurnRecord`, and
  `EventTraceObserver`.
- Hosted tools: `HostedTool`, `HostedToolPart`, `Source`, `Citation`.
- Tools: `Registry`, `ToolDeps`, `ToolSpec`, `allow_all`.
- Transcripts: `Transcript`, `Origin`, `final_text`, `join_text`.

`__version__` is the installed package version string.

Provider-specific clients, the full error hierarchy, retry configuration, OpenTelemetry
setup, and metrics remain in their defining modules. Importing `agentloop` does not load
optional provider SDKs or the OpenTelemetry SDK.
