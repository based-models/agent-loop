"""A provider-agnostic agent loop."""

from agentloop.agent import Agent, Deps, tool
from agentloop.config import Settings, build_client
from agentloop.errors import AgentLoopError, ToolError
from agentloop.loop import run_conversation
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
