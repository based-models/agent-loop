"""A provider-agnostic agent loop.

This top level is the ergonomic surface: build an `Agent`, define tools with
`@tool`, run it. Bringing your own provider or observer means going one level
down -- `agentloop.provider` (the `LLMClient` protocol and canonical request/
response types), `agentloop.errors` (the full `ProviderError` hierarchy),
`agentloop.retry` (`RetryingClient`/`RetryPolicy`, provider-agnostic), and
`agentloop.tracing` (OpenTelemetry, opt-in). Those aren't re-exported here on
purpose: doing so would make every `import agentloop` pay for imports (like
OpenTelemetry) that most consumers never need.
"""

from agentloop.agent import Agent, Deps, tool
from agentloop.config import Settings, build_client
from agentloop.errors import AgentLoopError, ToolError
from agentloop.loop import ListObserver, run_conversation
from agentloop.observer import (
    ConsoleObserver,
    LoopObserver,
    MultiObserver,
    NullObserver,
)
from agentloop.tooling import Registry, ToolDeps, ToolSpec, allow_all
from agentloop.transcript import Transcript, final_text

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentLoopError",
    "ConsoleObserver",
    "Deps",
    "ListObserver",
    "LoopObserver",
    "MultiObserver",
    "NullObserver",
    "Registry",
    "Settings",
    "ToolDeps",
    "ToolError",
    "ToolSpec",
    "Transcript",
    "__version__",
    "allow_all",
    "build_client",
    "final_text",
    "run_conversation",
    "tool",
]
