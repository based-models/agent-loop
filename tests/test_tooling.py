import ast
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from agentloop.errors import ToolError
from agentloop.observer import NullObserver
from agentloop.tooling import (
    ApprovalHook,
    Registry,
    ToolSpec,
    allow_all,
    run_tools,
)
from agentloop.transcript import TextPart, ToolCallPart, ToolResultPart


class ToyDeps(BaseModel):
    """Everything a deps bundle needs and nothing more. Note: no cwd."""

    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def schema(**properties: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in properties.items()},
        "required": list(properties),
        "additionalProperties": False,
    }


def spec(name: str, handler: object, **properties: str) -> ToolSpec:
    return ToolSpec(
        name=name, description=f"the {name} tool",
        parameters=schema(**properties), handler=handler,  # type: ignore[arg-type]
    )


def echo(deps: ToyDeps, text: str) -> str:
    return text


def boom(deps: ToyDeps) -> str:
    raise RuntimeError("kaboom")


def refuse(deps: ToyDeps) -> str:
    raise ToolError("the input was not in the expected shape")


def slow(deps: ToyDeps, ms: int) -> str:
    time.sleep(ms / 1000)
    return str(ms)


REGISTRY = Registry([
    spec("echo", echo, text="string"),
    spec("boom", boom),
    spec("refuse", refuse),
    spec("slow", slow, ms="integer"),
])


def call(name: str, **arguments: object) -> ToolCallPart:
    return ToolCallPart(id=f"c-{name}", tool_name=name, arguments=dict(arguments))


# --- the spine: totality (Part 6) ---


def test_every_call_gets_exactly_one_result() -> None:
    calls = [
        ToolCallPart(id="c1", tool_name="echo", arguments={"text": "hi"}),
        ToolCallPart(id="c2", tool_name="nope", arguments={}),                  # (2)
        ToolCallPart(id="c3", tool_name="echo", arguments={},
                     parse_error="Expecting value"),                            # (1)
        ToolCallPart(id="c4", tool_name="echo", arguments={"nonsense": 1}),     # (3)
        ToolCallPart(id="c5", tool_name="boom", arguments={}),                  # (4)
    ]
    results = run_tools(calls, REGISTRY, ToyDeps())
    assert [r.call_id for r in results] == ["c1", "c2", "c3", "c4", "c5"]
    assert [r.is_error for r in results] == [False, True, True, True, True]


def test_a_raising_handler_becomes_an_error_result() -> None:
    results = run_tools([call("boom")], REGISTRY, ToyDeps())
    assert len(results) == 1 and results[0].is_error
    assert "kaboom" in results[0].content        # and it did NOT propagate
    assert "RuntimeError" in results[0].content  # named, because it is a bug


def test_a_tool_error_reaches_the_model_verbatim() -> None:
    """The contract from Part 6.3: your message, unchanged, no type name."""
    results = run_tools([call("refuse")], REGISTRY, ToyDeps())
    assert results[0].content == "Error: the input was not in the expected shape"
    assert "ToolError" not in results[0].content


def test_unknown_tool_message_lists_the_real_ones() -> None:
    results = run_tools([call("ekco")], REGISTRY, ToyDeps())
    assert "echo" in results[0].content          # the model can self-correct


def test_a_parse_failure_quotes_the_raw_string() -> None:
    bad = ToolCallPart(id="c1", tool_name="echo", arguments={},
                       raw_arguments='{"text": ', parse_error="Expecting value")
    results = run_tools([bad], REGISTRY, ToyDeps())
    assert '{"text": ' in results[0].content     # so the model sees what it sent
    assert "valid JSON" in results[0].content    # and what to do about it


def test_non_tool_parts_are_ignored() -> None:
    assert run_tools([TextPart(text="hi")], REGISTRY, ToyDeps()) == []


# --- the approval hook (Part 6.6) ---


def test_before_tool_can_deny() -> None:
    deps = ToyDeps(before_tool=lambda c: "not today")
    results = run_tools([call("echo", text="hi")], REGISTRY, deps)
    assert results[0].is_error and "not today" in results[0].content


