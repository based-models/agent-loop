"""The tool registry and the executor. No tools live here.

The executor is a TOTAL function: every tool call produces exactly one result,
including every way a call can fail. An exception escaping here becomes an
unpaired `tool_use` on the next request, which is the most confusing failure in
this whole system.

Nothing in this module knows what a file or a shell is. `ToolDeps` asks for the
approval hook and nothing else; the application supplies a bundle carrying
whatever its own tools need. See `docs/examples/` for one such bundle.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from agentloop.errors import ToolError
from agentloop.observer import LoopObserver, guarded
from agentloop.provider import ToolSchema
from agentloop.transcript import ContentPart, ToolCallPart, ToolResultPart

logger = logging.getLogger(__name__)

type Handler = Callable[..., str]
type ApprovalHook = Callable[[ToolCallPart], str | None]

MAX_PARALLEL_TOOLS = 8


def allow_all(call: ToolCallPart) -> str | None:
    """The default approval hook: permit everything.

    Public, not underscore-prefixed: every application that builds a deps bundle
    needs it as a default, and those live in other distributions. A private name
    cannot cross a repository boundary.
    """
    return None


class ToolDeps(Protocol):
    """What the executor needs from a deps bundle: the gate, and nothing else.

    A read-only property rather than a bare attribute, for two reasons. It is the
    honest declaration — `run_tools` reads this hook and never assigns to it — and
    a bare protocol variable would demand a settable attribute, which a
    Callable-typed field on a pydantic BaseModel is not. See chapter 6, Part 4.2.
    """

    @property
    def before_tool(self) -> ApprovalHook: ...


class ToolSpec(BaseModel):
    """One tool: what the model sees, plus the function behind it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # Preserve the caller's exact schema object: key order and identity are part
    # of the hand-written prompt contract.
    parameters: SkipValidation[dict[str, Any]]
    handler: Handler


class Registry:
    """A flat name -> ToolSpec map. No plugin machinery.

    Construction is where configuration mistakes surface: a duplicate name (which
    would silently swap the handler the model reaches) and a handler whose
    signature cannot be read (which the executor could not bind) both raise here,
    before any model has been called.
    """

    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        self._by_name: dict[str, ToolSpec] = {}
        self._signatures: dict[str, inspect.Signature] = {}
        for spec in specs:
            if spec.name in self._by_name:
                raise ValueError(f"duplicate tool name {spec.name!r}")
            try:
                signature = inspect.signature(spec.handler)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"tool {spec.name!r}: cannot read the handler's signature ({exc})"
                ) from exc
            self._by_name[spec.name] = spec
            self._signatures[spec.name] = signature

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def signature(self, name: str) -> inspect.Signature:
        """The handler's signature, read once at registration."""
        try:
            return self._signatures[name]
        except KeyError:
            raise KeyError(f"no tool registered named {name!r}") from None

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
            )
            for spec in self._by_name.values()
        )


# --- the executor ------------------------------------------------------------


def _execute(call: ToolCallPart, registry: Registry, deps: ToolDeps) -> ToolResultPart:
    """Run one tool call. NEVER raises: every failure becomes an error result."""

    def fail(message: str) -> ToolResultPart:
        return ToolResultPart(
            call_id=call.id, tool_name=call.tool_name, content=message, is_error=True
        )

    if call.parse_error is not None:
        return fail(
            f"Could not parse arguments as JSON ({call.parse_error}). "
            f"Received: {call.raw_arguments!r}. Re-issue with valid JSON."
        )

    spec = registry.get(call.tool_name)
    if spec is None:
        return fail(f"Unknown tool {call.tool_name!r}. Available: {registry.names()}")

    try:
        denial = deps.before_tool(call)
    except Exception as exc:  # noqa: BLE001 - the hook is someone else's code
        logger.exception("before_tool hook raised")
        return fail(f"Error: the approval hook failed: {type(exc).__name__}: {exc}")
    if denial is not None:
        return fail(f"Denied: {denial}")

    try:
        bound = registry.signature(spec.name).bind(deps, **call.arguments)
    except TypeError as exc:
        return fail(f"Invalid arguments for {call.tool_name}: {exc}")

    try:
        result = spec.handler(*bound.args, **bound.kwargs)
        content = str(result)  # inside the guard: __str__ is someone else's code too
    except ToolError as exc:
        return fail(f"Error: {exc}")
    except Exception as exc:  # noqa: BLE001 - totality is the invariant; see chapter 6
        logger.exception("tool %s raised", call.tool_name)
        return fail(f"Error: {type(exc).__name__}: {exc}")
    return ToolResultPart(call_id=call.id, tool_name=call.tool_name, content=content)


def _run_one(
    call: ToolCallPart, registry: Registry, deps: ToolDeps, observer: LoopObserver
) -> ToolResultPart:
    observer.on_tool_start(call)
    result = _execute(call, registry, deps)
    observer.on_tool_end(call, result)
    return result


def run_tools(
    content: Sequence[ContentPart],
    registry: Registry,
    deps: ToolDeps,
    observer: LoopObserver | None = None,
) -> list[ToolResultPart]:
    """Run every tool call in `content`.

    Returns exactly one result per call, in call order, whatever happens.
    """
    watcher = guarded(observer)
    calls = [part for part in content if isinstance(part, ToolCallPart)]
    if not calls:
        return []
    if len(calls) == 1:
        return [_run_one(calls[0], registry, deps, watcher)]

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TOOLS) as pool:
        futures = [
            pool.submit(_run_one, call, registry, deps, watcher) for call in calls
        ]
        # submit order, NOT completion order
        return [future.result() for future in futures]
