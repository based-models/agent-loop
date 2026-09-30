from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agentloop.config import Settings, build_client
from agentloop.errors import AgentLoopError, AuthError, ConfigurationError


def settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


# --- Settings (Part 1) ---


def test_an_unknown_provider_is_rejected_by_name() -> None:
    with pytest.raises(ValidationError, match="anthropic"):
        settings(provider="gemini")


def test_an_empty_key_reads_as_unset() -> None:
    # so the Anthropic SDK's credential chain still works (chapter 4)
    assert settings(anthropic_api_key="").anthropic_api_key is None


def test_a_real_key_is_wrapped_in_secretstr() -> None:
    key = settings(anthropic_api_key="sk-ant-secret").anthropic_api_key
    assert key is not None
    assert key.get_secret_value() == "sk-ant-secret"


def test_secrets_do_not_appear_in_repr() -> None:
    # a stray print(settings) must not leak a key
    assert "sk-ant-secret" not in repr(settings(anthropic_api_key="sk-ant-secret"))


def test_settings_are_frozen() -> None:
    with pytest.raises(ValidationError):
        settings().provider = "modal"


def test_max_attempts_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError, match="max_attempts"):
        settings(max_attempts=0)


def test_max_attempts_is_read_from_the_mapping() -> None:
    assert Settings.from_env({"LLM_MAX_ATTEMPTS": "5"}).max_attempts == 5


def test_a_non_numeric_max_attempts_names_the_field() -> None:
    with pytest.raises(ValidationError, match="max_attempts"):
        Settings.from_env({"LLM_MAX_ATTEMPTS": "three"})


@pytest.mark.parametrize(
    ("provider", "field"),
    [("anthropic", "anthropic_model"), ("openrouter", "openrouter_model"),
     ("modal", "modal_endpoint_model"), ("openai", "openai_model")],
)
def test_model_is_the_configured_providers(provider: str, field: str) -> None:
    configured = settings(provider=provider, **{field: "the-one"})
    assert configured.model == "the-one"


# --- from_env (Part 1.3) ---


def test_from_env_reads_the_mapping_it_is_given() -> None:
    """A dict. No monkeypatch, no fixture, no ordering."""
    parsed = Settings.from_env({"LLM_PROVIDER": "modal",
                                "MODAL_ENDPOINT_URL": "https://x.modal.run"})
    assert parsed.provider == "modal"
    assert parsed.modal_endpoint_url == "https://x.modal.run"


def test_from_env_ignores_the_real_environment() -> None:
    """The point of taking a Mapping: os.environ is not consulted."""
    import os

    os.environ["LLM_PROVIDER"] = "openrouter"
    try:
        assert Settings.from_env({}).provider == "anthropic"      # the default
    finally:
        del os.environ["LLM_PROVIDER"]


# --- build_client (Part 2) ---


def test_openrouter_without_a_key_fails_at_startup() -> None:
    with pytest.raises(AuthError, match="OPENROUTER_API_KEY"):
        build_client(settings(provider="openrouter"))


def test_modal_without_an_endpoint_fails_at_startup() -> None:
    # ConfigurationError IS a ValueError (below), so this still passes unchanged.
    with pytest.raises(ValueError, match="MODAL_ENDPOINT_URL"):
        build_client(settings(provider="modal"))


def test_modal_proxy_tokens_are_both_or_neither() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        build_client(
            settings(
                provider="modal",
                modal_endpoint_url="https://x.modal.run",
                modal_proxy_token_id="wk-1",     # secret missing
            )
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: build_client(settings(provider="modal")),
        lambda: build_client(
            settings(
                provider="modal",
                modal_endpoint_url="https://x.modal.run",
                modal_proxy_token_id="wk-1",
            )
        ),
    ],
)
def test_a_bad_modal_setup_is_still_catchable_as_agentloop_error(
    build: Callable[[], object],
) -> None:
    # Every other provider's missing-config raises AuthError, an AgentLoopError.
    # Modal's raised bare ValueError instead -- `except AgentLoopError:
    # show_setup_instructions()` silently missed it. ConfigurationError closes
    # that gap without breaking the ValueError-shaped assertions above.
    with pytest.raises(ConfigurationError) as caught:
        build()
    assert isinstance(caught.value, AgentLoopError)


def test_an_unauthenticated_modal_endpoint_is_allowed() -> None:
    client = build_client(
        settings(provider="modal", modal_endpoint_url="https://x.modal.run")
    )
    assert client.name == "modal"


def test_openai_without_a_key_fails_at_startup() -> None:
    with pytest.raises(AuthError, match="OPENAI_API_KEY"):
        build_client(settings(provider="openai"))


def test_build_clients_unreachable_fallback_is_still_an_agentloop_error() -> None:
    # `Provider` is a Literal, so this can't happen through normal validation --
    # model_construct bypasses it, the same way a bug elsewhere in the codebase
    # could hand build_client a Settings it never validated.
    bogus = Settings.model_construct(provider="gemini")
    with pytest.raises(ConfigurationError):
        build_client(bogus)


def test_anthropic_is_wrapped_in_retries_too() -> None:
    client = build_client(settings(provider="anthropic", anthropic_api_key="sk-ant-x"))
    assert type(client).__name__ == "RetryingClient"
    assert client.name == "anthropic"


def test_openrouter_is_wrapped_in_retries() -> None:
    client = build_client(
        settings(provider="openrouter", openrouter_api_key="sk-or-x")
    )
    assert type(client).__name__ == "RetryingClient"
    assert client.name == "openrouter"          # the wrapper is transparent


def test_openai_is_wrapped_in_retries() -> None:
    client = build_client(settings(provider="openai", openai_api_key="sk-oai-x"))
    assert type(client).__name__ == "RetryingClient"
    assert client.name == "openai"


def test_config_imports_no_provider_sdk_at_module_scope() -> None:
    # what makes `pip install based-models-agentloop[openai-compat]` work without `anthropic`
    import ast
    import inspect

    import agentloop.config

    tree = ast.parse(inspect.getsource(agentloop.config))
    roots: set[str] = set()
    for node in tree.body:                      # module level ONLY
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert "anthropic" not in roots
    assert "httpx" not in roots


# --- which OpenAI API ---


def test_openai_defaults_to_responses() -> None:
    assert Settings.from_env({}).openai_api == "responses"


def test_openai_api_is_read_from_the_mapping() -> None:
    assert Settings.from_env({"OPENAI_API": "completions"}).openai_api == "completions"


def test_an_unknown_openai_api_names_the_field() -> None:
    with pytest.raises(ValidationError, match="openai_api"):
        Settings.from_env({"OPENAI_API": "chat"})


@pytest.mark.parametrize(("api", "hosted"), [("responses", {"web_search"}), ("completions", set())])
def test_build_client_speaks_the_configured_openai_api(api: str, hosted: set[str]) -> None:
    client = build_client(settings(provider="openai", openai_api_key="sk-x", openai_api=api))
    assert client.hosted_tools == frozenset(hosted)  # type: ignore[attr-defined]
    assert client.api == api                         # type: ignore[attr-defined]
