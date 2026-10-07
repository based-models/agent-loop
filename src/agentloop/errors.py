"""Every exception this library raises.

Provider errors carry enough context to decide whether retrying is worthwhile;
`retryable` is a class attribute because that is a property of the error kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agentloop.summary import ConversationSummary


class AgentLoopError(Exception):
    """Base for everything this library raises."""

    # Set by the loop on the way out, so a handler can see how the run went
    # without having supplied an observer. None for errors raised elsewhere.
    summary: ConversationSummary | None = None


class TranscriptError(AgentLoopError):
    """The transcript is in a state no provider will accept."""


class ToolError(AgentLoopError):
    """A tool failed in a way the model should see and can act on.

    Raised by tool HANDLERS, which live in the application, not here. `run_tools`
    renders this message verbatim; every other exception is rendered with its type
    name and logged with a traceback, because it is a bug rather than a condition.
    That distinction is the entire contract between a handler and the executor,
    which is why the exception type lives on this side of the seam.
    """


class LoopBudgetExceeded(AgentLoopError):
    """The agent loop ran out of iterations or wall-clock time.

    `kind`, `limit` and `actual` say which budget and by how much, so a handler
    never parses the message. They are None on an instance built without them.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["iterations", "deadline"] | None = None,
        limit: float | None = None,
        actual: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.limit = limit
        self.actual = actual


class ConfigurationError(AgentLoopError, ValueError):
    """Required configuration is missing or contradictory, before any provider is touched.

    Subclasses `ValueError` too, since that's what callers reasonably expect for a
    bad constructor argument -- but it's also an `AgentLoopError`, so `except
    AgentLoopError` catches a misconfigured provider the same way it catches a
    provider that rejected the request.
    """


class ProviderError(AgentLoopError):
    """Something went wrong talking to a provider."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id
        # Provider attempts made before giving up; RetryingClient sets it.
        self.attempts = 1
        self.retry_wait_seconds = 0.0


# --- your bug: retrying changes nothing ---

class AuthError(ProviderError):
    """401 / 403, or missing credentials."""


class BadRequestError(ProviderError):
    """400. Usually a malformed request on our side."""


class ContextLengthExceeded(BadRequestError):
    """The request does not fit in the model's context window."""


class ModelNotFound(ProviderError):
    """404, or a model id the provider does not serve."""


class InsufficientCredits(ProviderError):
    """OpenRouter 402."""


# --- their problem, probably temporary: retry ---

class RateLimited(ProviderError):
    """429. Honour `retry_after` when the server sends one."""

    retryable = True


class ProviderUnavailable(ProviderError):
    """5xx, including Anthropic's 529 overloaded_error."""

    retryable = True


class ProviderTimeout(ProviderError):
    """The request timed out. On Modal this is usually a cold start."""

    retryable = True


class ResponseInvalid(ProviderError):
    """A 200 we could not normalize — usually an empty assistant turn."""

    retryable = True
