import logging

import pytest

from agentloop import Agent, Deps, Registry, ToolSpec, tool
from agentloop.errors import ConfigurationError, LoopBudgetExceeded
from agentloop.provider import HostedTool, ModelRequest, ModelResponse, StopReason, Usage
from agentloop.tooling import run_tools
from agentloop.transcript import (
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
    final_text,
)

SCHEMA = {
    "type": "object",
    "properties": {"timezone": {"type": ["string", "null"], "description": "tz"}},
    "required": ["timezone"],
    "additionalProperties": False,
}


@tool(parameters=SCHEMA)
def echo_tz(timezone: str | None) -> str:
    """Echo the timezone you were given.

    A second paragraph, which must NOT reach the model.
    """
    return f"tz={timezone!r}"


@tool(parameters=SCHEMA)
def with_default(timezone: str | None = None) -> str:
    """The shape that silently swallowed the deps bundle before this chapter."""
    return f"tz={timezone!r}"


def call(name: str, **arguments: object) -> ToolCallPart:
    return ToolCallPart(id="c1", tool_name=name, arguments=dict(arguments))


def run(spec: ToolSpec, **arguments: object) -> ToolResultPart:
    return run_tools([call(spec.name, **arguments)], Registry([spec]), Deps())[0]


# --- the decorator (Part 4) ---


def test_a_plain_function_becomes_a_tool() -> None:
    result = run(echo_tz, timezone="Asia/Tokyo")
    assert result.is_error is False
    assert result.content == "tz='Asia/Tokyo'"


def test_name_and_description_come_from_the_function() -> None:
    assert echo_tz.name == "echo_tz"
    assert echo_tz.description == "Echo the timezone you were given."   # first para only


def test_the_schema_is_the_one_you_wrote() -> None:
    """D7: the decorator does not generate it, and does not touch it."""
    assert echo_tz.parameters is SCHEMA


def test_a_tool_with_no_docstring_and_no_description_fails_clearly() -> None:
    with pytest.raises(ValueError, match="no_doc"):

        @tool(SCHEMA)
        def no_doc(timezone: str) -> str:
            return timezone


def test_an_explicit_description_does_not_need_a_docstring() -> None:
    @tool(SCHEMA, description="Echo it back.")
    def no_doc(timezone: str) -> str:
        return timezone

    assert no_doc.description == "Echo it back."


# --- the trap (Part 4.2) ---


def test_bad_arguments_are_a_clean_result_not_a_logged_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The test the naive wrapper fails.

    A (deps, *args, **kwargs) signature accepts anything, so bind() cannot fail,
    the TypeError happens inside the call, and chapter 6's case (3) is demoted to
    a logged traceback. Both assertions matter; the second is the one that bites.
    """
    with caplog.at_level(logging.ERROR):
        result = run(echo_tz, nonsense=1)

    assert result.is_error
    assert "Invalid arguments for echo_tz" in result.content
    assert "TypeError" not in result.content
    assert caplog.records == []          # no traceback logged for a model mistake


def test_the_advertised_signature_keeps_the_real_parameters() -> None:
    import inspect

    assert str(inspect.signature(echo_tz.handler)) == (
        "(deps, /, timezone: str | None) -> str"
    )


# --- the silent failure (Part 2) ---


def test_a_defaulted_parameter_no_longer_swallows_deps() -> None:
    """Before this chapter, bind(deps) put the deps bundle in `timezone`."""
    assert run(with_default).content == "tz=None"


# --- deps passthrough (Part 4.4) ---


def test_a_tool_can_ask_for_deps() -> None:
    seen: list[object] = []

    @tool(parameters={"type": "object", "properties": {}, "required": [],
                      "additionalProperties": False})
    def wants_it(*, deps: Deps) -> str:
        """Takes deps."""
        seen.append(deps)
        return "ok"

    deps = Deps()
    assert run_tools([call("wants_it")], Registry([wants_it]), deps)[0].content == "ok"
    assert seen == [deps]


def test_deps_is_never_advertised_to_the_model() -> None:
    import inspect

    @tool(parameters={"type": "object", "properties": {}, "required": [],
                      "additionalProperties": False})
    def wants_it(*, deps: Deps) -> str:
        """Takes deps."""
        return "ok"

    rendered = str(inspect.signature(wants_it.handler))
    assert rendered.count("deps") == 1          # the prepended one, not the declared one


# --- Deps (Part 3) ---


def test_deps_satisfies_the_protocol_with_the_permissive_default() -> None:
    assert Deps().before_tool(call("anything")) is None


def test_deps_compares_equal_across_instances() -> None:
    """The reason allow_all is a named function and not a lambda."""
    assert Deps() == Deps()


# --- final_text (Part 5.2) ---


def test_final_text_returns_the_last_assistant_message() -> None:
    transcript = Transcript()
    transcript.add_user_message("hi")
    transcript.add_assistant_message([TextPart(text="one"), TextPart(text=" two")])
    assert final_text(transcript) == "one two"


def test_final_text_is_empty_when_nothing_was_said() -> None:
    assert final_text(Transcript()) == ""


# --- the package surface (Part 6) ---


def test_importing_agentloop_pulls_in_no_provider_sdk() -> None:
    """The re-exports must not undo chapter 9's lazy imports."""
    import subprocess
    import sys

    code = (
        "import agentloop, sys; "
        "print([m for m in ('anthropic', 'httpx') if m in sys.modules])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


# --- configuration errors surface at construction ---


class QuietClient:
    name, model = "fake", "m"

    def __init__(self) -> None:
        self.closed = False

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content=(TextPart(text="hi"),), stop_reason=StopReason.END_TURN,
                             usage=Usage(), model="m", provider="fake")

    def close(self) -> None:
        self.closed = True


