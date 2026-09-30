"""The ergonomic layer: everything chapter 9 made you write by hand.

Strictly additive. Nothing in tooling.py or loop.py changes to support any of
it -- which is the test of whether the primitives underneath were right.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentloop.errors import ConfigurationError
from agentloop.loop import (
    DEFAULT_DEADLINE_SECONDS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_TOKENS,
    run_conversation,
)
from agentloop.observer import LoopObserver
from agentloop.provider import HostedTool, LLMClient, supported_hosted_tools
from agentloop.tooling import (
    ApprovalHook,
    Handler,
    Registry,
    ToolDeps,
    ToolSpec,
    allow_all,
)
from agentloop.transcript import Transcript, final_text


class Deps(BaseModel):
    """The default deps bundle: the approval gate, and nothing else."""

    model_config = ConfigDict(frozen=True)

    before_tool: ApprovalHook = allow_all


def _make_handler(fn: Callable[..., Any]) -> Handler:
    """Wrap `fn` so it can be called as `handler(deps, **arguments)`.

    The wrapper advertises the ORIGINAL parameters with `deps` prepended, so
    chapter 6's argument dry run still validates the model's arguments against
    the real function. A generic (deps, *args, **kwargs) signature would accept
    anything, and every bad call from the model would be demoted from a clean
    "Invalid arguments" result to a logged exception. See chapter 10, Part 4.2.

    functools.wraps does NOT do this: it sets __wrapped__, which
    inspect.signature follows, giving back a signature with no deps at all.
    """
    signature = inspect.signature(fn)
    wants_deps = "deps" in signature.parameters
    passthrough = [p for p in signature.parameters.values() if p.name != "deps"]

    def handler(deps: ToolDeps, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs, deps=deps) if wants_deps else fn(*args, **kwargs)

    handler.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[
            inspect.Parameter("deps", inspect.Parameter.POSITIONAL_ONLY),
            *passthrough,
        ]
    )
    return handler


def tool(
    parameters: dict[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], ToolSpec]:
    """Turn a plain function into a ToolSpec.

    `parameters` is REQUIRED and hand-written. This decorator will not generate
    a JSON Schema for you -- see chapter 10, Part 4.5, and divergence D7.

    The decorated function does not take `deps`. If it wants one, it declares a
    keyword-only `deps` parameter; either way `deps` is stripped from the
    signature the model is shown.
    """

    def decorate(fn: Callable[..., Any]) -> ToolSpec:
        doc = inspect.getdoc(fn) or ""
        resolved_description = description or doc.split("\n\n")[0]
        if not resolved_description:
            raise ValueError(
                f"@tool on {fn.__name__!r} has no description: give it a "
                f"docstring or pass description=... explicitly"
            )
        return ToolSpec(
            name=name or fn.__name__,
            description=resolved_description,
            parameters=parameters,
            handler=_make_handler(fn),
        )

    return decorate


def _with_tracing(
    observer: LoopObserver | None, *, provider: str, model: str, name: str
) -> LoopObserver:
    """Build an OTelObserver from the client, beside whatever was already passed.

    The import is LAZY on purpose. agent.py is imported by __init__.py, so a
    module-scope `from agentloop.tracing import OTelObserver` here would mean
    every `import agentloop` loads OpenTelemetry -- including for the consumers
    who will never trace anything. Chapter 11, Part 3.1 has the argument;
    chapter 13 asserts it against the installed wheel.
    """
    from agentloop.observer import MultiObserver
    from agentloop.tracing import OTelObserver

    spans = OTelObserver(provider=provider, model=model, agent_name=name)
    return MultiObserver(observer, spans) if observer is not None else spans


class Agent:
    """A client, a registry and a deps bundle, wired together."""

    def __init__(
        self,
        tools: Sequence[ToolSpec | HostedTool],
        *,
        client: LLMClient,
        system: str | None = None,
        deps: ToolDeps | None = None,
        observer: LoopObserver | None = None,
        name: str = "agentloop",
        trace: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        deadline_seconds: float | None = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        """A client, a registry and a deps bundle, wired together.

        `client` is not owned by this `Agent` and is never closed by it -- see
        `close()` and `from_env()`, which do own the client they build.

        `trace=True` builds an `OTelObserver` (from `client.name`/`client.model`
        and `name`) and merges it with `observer`. Spans go nowhere until you
        separately call `agentloop.tracing.configure_tracing(endpoint)`; see its
        docstring. An `OTelObserver` also holds a non-reentrant lock: one
        `Agent(..., trace=True)` instance can run only one conversation at a
        time. Calling `run()`/`converse()` again while one is already in flight
        (e.g. from a second concurrent request sharing this instance) raises
        `RuntimeError` rather than corrupting the in-flight spans -- build one
        traced `Agent` per concurrent conversation instead.
        """
        specs = [t for t in tools if isinstance(t, ToolSpec)]
        self._hosted = tuple(t for t in tools if isinstance(t, HostedTool))
        _check_hosted(self._hosted, specs, client)
        self._registry = Registry(specs)
        self._client = client
        self._system = system
        self._deps: ToolDeps = deps or Deps()
        self.name = name
        self._observer = (
            _with_tracing(observer, provider=client.name, model=client.model, name=name)
            if trace
            else observer
        )
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._deadline_seconds = deadline_seconds
        # Only a client this Agent built is this Agent's to close.
        self._owns_client = False

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], tools: Sequence[ToolSpec | HostedTool], **kwargs: Any
    ) -> Agent:
        """Build the configured provider's client from an environment mapping.

        YOU supply the mapping -- `Agent.from_env(os.environ, ...)`. There is no
        default, deliberately: see chapter 10, Part 5.1.
        """
        from agentloop.config import Settings, build_client

        settings = Settings.from_env(env)
        # The observer is built BEFORE the client, so the retry wrapper inside
        # the client reports to the same observer the loop does.
        observer: LoopObserver | None = kwargs.pop("observer", None)
        if kwargs.pop("trace", False):
            observer = _with_tracing(
                observer,
                provider=settings.provider,
                model=settings.model,
                name=kwargs.get("name", "agentloop"),
            )
        client = build_client(settings, observer=observer)
        agent = cls(tools, client=client, observer=observer, **kwargs)
        agent._owns_client = True
        return agent

    def close(self) -> None:
        """Close the client, if `from_env` built it. A client you passed in is yours."""
        if self._owns_client:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> Agent:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def run(self, prompt: str) -> str:
        """One question, one answer. Discards the transcript."""
        transcript = Transcript()
        transcript.add_user_message(prompt)
        return final_text(self.converse(transcript))

    def converse(self, transcript: Transcript) -> Transcript:
        """Advance an existing transcript. Mutates and returns it."""
        return run_conversation(
            transcript,
            self._client,
            self._registry,
            self._deps,
            system=self._system,
            observer=self._observer,
            max_tokens=self._max_tokens,
            max_iterations=self._max_iterations,
            deadline_seconds=self._deadline_seconds,
            hosted_tools=self._hosted,
        )


def _check_hosted(
    hosted: Sequence[HostedTool], specs: Sequence[ToolSpec], client: LLMClient
) -> None:
    """Fail at construction, not on the first request, for a hosted tool that
    cannot run here.

    Native and client-side tools are separate, explicit choices: a hosted tool
    the client does not support is an error naming the provider, never quietly
    swapped for something else.
    """
    supported = supported_hosted_tools(client)
    for tool in hosted:
        if tool.kind not in supported:
            api = getattr(client, "api", None)
            where = f"{client.name} ({api})" if api else client.name
            raise ConfigurationError(
                f"{where} cannot run the hosted tool {tool.kind!r}; "
                f"it supports {sorted(supported) or 'no hosted tools'}"
            )
    kinds = {tool.kind for tool in hosted}
    clashes = sorted(spec.name for spec in specs if spec.name in kinds)
    if clashes:
        # Anthropic's hosted web search is itself named "web_search" on the wire.
        raise ConfigurationError(
            f"tool name(s) {clashes} collide with a hosted tool; rename the ToolSpec"
        )
