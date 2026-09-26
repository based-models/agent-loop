import json

import pytest
from pydantic import ValidationError

from agentloop.provider import (
    LLMClient,
    ModelRequest,
    ModelResponse,
    StopReason,
    ToolSchema,
    Usage,
)
from agentloop.transcript import Message, TextPart


class EchoClient:
    """A plain class. It subclasses nothing and imports nothing from us."""

    name = "fake"
    model = "fake-1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=(TextPart(text="ok"),),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=1, output_tokens=1),
            model=self.model,
            provider=self.name,
        )


def test_a_plain_class_satisfies_the_protocol() -> None:
    client: LLMClient = EchoClient()
    request = ModelRequest(
        messages=(Message(role="user", content=(TextPart(text="hi"),)),)
    )
    assert client.complete(request).stop_reason is StopReason.END_TURN


# --- the request (Part 2) ---

def test_temperature_is_omitted_by_default() -> None:
    # experiment 1.1: sending it at all is a 400 on current Claude models
    assert ModelRequest(messages=()).temperature is None


def test_temperature_can_be_set_explicitly() -> None:
    assert ModelRequest(messages=(), temperature=0.0).temperature == 0.0


def test_max_tokens_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ModelRequest(messages=(), max_tokens=0)


def test_system_is_a_field_not_a_message() -> None:
    assert ModelRequest(messages=(), system="be brief").system == "be brief"


def test_there_is_no_system_role_to_use_by_mistake() -> None:
    # The reason two system messages are unreachable: the role does not exist.
    # mypy rejects this line too; the ignore lets us prove the runtime half.
    with pytest.raises(ValidationError):
        Message(role="system", content=(TextPart(text="be brief"),))  # type: ignore[arg-type]


def test_tool_schema_holds_the_bare_json_schema() -> None:
    schema = {"type": "object", "properties": {}, "required": [],
              "additionalProperties": False}
    tool = ToolSchema(name="read", description="Read a file.", parameters=schema)
    assert tool.parameters == schema        # no envelope; adapters add their own
    assert tool.strict is True


# --- the response (Part 3) ---

def test_unreported_usage_is_none_not_zero() -> None:
    usage = Usage(input_tokens=5)
    assert usage.input_tokens == 5
    assert usage.cache_read_tokens is None      # "not measured" != "measured as zero"


def test_negative_usage_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


def test_an_empty_response_cannot_be_built() -> None:
    # an adapter physically cannot hand back an empty assistant turn
    with pytest.raises(ValidationError):
        ModelResponse(
            content=(),
            stop_reason=StopReason.END_TURN,
            usage=Usage(),
            model="m",
            provider="p",
        )


def test_raw_stop_reason_survives_normalization() -> None:
    # Rule 1 will set stop_reason to TOOL_USE while the wire said "stop";
    # keeping the original is what makes that debuggable.
    response = ModelResponse(
        content=(TextPart(text="hi"),),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(),
        model="m",
        provider="p",
        raw_stop_reason="stop",
    )
    assert response.stop_reason is StopReason.TOOL_USE
    assert response.raw_stop_reason == "stop"


def test_stop_reasons_serialize_as_plain_strings() -> None:
    # StrEnum members ARE strings, so they log and serialize with no conversion
    assert json.dumps({"stop": StopReason.TOOL_USE}) == '{"stop": "tool_use"}'
