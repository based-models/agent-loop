from collections.abc import Mapping
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, ConfigDict

from agentloop.agent import Agent
from agentloop.errors import ProviderUnavailable, RateLimited, ToolError
from agentloop.loop import run_conversation
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.providers._openai_compat import OpenAICompatClient
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.tracing import OTelObserver
from agentloop.transcript import ContentPart, TextPart, ToolCallPart, Transcript


class ToyDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def echo(deps: ToyDeps, text: str) -> str:
    return f"echoed: {text}"


def boom(deps: ToyDeps) -> str:
    raise ToolError("no")


def _spec(name: str, handler: object, **props: str) -> ToolSpec:
    return ToolSpec(
        name=name, description=f"the {name} tool",
        parameters={
            "type": "object",
            "properties": {k: {"type": v} for k, v in props.items()},
            "required": list(props), "additionalProperties": False,
        },
        handler=handler,  # type: ignore[arg-type]
    )


REGISTRY = Registry([_spec("echo", echo, text="string"), _spec("boom", boom)])


class ScriptedClient:
    name, model = "fake", "m"

    def __init__(self, *responses: ModelResponse) -> None:
        self._queue = list(responses)

    def complete(self, request: ModelRequest) -> ModelResponse:
        return self._queue.pop(0)


def resp(*parts: ContentPart, stop: StopReason = StopReason.END_TURN,
         usage: Usage | None = None) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts), stop_reason=stop, usage=usage or Usage(),
        model="m", provider="fake",
    )


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    # The APPLICATION (here, the test) configures the provider. Never the library.
    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(sink))
    trace.set_tracer_provider(provider)
    return sink


def drive(exporter: InMemorySpanExporter, *responses: ModelResponse,
          capture: bool = False) -> None:
    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    run_conversation(
        transcript, ScriptedClient(*responses), REGISTRY, ToyDeps(),
        # agent_name is the APPLICATION's, and this is the seam: a test, the CLI and
        # an eval each pass their own, and the library ships only the default.
        observer=OTelObserver(provider="fake", model="m", agent_name="harness",
                              capture_content=capture),
    )


def attrs(span: ReadableSpan) -> Mapping[str, Any]:
    """Narrow `span.attributes`, which the SDK types as `Attributes | None`.

    It is never None for a span this suite created, but mypy cannot know that, so
    narrow once here rather than writing `assert ... is not None` nine times.
    """
    assert span.attributes is not None
    return span.attributes


def echo_call(text: str = "hi") -> ToolCallPart:
    return ToolCallPart(id="c1", tool_name="echo", arguments={"text": text})


def test_the_span_tree_has_the_expected_shape(exporter: InMemorySpanExporter) -> None:
    drive(
        exporter,
        resp(echo_call(), stop=StopReason.TOOL_USE,
             usage=Usage(input_tokens=100, output_tokens=20)),
        resp(TextPart(text="done"), usage=Usage(input_tokens=150, output_tokens=10)),
    )
    names = [span.name for span in exporter.get_finished_spans()]
    assert "chat m" in names
    assert "execute_tool echo" in names
    assert "invoke_agent harness" in names          # the injected name, not the default
    assert names.count("chat m") == 2


def test_the_agent_name_defaults_to_the_library(exporter: InMemorySpanExporter) -> None:
    """An application that does not name itself still gets a usable root span."""
    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    run_conversation(
        transcript, ScriptedClient(resp(TextPart(text="hi"))), REGISTRY, ToyDeps(),
        observer=OTelObserver(provider="fake", model="m"),
    )
    assert "invoke_agent agentloop" in [s.name for s in exporter.get_finished_spans()]


