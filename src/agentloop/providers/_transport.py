"""Name a transport failure once, so no caller walks `__cause__` to find out what it was.

Shared by the adapters that sit on httpx (directly, or underneath a vendor SDK).
httpx and ssl are imported inside the function: the extras stay optional.
"""

from __future__ import annotations

from agentloop.errors import (
    FailurePhase,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUnreachable,
    UnexpectedRedirect,
)

_CHAIN_DEPTH = 8


def _chain(exc: BaseException) -> list[BaseException]:
    """`exc` and everything it was raised from or while handling, outermost first."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [exc]
    while pending and len(seen) < _CHAIN_DEPTH:
        current = pending.pop(0)
        if current is None or any(current is s for s in seen):
            continue
        seen.append(current)
        pending.extend((current.__cause__, current.__context__))
    return seen


def classify_transport_error(exc: BaseException, *, provider: str) -> ProviderError:
    """Map a transport failure to an error that says what failed and whether it was sent.

    Environment faults (TLS verification, DNS, a refusing proxy) come back as the
    non-retryable `ProviderUnreachable`; they fail the same way on every attempt.
    Everything that may be transient stays retryable.
    """
    import ssl

    import httpx

    # A TLS or DNS failure is wrapped in a ConnectError, so look at the whole chain
    # for the specific cause before falling back to the transport type.
    chain = _chain(exc)
    message = str(exc)

    def unreachable(phase: FailurePhase) -> ProviderError:
        return ProviderUnreachable(
            message, provider=provider, phase=phase, request_sent=False
        )

    if any(isinstance(e, ssl.SSLError) for e in chain):
        return unreachable(FailurePhase.TLS)
    if any(isinstance(e, httpx.ProxyError) for e in chain):
        return unreachable(FailurePhase.PROXY)
    # socket.gaierror, matched by name: the library never imports socket.
    if any(type(e).__name__ == "gaierror" for e in chain):
        return unreachable(FailurePhase.CONNECT)

    for e in chain:
        if isinstance(e, httpx.ConnectTimeout | httpx.PoolTimeout):
            return ProviderTimeout(
                message, provider=provider, phase=FailurePhase.CONNECT, request_sent=False
            )
        if isinstance(e, httpx.ReadTimeout):
            return ProviderTimeout(
                message, provider=provider, phase=FailurePhase.READ, request_sent=True
            )
        if isinstance(e, httpx.WriteTimeout):
            return ProviderTimeout(message, provider=provider, phase=FailurePhase.SEND)
        if isinstance(e, httpx.ConnectError):
            return ProviderUnavailable(
                message, provider=provider, phase=FailurePhase.CONNECT, request_sent=False
            )
        if isinstance(e, httpx.WriteError):
            return ProviderUnavailable(message, provider=provider, phase=FailurePhase.SEND)
        if isinstance(e, httpx.ReadError | httpx.RemoteProtocolError):
            return ProviderUnavailable(
                message, provider=provider, phase=FailurePhase.READ, request_sent=True
            )
        if isinstance(e, httpx.TimeoutException):
            return ProviderTimeout(message, provider=provider)
    return ProviderUnavailable(message, provider=provider)


def redirect_error(
    status: int, location: str | None, *, provider: str, request_id: str | None = None
) -> UnexpectedRedirect:
    """A 3xx, reported with the target's host only: a path or query can hold a token."""
    from urllib.parse import urlsplit  # lazy: only a redirect needs it

    host = urlsplit(location).hostname if location else None
    target = f" to {host}" if host else ""
    return UnexpectedRedirect(
        f"{provider} answered {status}, a redirect{target}; redirects are not followed",
        provider=provider,
        status_code=status,
        request_id=request_id,
        phase=FailurePhase.REDIRECT,
        request_sent=True,
        redirect_host=host,
    )