def test_a_denied_call_never_reaches_the_handler() -> None:
    deps = ToyDeps(before_tool=lambda c: "no")
    results = run_tools([call("boom")], REGISTRY, deps)   # boom would raise
    assert results[0].content == "Denied: no"


def test_a_hook_that_raises_does_not_escape() -> None:
    """The hook is the APPLICATION's code. Totality has to cover it too."""

    def broken(denied: ToolCallPart) -> str | None:
        raise RuntimeError("policy server down")

    results = run_tools([call("echo", text="hi")], REGISTRY, ToyDeps(before_tool=broken))
    assert len(results) == 1 and results[0].is_error
    assert "approval hook failed" in results[0].content


# --- parallel execution (Part 6.5) ---


def test_parallel_results_keep_call_order() -> None:
    calls = [
        ToolCallPart(id=f"c{i}", tool_name="slow", arguments={"ms": ms})
        for i, ms in enumerate([120, 10, 60])          # they finish out of order
    ]
    results = run_tools(calls, REGISTRY, ToyDeps())
    assert [r.call_id for r in results] == ["c0", "c1", "c2"]
    assert [r.content for r in results] == ["120", "10", "60"]


# --- the registry (Part 4) ---


def test_registry_exposes_schemas_for_the_request() -> None:
    assert {tool.name for tool in REGISTRY.schemas()} == {
        "echo", "boom", "refuse", "slow"
    }


def test_registry_names_are_sorted_so_the_error_message_is_stable() -> None:
    assert REGISTRY.names() == ["boom", "echo", "refuse", "slow"]


# --- the observer (Part 3) ---


def test_the_observer_sees_a_start_and_an_end_for_every_call() -> None:
    seen: list[tuple[str, str]] = []

    class Recorder(NullObserver):
        def on_tool_start(self, call: ToolCallPart) -> None:
            seen.append(("start", call.id))

        def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
            seen.append(("end", call.id))

    run_tools([call("echo", text="hi"), call("boom")], REGISTRY, ToyDeps(), Recorder())
    assert sorted(seen) == [("end", "c-boom"), ("end", "c-echo"),
                            ("start", "c-boom"), ("start", "c-echo")]


# --- the seam itself (Part 2) ---


def test_a_deps_bundle_needs_nothing_but_the_hook() -> None:
    """ToyDeps has no cwd, no session, no client. The executor does not care."""
    results = run_tools([call("echo", text="ok")], REGISTRY, ToyDeps())
    assert results[0].content == "ok" and results[0].is_error is False


def test_the_library_does_not_know_what_a_shell_is() -> None:
    """A preview of the layering test in test_layering.py, on this module alone.

    It is here as well because this is where the temptation lives: `bash` is forty
    lines and it would fit right below `run_tools`.
    Put the fence up in the same commit as the field.
    """
    source = (Path(__file__).parent.parent / "src" / "agentloop" / "tooling.py")
    roots = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not (roots & {"subprocess", "os", "shutil"}), (
        f"tooling.py imports {sorted(roots & {'subprocess', 'os', 'shutil'})}. "
        "Running commands and touching files is the application's job."
    )


# --- configuration mistakes surface at registration, not mid-run ---


def test_a_duplicate_tool_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate tool name 'echo'"):
        Registry([spec("echo", echo, text="string"), spec("echo", boom)])


def test_a_handler_without_a_signature_is_rejected_at_registration() -> None:
    # Some builtins have no introspectable signature. Binding would raise
    # ValueError inside the executor, which promises never to raise.
    with pytest.raises(ValueError, match="'biggest'.*signature"):
        Registry([spec("biggest", max)])


class Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no str for you")


def unprintable(deps: ToyDeps) -> Unprintable:
    return Unprintable()


def test_a_result_that_cannot_become_a_string_is_still_one_result() -> None:
    registry = Registry([spec("unprintable", unprintable)])
    [result] = run_tools([call("unprintable")], registry, ToyDeps())
    assert result.is_error
    assert "no str for you" in result.content


