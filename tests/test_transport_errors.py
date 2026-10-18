"""Transport failures are named once, at the adapter, with whether the request was sent."""

import ssl
from collections.abc import Callable

import httpx
import pytest

from agentloop.errors import (
    FailurePhase,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUnreachable,
    ResponseInvalid,
    UnexpectedRedirect,
)
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.providers._openai_compat import OpenAICompatClient, Quirks
from agentloop.providers._transport import classify_transport_error
from agentloop.retry import RetryingClient, RetryPolicy
from agentloop.transcript import TextPart

REQUEST = ModelRequest(messages=())


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAICompatClient:
    client = OpenAICompatClient(
        name="test", model="m", base_url="https://x.test/v1", headers={},
        quirks=Quirks(), timeout=httpx.Timeout(1.0),
    )
    client._http = httpx.Client(
        base_url="https://x.test/v1", transport=httpx.MockTransport(handler)
    )
    return client


def raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def tls_failure() -> httpx.ConnectError:
    cause = ssl.SSLCertVerificationError("certificate verify failed")
    error = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    error.__cause__ = cause
    return error


def test_a_tls_failure_is_unreachable_and_was_not_sent() -> None:
    error = classify_transport_error(tls_failure(), provider="p")
    assert isinstance(error, ProviderUnreachable)
    assert isinstance(error, ProviderUnavailable)  # existing handlers still catch it
    assert error.retryable is False
    assert (error.phase, error.request_sent) == (FailurePhase.TLS, False)


def test_a_proxy_refusal_is_unreachable() -> None:
    error = classify_transport_error(httpx.ProxyError("407"), provider="p")
    assert isinstance(error, ProviderUnreachable)
    assert (error.phase, error.request_sent) == (FailurePhase.PROXY, False)


def test_a_dns_failure_is_unreachable() -> None:
    class gaierror(OSError):  # noqa: N801 - named like socket's, which we never import
        pass

    error = httpx.ConnectError("name resolution")
    error.__cause__ = gaierror("no such host")
    result = classify_transport_error(error, provider="p")
    assert isinstance(result, ProviderUnreachable)
    assert result.phase is FailurePhase.CONNECT


def test_a_refused_connection_is_transient_and_was_not_sent() -> None:
    error = classify_transport_error(httpx.ConnectError("refused"), provider="p")
    assert type(error) is ProviderUnavailable
    assert error.retryable is True
    assert (error.phase, error.request_sent) == (FailurePhase.CONNECT, False)


def test_a_read_failure_may_have_been_billed() -> None:
    error = classify_transport_error(httpx.ReadError("reset"), provider="p")
    assert type(error) is ProviderUnavailable
    assert (error.phase, error.request_sent) == (FailurePhase.READ, True)


@pytest.mark.parametrize(
    ("exc", "phase", "sent"),
    [
        (httpx.ConnectTimeout("t"), FailurePhase.CONNECT, False),
        (httpx.ReadTimeout("t"), FailurePhase.READ, True),
        (httpx.WriteTimeout("t"), FailurePhase.SEND, None),
    ],
)
def test_a_timeout_says_which_phase_and_whether_it_was_sent(
    exc: Exception, phase: FailurePhase, sent: bool | None
) -> None:
    error = classify_transport_error(exc, provider="p")
    assert isinstance(error, ProviderTimeout)
    assert (error.phase, error.request_sent) == (phase, sent)


def test_an_unknown_send_state_is_none_not_false() -> None:
    error = classify_transport_error(httpx.WriteError("broken pipe"), provider="p")
    assert error.request_sent is None


def test_an_unrecognised_transport_error_is_still_retryable_and_unknown() -> None:
    error = classify_transport_error(httpx.TransportError("odd"), provider="p")
    assert type(error) is ProviderUnavailable
    assert (error.phase, error.request_sent) == (None, None)


def test_the_client_raises_the_classified_error_from_the_original() -> None:
    original = tls_failure()
    with pytest.raises(ProviderUnreachable) as caught:
        client_with(raising(original)).complete(REQUEST)
    assert caught.value.__cause__ is original


def test_a_redirect_is_not_followed_and_names_only_the_host() -> None:
    location = "https://login.corp.example/sso?token=secret"
    client = client_with(lambda _: httpx.Response(307, headers={"location": location}))
    with pytest.raises(UnexpectedRedirect) as caught:
        client.complete(REQUEST)
    error = caught.value
    assert error.retryable is False
    assert (error.status_code, error.phase) == (307, FailurePhase.REDIRECT)
    assert error.redirect_host == "login.corp.example"
    assert "secret" not in str(error)


def test_a_redirect_without_a_location_has_no_host() -> None:
    client = client_with(lambda _: httpx.Response(302))
    with pytest.raises(UnexpectedRedirect) as caught:
        client.complete(REQUEST)
    assert caught.value.redirect_host is None


def test_a_non_json_body_reports_its_real_status() -> None:
    client = client_with(lambda _: httpx.Response(203, text="<html>"))
    with pytest.raises(ResponseInvalid, match="a 203") as caught:
        client.complete(REQUEST)
    assert caught.value.status_code == 203
    assert (caught.value.phase, caught.value.request_sent) == (FailurePhase.BODY, True)


def test_an_error_status_was_sent() -> None:
    client = client_with(lambda _: httpx.Response(503, json={"error": {"message": "down"}}))
    with pytest.raises(ProviderUnavailable) as caught:
        client.complete(REQUEST)
    assert (caught.value.phase, caught.value.request_sent) == (FailurePhase.STATUS, True)


# --- through the retry wrapper ---


class Counting:
    name = "counting"
    model = "m"
    api = "completions"
    hosted_tools: frozenset[str] = frozenset()

    def __init__(self, failures: list[ProviderError]) -> None:
        self.failures = failures
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ModelResponse(
            content=(TextPart(text="ok"),),
            stop_reason=StopReason.END_TURN,
            usage=Usage(),
            model="m",
            provider="counting",
        )

    def close(self) -> None:
        pass


def test_a_tls_failure_fails_fast_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("agentloop.retry.time.sleep", slept.append)
    inner = Counting([classify_transport_error(tls_failure(), provider="p")] * 3)
    with pytest.raises(ProviderUnreachable) as caught:
        RetryingClient(inner, policy=RetryPolicy(max_attempts=3)).complete(REQUEST)
    assert inner.calls == 1
    assert slept == []
    assert caught.value.attempts == 1


def test_a_reset_connection_is_still_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    inner = Counting([classify_transport_error(httpx.ReadError("reset"), provider="p")])
    response = RetryingClient(inner, policy=RetryPolicy(max_attempts=3)).complete(REQUEST)
    assert inner.calls == 2
    assert response.attempts == 2

