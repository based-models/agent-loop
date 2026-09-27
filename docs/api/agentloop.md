# `agentloop`

The package root is the recommended import surface for ordinary agents:

```python
from agentloop import Agent, ConsoleObserver, ToolError, tool
```

It re-exports:

- Agent layer: `Agent`, `Deps`, `tool`.
- Configuration: `Settings`, `build_client`.
- Errors: `AgentLoopError`, `ToolError`.
- Loop: `run_conversation`, `ListObserver`.
- Observers: `LoopObserver`, `NullObserver`, `ConsoleObserver`, `MultiObserver`.
- Tools: `Registry`, `ToolDeps`, `ToolSpec`, `allow_all`.
- Transcripts: `Transcript`, `final_text`.

`__version__` is the installed package version string, currently `"0.1.0"`.

Lower-level types deliberately remain in their defining modules so importing `agentloop`
does not load optional tracing or provider dependencies.
