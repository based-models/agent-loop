"""Configuration and client construction.

`Settings` is constructed explicitly -- there is deliberately no module-level
instance, and nothing here reads a file or a process environment. `from_env`
takes the mapping; the caller decides where it came from.

Provider SDKs are imported lazily so a single-extra install stays importable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import httpx
    import httpx2

from agentloop.errors import AuthError, ConfigurationError
from agentloop.observer import LoopObserver
from agentloop.provider import LLMClient
from agentloop.retry import RetryingClient, RetryPolicy

type Provider = Literal["anthropic", "openrouter", "modal", "openai"]
type OpenAIApi = Literal["responses", "completions"]


class Settings(BaseModel):
    """Runtime configuration. Construct it explicitly; `from_env` is opt-in.

    A plain BaseModel, not pydantic-settings: a dependency whose job is reaching os.environ would
    make the layering test's ban on `os` a comment rather than a property.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        protected_namespaces=(),  # several fields are legitimately named *_model
    )

    provider: Provider = "anthropic"

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5"

    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"

    modal_endpoint_url: str = ""
    modal_endpoint_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    modal_proxy_token_id: SecretStr | None = None
    modal_proxy_token_secret: SecretStr | None = None

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.1"
    # Responses is OpenAI's current API and the only one with hosted tools.
    openai_api: OpenAIApi = "responses"

    # TOTAL attempts, first one included: 3 means up to two retries. Applies to
    # every provider -- the Anthropic SDK's own retries are switched off.
    max_attempts: int = Field(default=3, ge=1)

    @field_validator(
        "anthropic_api_key",
        "openrouter_api_key",
        "modal_proxy_token_id",
        "modal_proxy_token_secret",
        "openai_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """An empty variable means "not configured", not "configured as empty".

        Also normalizes the `None` that `env.get()` returns for a missing key, so
        `from_env` can pass it straight through without an `or None` per field.
        """
        return None if value == "" else value

    @property
    def model(self) -> str:
        """The configured provider's model: what its client's `.model` will be."""
        match self.provider:
            case "anthropic":
                return self.anthropic_model
            case "openrouter":
                return self.openrouter_model
            case "modal":
                return self.modal_endpoint_model
            case "openai":
                return self.openai_model

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Translate an environment mapping into Settings.

        The CALLER supplies the mapping -- `os.environ`, a dict in a test, a
        parsed secrets blob. This function never goes looking for one, which is
        what lets `os` stay off the library's import allowlist.

        Written out longhand on purpose: this is every environment variable the
        program understands, in one greppable place.
        """
        return cls(
            provider=env.get("LLM_PROVIDER", "anthropic"),          # type: ignore[arg-type]
            anthropic_api_key=env.get("ANTHROPIC_API_KEY"),         # type: ignore[arg-type]
            anthropic_model=env.get("ANTHROPIC_MODEL", "claude-opus-5"),
            openrouter_api_key=env.get("OPENROUTER_API_KEY"),       # type: ignore[arg-type]
            openrouter_model=env.get("OPENROUTER_MODEL", "openrouter/free"),
            modal_endpoint_url=env.get("MODAL_ENDPOINT_URL", ""),
            modal_endpoint_model=env.get(
                "MODAL_ENDPOINT_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8"
            ),
            modal_proxy_token_id=env.get("MODAL_PROXY_TOKEN_ID"),       # type: ignore[arg-type]
            modal_proxy_token_secret=env.get("MODAL_PROXY_TOKEN_SECRET"),  # type: ignore[arg-type]
            openai_api_key=env.get("OPENAI_API_KEY"),                # type: ignore[arg-type]
            openai_model=env.get("OPENAI_MODEL", "gpt-5.1"),
            openai_api=env.get("OPENAI_API", "responses"),          # type: ignore[arg-type]
            # The raw string: pydantic coerces it, and rejects "three" with a
            # ValidationError that names the field, where int() would not.
            max_attempts=env.get("LLM_MAX_ATTEMPTS", "3"),  # type: ignore[arg-type]
        )


def build_client(
    settings: Settings,
    *,
    observer: LoopObserver | None = None,
    http_client: httpx.Client | httpx2.Client | None = None,
) -> LLMClient:
    """Construct the configured provider's client, ready to use.

    `http_client` carries your trust store, proxy or transport; it stays yours to close.
    It is an `httpx2.Client` for anthropic (its SDK is built on httpx2) and an
    `httpx.Client` for the others; the wrong one raises `ConfigurationError`.
    """
    # The helpers take the client untyped: each provider constructor checks the package
    # itself and raises ConfigurationError for the wrong one.
    inner: LLMClient
    match settings.provider:
        case "anthropic":
            # Wrapped like the others; the SDK's own retries are off (max_retries=0).
            inner = _build_anthropic(settings, http_client)
        case "openrouter":
            inner = _build_openrouter(settings, http_client)
        case "modal":
            inner = _build_modal(settings, http_client)
        case "openai":
            inner = _build_openai(settings, http_client)
        case _:
            raise ConfigurationError(f"unsupported provider: {settings.provider!r}")

    return RetryingClient(
        inner,
        policy=RetryPolicy(max_attempts=settings.max_attempts),
        observer=observer,
    )


def _build_anthropic(settings: Settings, http_client: Any) -> LLMClient:
    from agentloop.providers.anthropic import AnthropicClient  # lazy: keeps the extra optional

    key = settings.anthropic_api_key
    return AnthropicClient(
        # None lets the SDK resolve credentials itself.
        api_key=key.get_secret_value() if key else None,
        model=settings.anthropic_model,
        http_client=http_client,
    )


def _build_openrouter(settings: Settings, http_client: Any) -> LLMClient:
    from agentloop.providers.openrouter import build_openrouter_client

    if settings.openrouter_api_key is None:
        raise AuthError("OPENROUTER_API_KEY is not set")
    return build_openrouter_client(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.openrouter_model,
        http_client=http_client,
    )


def _build_modal(settings: Settings, http_client: Any) -> LLMClient:
    from agentloop.providers.modal import build_modal_client

    if not settings.modal_endpoint_url:
        raise ConfigurationError("MODAL_ENDPOINT_URL is not set")

    token_id = settings.modal_proxy_token_id
    token_secret = settings.modal_proxy_token_secret
    if (token_id is None) != (token_secret is None):
        raise ConfigurationError(
            "MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET must be set together "
            "(or both left unset for an --unauthenticated endpoint)"
        )

    return build_modal_client(
        endpoint_url=settings.modal_endpoint_url,
        model=settings.modal_endpoint_model,
        proxy_token_id=token_id.get_secret_value() if token_id else "",
        proxy_token_secret=token_secret.get_secret_value() if token_secret else "",
        http_client=http_client,
    )


def _build_openai(settings: Settings, http_client: Any) -> LLMClient:
    from agentloop.providers.openai import build_openai_client

    if settings.openai_api_key is None:
        raise AuthError("OPENAI_API_KEY is not set")
    return build_openai_client(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        api=settings.openai_api,
        http_client=http_client,
    )
