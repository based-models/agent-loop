"""MetricsObserver: outcomes as low-cardinality counters and histograms."""

from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import BaseModel, ConfigDict

from agentloop.errors import AuthError, ToolError
from agentloop.loop import run_conversation
from agentloop.metrics import MetricsObserver
from agentloop.provider import ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.summary import SummaryOptions
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import ContentPart, TextPart, ToolCallPart, Transcript


class ToyDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def echo(deps: ToyDeps, text: str) -> str:
    return text


def boom(deps: ToyDeps) -> str:
    raise ToolError("no")


def _spec(name: str, handler: object, **props: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters={
            "type": "object",
            "properties": {k: {"type": v} for k, v in props.items()},
            "required": list(props),
            "additionalProperties": False,
        },
        handler=handler,  # type: ignore[arg-type]
    )


REGISTRY = Registry([_spec("echo", echo, text="string"), _spec("boom", boom)])


def resp(
    *parts: ContentPart, stop: StopReason = StopReason.END_TURN, usage: Usage | None = None
) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts),
        stop_reason=stop,
        usage=usage or Usage(input_tokens=10, output_tokens=2),
        model="fake-1",
        provider="fake",
    )


def tool_call(call_id: str, name: str = "echo") -> ToolCallPart:
    arguments = {"text": "same"} if name == "echo" else {}
    return ToolCallPart(id=call_id, tool_name=name, arguments=arguments)


class Harness:
    def __init__(self) -> None:
        self.reader = InMemoryMetricReader()
        self.provider = MeterProvider(metric_readers=[self.reader])
        self.observer = MetricsObserver(self.provider.get_meter("test"))

    def run(self, client: FakeClient, **kwargs: Any) -> None:
        transcript = Transcript()
        transcript.add_user_message("go")
        run_conversation(
            transcript, client, REGISTRY, ToyDeps(), observer=self.observer, **kwargs
        )

    def points(self, name: str) -> list[tuple[dict[str, Any], float]]:
        data = self.reader.get_metrics_data()
        found: list[tuple[dict[str, Any], float]] = []
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        value = getattr(point, "value", None)
                        if value is None:
                            value = point.sum  # type: ignore[union-attr]
                        found.append((dict(point.attributes or {}), value))
        return found

    def names(self) -> set[str]:
        data = self.reader.get_metrics_data()
        return {
            metric.name
            for resource in (data.resource_metrics if data else [])
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_a_finished_conversation_is_counted_by_status(harness: Harness) -> None:
    harness.run(FakeClient(resp(TextPart(text="x"))), agent_name="lookup")
    ((attrs, value),) = harness.points("agentloop.conversations")
    assert value == 1
    assert attrs == {
        "provider": "fake", "model": "fake-1", "agent_name": "lookup", "status": "completed",
    }


def test_a_failed_conversation_is_counted_under_its_status(harness: Harness) -> None:
    with pytest.raises(AuthError):
        harness.run(FakeClient(AuthError("no")))
    ((attrs, _),) = harness.points("agentloop.conversations")
    assert attrs["status"] == "provider_error"


def test_tool_calls_are_split_by_whether_they_errored(harness: Harness) -> None:
    harness.run(
        FakeClient(
            resp(tool_call("a"), tool_call("b"), tool_call("c", "boom"),
                 stop=StopReason.TOOL_USE),
            resp(TextPart(text="done")),
        )
    )
    calls = {(a["tool"], a["error"]): v for a, v in harness.points("agentloop.tool.calls")}
    assert calls == {("echo", False): 2, ("boom", True): 1}


def test_tokens_are_counted_by_type_and_unreported_ones_are_skipped(harness: Harness) -> None:
    harness.run(
        FakeClient(resp(TextPart(text="x"), usage=Usage(input_tokens=7, output_tokens=3)))
    )
    tokens = {a["type"]: v for a, v in harness.points("agentloop.tokens")}
    assert tokens == {"input": 7, "output": 3}


def test_cost_is_recorded_only_when_a_provider_reported_one(harness: Harness) -> None:
    harness.run(FakeClient(resp(TextPart(text="x"))))
    assert "agentloop.cost" not in harness.names()
    harness.run(FakeClient(resp(TextPart(text="x"), usage=Usage(cost_usd=0.25))))
    ((_, value),) = harness.points("agentloop.cost")
    assert value == pytest.approx(0.25)


def test_histograms_record_duration_and_iterations(harness: Harness) -> None:
    harness.run(
        FakeClient(resp(tool_call("a"), stop=StopReason.TOOL_USE), resp(TextPart(text="x")))
    )
    ((_, iterations),) = harness.points("agentloop.conversation.iterations")
    assert iterations == 2  # a histogram point's sum
    assert harness.points("agentloop.conversation.duration")
    assert harness.points("agentloop.conversation.tool_duration")[0][0]["tool"] == "echo"


def test_stalls_and_budget_warnings_are_counted(harness: Harness) -> None:
    harness.run(
        FakeClient(
            *[resp(tool_call(f"c{i}"), stop=StopReason.TOOL_USE) for i in range(3)],
            resp(TextPart(text="done")),
        ),
        max_iterations=6,
        summary_options=SummaryOptions(stall_threshold=3, warn_at=(0.5,)),
    )
    ((stall_attrs, stalls),) = harness.points("agentloop.stalls")
    assert (stall_attrs, stalls) == ({"tool": "echo"}, 1)
    ((warning_attrs, _),) = harness.points("agentloop.budget_warnings")
    assert warning_attrs == {"kind": "iterations"}


def test_no_metric_ever_carries_a_conversation_correlation_or_parent_id(
    harness: Harness,
) -> None:
    harness.run(
        FakeClient(resp(tool_call("a"), stop=StopReason.TOOL_USE), resp(TextPart(text="x"))),
        conversation_id="conv-unique-1",
        correlation_id="round-unique-2",
        parent_id="parent-unique-3",
    )
    seen: set[str] = set()
    for name in harness.names():
        for attrs, _ in harness.points(name):
            seen.update(attrs)
            seen.update(str(v) for v in attrs.values())
    assert not {"conversation_id", "correlation_id", "parent_id"} & seen
    assert not {v for v in seen if "unique" in v}