def test_model_span_attributes_follow_the_conventions(
    exporter: InMemorySpanExporter,
) -> None:
    drive(exporter, resp(TextPart(text="hi"),
                         usage=Usage(input_tokens=7, output_tokens=3)))
    chat = next(s for s in exporter.get_finished_spans() if s.name == "chat m")
    assert attrs(chat)["gen_ai.operation.name"] == "chat"
    assert attrs(chat)["gen_ai.provider.name"] == "fake"     # not gen_ai.system
    assert attrs(chat)["gen_ai.request.model"] == "m"
    assert attrs(chat)["gen_ai.usage.input_tokens"] == 7
    assert attrs(chat)["gen_ai.response.finish_reasons"] == ("end_turn",)


def test_tool_span_carries_the_call_id(exporter: InMemorySpanExporter) -> None:
    drive(exporter, resp(echo_call(), stop=StopReason.TOOL_USE),
          resp(TextPart(text="done")))
    tool = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool echo"
    )
    assert attrs(tool)["gen_ai.tool.name"] == "echo"
    assert attrs(tool)["gen_ai.tool.call.id"] == "c1"
    assert attrs(tool)["agentloop.tool.is_error"] is False


def test_result_bytes_is_the_encoded_byte_length_not_the_char_count(
    exporter: InMemorySpanExporter,
) -> None:
    # Multi-byte UTF-8 text (accents, emoji) must not under-report its size.
    text = "héllo wörld 😀"
    drive(exporter, resp(echo_call(text), stop=StopReason.TOOL_USE),
          resp(TextPart(text="done")))
    tool = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool echo"
    )
    expected = len(f"echoed: {text}".encode())
    assert attrs(tool)["agentloop.tool.result_bytes"] == expected
    assert expected > len(f"echoed: {text}")   # the bug this guards against


def test_a_failed_tool_sets_span_status_error(exporter: InMemorySpanExporter) -> None:
    drive(exporter,
          resp(ToolCallPart(id="c1", tool_name="boom", arguments={}),
               stop=StopReason.TOOL_USE),
          resp(TextPart(text="oh well")))
    tool = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool boom"
    )
    assert attrs(tool)["agentloop.tool.is_error"] is True
    assert tool.status.status_code.name == "ERROR"


def test_unreported_usage_is_omitted_not_zero(exporter: InMemorySpanExporter) -> None:
    drive(exporter, resp(TextPart(text="hi"),
                         usage=Usage(input_tokens=5, output_tokens=1)))
    chat = next(s for s in exporter.get_finished_spans() if s.name == "chat m")
    assert "gen_ai.usage.cache_read.input_tokens" not in attrs(chat)


def test_content_is_not_captured_by_default(exporter: InMemorySpanExporter) -> None:
    """The default must never put the user's data into telemetry.

    The argument here stands in for what a real coding agent sends: a file path,
    a diff, a shell command, all of which routinely contain secrets.
    """
    drive(exporter, resp(echo_call("API_KEY = 'sk-real-key'"), stop=StopReason.TOOL_USE),
          resp(TextPart(text="done")))
    tool = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool echo"
    )
    assert "gen_ai.tool.call.arguments" not in attrs(tool)
    assert "gen_ai.tool.call.result" not in attrs(tool)
    assert "sk-real-key" not in str(attrs(tool))


def test_content_capture_can_be_opted_into(exporter: InMemorySpanExporter) -> None:
    drive(exporter, resp(echo_call(), stop=StopReason.TOOL_USE),
          resp(TextPart(text="done")), capture=True)
    tool = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool echo"
    )
    assert "gen_ai.tool.call.arguments" in attrs(tool)


def test_the_loop_does_not_import_opentelemetry() -> None:
    import ast
    import inspect

    import agentloop.loop

    tree = ast.parse(inspect.getsource(agentloop.loop))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "opentelemetry" not in roots


# --- parentage: children hang off the root, even from worker threads ---


def span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return int(span.context.span_id)


