"""Every exception this library raises.

Provider errors carry enough context to decide whether retrying is worthwhile;
`retryable` is a class attribute because that is a property of the error kind.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agentloop.summary import ConversationSummary


class FailurePhase(StrEnum):
    """Where in the request's life a provider call failed.

    The adapter is the only code that sees the transport exception, so it names the
    phase once and callers never walk `__cause__` themselves.
    """

    CONNECT = "connect"  # DNS, TCP, connect timeout
    TLS = "tls"  # handshake or certificate verification
    PROXY = "proxy"  # the proxy refused or could not be used
    SEND = "send"  # writing the request
    READ = "read"  # waiting for or reading the response
    STATUS = "status"  # the server answered with an error status
    REDIRECT = "redirect"  # the server answered with a 3xx
    BODY = "body"  # a success status whose body we could not use


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
        phase: FailurePhase | None = None,
        request_sent: bool | None = None,
        redirect_host: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id
        self.phase = phase
        # False: nothing left this machine, so nothing was billed. True: the request
        # may have reached the provider. None: the adapter could not tell, which is
        # not the same as False.
        self.request_sent = request_sent
        # Host only, never a path or query: those can carry credentials.
        self.redirect_host = redirect_host
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


class UnexpectedRedirect(ProviderError):
    """A 3xx. Redirects are never followed: that would resend credentials to a new
    host, and a login or proxy page is a fault in the environment, not an answer."""


# --- their problem, probably temporary: retry ---

class RateLimited(ProviderError):
    """429. Honour `retry_after` when the server sends one."""

    retryable = True


class ProviderUnavailable(ProviderError):
    """5xx (including Anthropic's 529 overloaded_error), 409, or a transient
    transport failure such as a reset connection. See `phase`."""

    retryable = True


class ProviderUnreachable(ProviderUnavailable):
    """This machine cannot reach the provider: TLS verification, DNS or a proxy
    refusal. A fault in the environment, so retrying cannot help."""

    retryable = False


class ProviderTimeout(ProviderError):
    """The request timed out. On Modal this is usually a cold start."""

    retryable = True


class ResponseInvalid(ProviderError):
    """A 200 we could not normalize — usually an empty assistant turn."""

    retryable = True
