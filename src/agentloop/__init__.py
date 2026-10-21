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
from agentloop.collector import SummaryCollector
from agentloop.config import Settings, build_client
from agentloop.errors import AgentLoopError, ToolError
from agentloop.events import EventTraceObserver
from agentloop.loop import ListObserver, run_conversation, run_conversation_result
from agentloop.observer import (
    ConsoleObserver,
    LoopObserver,
    MultiObserver,
    NullObserver,
)
from agentloop.provider import AttemptRecord, HostedTool
from agentloop.summary import (
    ConversationContext,
    ConversationResult,
    ConversationStatus,
    ConversationSummary,
    FailureCount,
    SummaryOptions,
    TurnRecord,
)
from agentloop.tooling import Registry, ToolDeps, ToolSpec, allow_all
from agentloop.transcript import (
    Citation,
    HostedToolPart,
    Origin,
    Source,
    Transcript,
    final_text,
    join_text,
)

__version__ = "0.6.1"

__all__ = [
    "Agent",
    "AgentLoopError",
    "AttemptRecord",
    "Citation",
    "ConsoleObserver",
    "ConversationContext",
    "ConversationResult",
    "ConversationStatus",
    "ConversationSummary",
    "Deps",
    "EventTraceObserver",
    "FailureCount",
    "HostedTool",
    "HostedToolPart",
    "ListObserver",
    "LoopObserver",
    "MultiObserver",
    "NullObserver",
    "Origin",
    "Registry",
    "Settings",
    "Source",
    "SummaryCollector",
    "SummaryOptions",
    "ToolDeps",
    "ToolError",
    "ToolSpec",
    "TurnRecord",
    "Transcript",
    "__version__",
    "allow_all",
    "build_client",
    "final_text",
    "join_text",
    "run_conversation",
    "run_conversation_result",
    "tool",
]