def test_a_misspelled_loop_option_fails_at_construction() -> None:
    with pytest.raises(TypeError, match="max_iteration"):
        Agent([], client=QuietClient(), max_iteration=3)  # type: ignore[call-arg]


def test_loop_options_reach_the_loop() -> None:
    agent = Agent([], client=QuietClient(), max_iterations=0)
    with pytest.raises(LoopBudgetExceeded, match="0 iterations"):
        agent.run("go")


# --- lifecycle: close what you built, not what you were given ---


def test_an_agent_does_not_close_a_client_it_was_given() -> None:
    client = QuietClient()
    with Agent([], client=client):
        pass
    assert not client.closed


def test_an_agent_closes_the_client_from_env_built() -> None:
    agent = Agent.from_env({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-or-x"}, [])
    http = agent._client._inner._http  # type: ignore[attr-defined]
    agent.close()
    assert http.is_closed


# --- hosted tools: an explicit choice, checked at construction ---

SEARCH = HostedTool(kind="web_search")


class SearchingClient(QuietClient):
    hosted_tools = frozenset({"web_search"})

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return super().complete(request)


def test_a_hosted_tool_reaches_every_request() -> None:
    client = SearchingClient()
    Agent([SEARCH], client=client).run("go")
    assert client.requests[0].hosted_tools == (SEARCH,)
    assert client.requests[0].tools == ()           # not in the registry


def test_a_hosted_tool_the_client_cannot_run_fails_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="fake cannot run the hosted tool 'web_search'"):
        Agent([SEARCH], client=QuietClient())


def test_a_tool_named_like_a_hosted_tool_is_refused() -> None:
    @tool(parameters=SCHEMA, name="web_search")
    def my_search(timezone: str | None) -> str:
        """Search."""
        return ""

    with pytest.raises(ConfigurationError, match="collide"):
        Agent([my_search, SEARCH], client=SearchingClient())


@pytest.mark.parametrize(
    ("env", "supported"),
    [
        ({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-x"}, True),
        ({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-x"}, True),       # responses
        ({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-x", "OPENAI_API": "completions"}, False),
        ({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-or-x"}, False),
        ({"LLM_PROVIDER": "modal", "MODAL_ENDPOINT_URL": "https://x.modal.run"}, False),
    ],
)
def test_native_search_is_supported_where_the_provider_runs_it(
    env: dict[str, str], supported: bool
) -> None:
    if supported:
        Agent.from_env(env, [SEARCH]).close()
    else:
        with pytest.raises(ConfigurationError, match="cannot run the hosted tool"):
            Agent.from_env(env, [SEARCH])