class BrokenTracer(NullObserver):
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        raise RuntimeError("tracer down")


def test_run_tools_called_directly_survives_a_raising_observer() -> None:
    """run_tools is public: it applies the failure policy itself, not only via the loop."""
    calls = [call("echo", text="a"), call("refuse")]
    results = run_tools(calls, REGISTRY, ToyDeps(), BrokenTracer())
    assert [r.call_id for r in results] == ["c-echo", "c-refuse"]


# --- a per-tool timeout ---


def test_a_tool_that_outlives_its_timeout_gets_an_error_result() -> None:
    registry = Registry([ToolSpec(
        name="slow", description="the slow tool", parameters=schema(ms="integer"),
        handler=slow, timeout_seconds=0.05,
    )])
    started = time.monotonic()
    [result] = run_tools(
        [ToolCallPart(id="c1", tool_name="slow", arguments={"ms": 2000})],
        registry, ToyDeps(),
    )
    assert time.monotonic() - started < 1.0
    assert result.is_error
    assert "timed out" in result.content
    assert result.call_id == "c1"


def test_a_timed_tool_that_finishes_in_time_returns_normally() -> None:
    registry = Registry([ToolSpec(
        name="slow", description="the slow tool", parameters=schema(ms="integer"),
        handler=slow, timeout_seconds=5,
    )])
    [result] = run_tools(
        [ToolCallPart(id="c1", tool_name="slow", arguments={"ms": 1})], registry, ToyDeps()
    )
    assert result.content == "1"
    assert not result.is_error


def test_a_timed_tool_keeps_its_error_contract() -> None:
    registry = Registry([
        ToolSpec(name="refuse", description="d", parameters=schema(),
                 handler=refuse, timeout_seconds=5),
        ToolSpec(name="boom", description="d", parameters=schema(),
                 handler=boom, timeout_seconds=5),
    ])
    refused, crashed = run_tools(
        [ToolCallPart(id="a", tool_name="refuse", arguments={}),
         ToolCallPart(id="b", tool_name="boom", arguments={})],
        registry, ToyDeps(),
    )
    assert refused.content == "Error: the input was not in the expected shape"
    assert crashed.content == "Error: RuntimeError: kaboom"


def test_one_hung_tool_does_not_hold_up_its_siblings() -> None:
    registry = Registry([
        ToolSpec(name="slow", description="d", parameters=schema(ms="integer"),
                 handler=slow, timeout_seconds=0.05),
        spec("echo", echo, text="string"),
    ])
    started = time.monotonic()
    hung, fine = run_tools(
        [ToolCallPart(id="a", tool_name="slow", arguments={"ms": 2000}),
         ToolCallPart(id="b", tool_name="echo", arguments={"text": "x"})],
        registry, ToyDeps(),
    )
    assert time.monotonic() - started < 1.0  # the pool's shutdown must not wait for it
    assert hung.is_error and fine.content == "x"


def test_a_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ToolSpec(name="t", description="d", parameters=schema(), handler=boom, timeout_seconds=0)


# --- parallel_safe ---


def test_an_unsafe_tool_makes_the_whole_turn_sequential() -> None:
    running = 0
    overlap = 0

    def watch(deps: ToyDeps) -> str:
        nonlocal running, overlap
        running += 1
        overlap = max(overlap, running)
        time.sleep(0.05)
        running -= 1
        return "ok"

    def run(safe: bool) -> int:
        nonlocal overlap
        overlap = 0
        registry = Registry([
            ToolSpec(name="a", description="d", parameters=schema(), handler=watch,
                     parallel_safe=safe),
            ToolSpec(name="b", description="d", parameters=schema(), handler=watch),
        ])
        results = run_tools(
            [ToolCallPart(id="1", tool_name="a", arguments={}),
             ToolCallPart(id="2", tool_name="b", arguments={})],
            registry, ToyDeps(),
        )
        assert [r.call_id for r in results] == ["1", "2"]
        return overlap

    assert run(safe=True) == 2
    assert run(safe=False) == 1