def test_every_child_span_is_parented_to_the_root(exporter: InMemorySpanExporter) -> None:
    """Two calls run on the thread pool, where the ambient OTel context is empty."""
    drive(
        exporter,
        resp(echo_call("a"), ToolCallPart(id="c2", tool_name="echo", arguments={"text": "b"}),
             stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "invoke_agent harness")
    children = [s for s in spans if s is not root]
    assert [s.name for s in children].count("execute_tool echo") == 2
    for child in children:
        assert child.parent is not None, child.name
        assert child.parent.span_id == span_id(root), child.name


# --- a provider that raises: the model span still ends, as an error ---


class DownClient:
    name, model = "fake", "m"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise ProviderUnavailable("upstream said: API_KEY=sk-real-key", status_code=503)


def test_a_provider_error_ends_the_model_span_as_an_error(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    with pytest.raises(ProviderUnavailable):
        run_conversation(transcript, DownClient(), REGISTRY, ToyDeps(),
                         observer=OTelObserver(provider="fake", model="m"))
    spans = exporter.get_finished_spans()
    chat = next(s for s in spans if s.name == "chat m")
    root = next(s for s in spans if s.name.startswith("invoke_agent"))
    for span in (chat, root):
        assert span.status.status_code is StatusCode.ERROR
        assert attrs(span)["error.type"] == "ProviderUnavailable"
        # capture is off: the exception's MESSAGE is content, and stays out
        assert span.status.description is None
        assert not span.events
        assert "sk-real-key" not in str(attrs(span))


# --- error text is content ---


def boom_turns() -> tuple[ModelResponse, ModelResponse]:
    return (resp(ToolCallPart(id="c1", tool_name="boom", arguments={}),
                 stop=StopReason.TOOL_USE),
            resp(TextPart(text="oh well")))


def test_a_tool_error_message_is_not_captured_by_default(
    exporter: InMemorySpanExporter,
) -> None:
    drive(exporter, *boom_turns())
    tool = next(s for s in exporter.get_finished_spans() if s.name == "execute_tool boom")
    assert tool.status.status_code is StatusCode.ERROR
    assert tool.status.description is None


def test_a_tool_error_message_is_captured_when_opted_in(
    exporter: InMemorySpanExporter,
) -> None:
    drive(exporter, *boom_turns(), capture=True)
    tool = next(s for s in exporter.get_finished_spans() if s.name == "execute_tool boom")
    assert tool.status.description == "Error: no"


def test_a_retry_event_carries_no_message_by_default(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    observer = OTelObserver(provider="fake", model="m")
    with observer.conversation():
        observer.on_request(ModelRequest(messages=()))
        observer.on_retry(1, RateLimited("quota for sk-real-key", status_code=429))
        observer.on_response(resp(TextPart(text="hi")))
    chat = next(s for s in exporter.get_finished_spans() if s.name == "chat m")
    [event] = chat.events
    assert event.attributes is not None
    assert event.attributes["agentloop.retry.error_type"] == "RateLimited"
    assert event.attributes["http.response.status_code"] == 429
    assert "agentloop.retry.message" not in event.attributes


# --- one conversation at a time ---


def test_a_second_concurrent_conversation_is_refused() -> None:
    observer = OTelObserver(provider="fake", model="m")
    with observer.conversation():
        second = observer.conversation()
        with pytest.raises(RuntimeError, match="already tracing"):
            second.__enter__()
    with observer.conversation():      # released: sequential reuse is fine
        pass


# --- the convenience path wires retries into the trace ---


def test_from_env_puts_retries_on_the_model_span(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[ModelRequest] = []

    def flaky(self: OpenAICompatClient, request: ModelRequest) -> ModelResponse:
        attempts.append(request)
        if len(attempts) == 1:
            raise RateLimited("slow down", status_code=429, retry_after=0)
        return resp(TextPart(text="hi"))

    monkeypatch.setattr(OpenAICompatClient, "complete", flaky)
    exporter.clear()
    env = {"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-or-test"}
    with Agent.from_env(env, [], trace=True) as agent:
        assert agent.run("go") == "hi"
    chat = next(s for s in exporter.get_finished_spans() if s.name == "chat openrouter/free")
    assert [event.name for event in chat.events] == ["retry"]


def test_the_root_span_carries_the_conversation_summary(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    run_conversation(
        transcript,
        ScriptedClient(
            resp(echo_call("the-secret"), stop=StopReason.TOOL_USE,
                 usage=Usage(input_tokens=100, output_tokens=20)),
            resp(TextPart(text="done"), usage=Usage(input_tokens=150, output_tokens=10)),
        ),
        REGISTRY, ToyDeps(),
        observer=OTelObserver(provider="fake", model="m", agent_name="harness"),
        agent_name="harness", correlation_id="round-42", parent_id="lookup-1",
    )
    (root,) = [s for s in exporter.get_finished_spans() if s.name == "invoke_agent harness"]
    got = attrs(root)
    assert got["agentloop.conversation.status"] == "completed"
    assert got["agentloop.conversation.iterations"] == 2
    assert got["agentloop.conversation.iteration_limit"] == 50
    assert got["agentloop.conversation.correlation_id"] == "round-42"
    assert got["agentloop.conversation.parent_id"] == "lookup-1"
    assert got["agentloop.usage.input_tokens"] == 250
    assert got["agentloop.tool.calls.echo"] == 1
    assert "agentloop.usage.cost_usd" not in got  # never reported, so not asserted as 0
    assert "the-secret" not in str(dict(got))  # content stays out with capture off


def test_a_failed_conversation_is_summarized_on_its_error_span(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    with pytest.raises(Exception):  # noqa: B017 - the point is the span, not the type
        run_conversation(
            transcript, ScriptedClient(), REGISTRY, ToyDeps(),
            observer=OTelObserver(provider="fake", model="m", agent_name="harness"),
            deadline_seconds=-1.0,
        )
    (root,) = [s for s in exporter.get_finished_spans() if s.name == "invoke_agent harness"]
    assert attrs(root)["agentloop.conversation.status"] == "deadline_exceeded"
    assert root.status.status_code is StatusCode.ERROR


def test_the_root_span_gets_its_ids_immediately_and_signals_as_events(
    exporter: InMemorySpanExporter,
) -> None:
    from agentloop.summary import SummaryOptions

    exporter.clear()
    transcript = Transcript()
    transcript.add_user_message("go")
    run_conversation(
        transcript,
        ScriptedClient(
            *[
                resp(
                    ToolCallPart(id=f"s{i}", tool_name="echo", arguments={"text": "hi"}),
                    stop=StopReason.TOOL_USE,
                )
                for i in range(3)
            ],
            resp(TextPart(text="done"),
                 usage=Usage(input_tokens=60, cache_read_tokens=30)),
        ),
        REGISTRY, ToyDeps(),
        observer=OTelObserver(provider="fake", model="m", agent_name="harness"),
        correlation_id="round-7", conversation_id="conv-7", max_iterations=6,
        summary_options=SummaryOptions(stall_threshold=3, warn_at=(0.5,)),
    )
    (root,) = [s for s in exporter.get_finished_spans() if s.name == "invoke_agent harness"]
    got = attrs(root)
    assert got["agentloop.conversation.id"] == "conv-7"
    assert got["agentloop.conversation.correlation_id"] == "round-7"
    assert got["agentloop.conversation.stalled"] is True
    assert got["agentloop.conversation.max_repeated_tool_calls"] == 3
    assert got["agentloop.usage.cache_hit_rate"] == pytest.approx(0.5)
    assert got["agentloop.conversation.model_seconds"] >= 0
    events = {event.name: dict(event.attributes or {}) for event in root.events}
    assert events["agentloop.stall"] == {"tool": "echo", "count": 3}
    assert events["agentloop.budget_warning"]["kind"] == "iterations"
