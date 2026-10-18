import logging
import time

import pytest

from agentloop.errors import (
    AuthError,
    BadRequestError,
    ContextLengthExceeded,
    ProviderUnavailable,
    RateLimited,
    ResponseInvalid,
)
from agentloop.loop import ListObserver
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.retry import RetryingClient, RetryPolicy
from agentloop.transcript import TextPart

FAST = RetryPolicy(base_delay=0.01)
REQUEST = ModelRequest(messages=())
OK = ModelResponse(
    content=(TextPart(text="ok"),),
    stop_reason=StopReason.END_TURN,
    usage=Usage(),
    model="m",
    provider="flaky",
)


class FlakyClient:
    """Raises the queued errors, then succeeds. Counts its own calls."""

    name, model = "flaky", "m"

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.call_count = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        if self.errors:
            raise self.errors.pop(0)
        return OK


# --- the three buckets (Part 1) ---


def test_retryable_is_a_property_of_the_class() -> None:
    assert RateLimited("x").retryable is True
    assert ProviderUnavailable("x").retryable is True
    assert ResponseInvalid("x").retryable is True
    assert BadRequestError("x").retryable is False
    assert AuthError("x").retryable is False
    assert ContextLengthExceeded("x").retryable is False


def test_context_length_is_a_bad_request_not_a_rate_limit() -> None:
    # so `except BadRequestError` catches it, and retry never does
    assert isinstance(ContextLengthExceeded("x"), BadRequestError)


# --- the retry loop (Part 3) ---


def test_retries_then_succeeds() -> None:
    inner = FlakyClient(
        ProviderUnavailable("529 overloaded"), ProviderUnavailable("529 overloaded")
    )
    response = RetryingClient(inner, policy=FAST).complete(REQUEST)
    # stamped for summaries: how many attempts, and how long it slept between them
    assert response.model_copy(
        update={"retry_wait_seconds": 0.0, "attempt_log": ()}
    ) == OK.model_copy(update={"attempts": 3})
    assert len(response.attempt_log) == 3
    assert response.retry_wait_seconds > 0
    assert inner.call_count == 3


def test_a_first_try_success_is_returned_untouched() -> None:
    assert RetryingClient(FlakyClient(), policy=FAST).complete(REQUEST) is OK


def test_an_exhausted_call_reports_how_many_attempts_it_made() -> None:
    inner = FlakyClient(*[ProviderUnavailable("503") for _ in range(3)])
    with pytest.raises(ProviderUnavailable) as caught:
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    assert caught.value.attempts == 3


def test_does_not_retry_a_client_error() -> None:
    inner = FlakyClient(BadRequestError("400 your schema is wrong"))
    with pytest.raises(BadRequestError):
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    assert inner.call_count == 1               # exactly one attempt


def test_gives_up_and_reraises_the_last_error() -> None:
    inner = FlakyClient(*[RateLimited("429 always")] * 5)
    policy = RetryPolicy(max_attempts=2, base_delay=0.01)
    with pytest.raises(RateLimited, match="always"):
        RetryingClient(inner, policy=policy).complete(REQUEST)
    assert inner.call_count == 2


def test_honours_retry_after_over_its_own_backoff() -> None:
    inner = FlakyClient(RateLimited("429", retry_after=0.05))
    slow = RetryPolicy(base_delay=10.0)        # backoff alone would wait 10 seconds
    started = time.monotonic()
    RetryingClient(inner, policy=slow).complete(REQUEST)
    assert 0.04 < time.monotonic() - started < 1.0


def test_retry_after_is_still_capped_at_max_delay() -> None:
    # A server's Retry-After is a request, not an override of the caller's own
    # ceiling -- a hostile or misconfigured "wait an hour" must not block past
    # what the policy allows.
    inner = FlakyClient(RateLimited("429", retry_after=3600.0))
    capped = RetryPolicy(max_delay=0.05)
    started = time.monotonic()
    RetryingClient(inner, policy=capped).complete(REQUEST)
    assert time.monotonic() - started < 1.0


def test_context_length_is_never_retried() -> None:
    inner = FlakyClient(ContextLengthExceeded("does not fit"))
    with pytest.raises(ContextLengthExceeded):
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    assert inner.call_count == 1               # fail loudly, do not spin


# --- the wrapper (Part 2) ---


def test_the_wrapper_is_transparent() -> None:
    inner = FlakyClient()
    wrapped = RetryingClient(inner, policy=FAST)
    assert (wrapped.name, wrapped.model) == (inner.name, inner.model)


def test_the_observer_sees_each_retry() -> None:
    observer = ListObserver()
    inner = FlakyClient(ProviderUnavailable("503"))
    RetryingClient(inner, policy=FAST, observer=observer).complete(REQUEST)
    assert observer.kinds() == ["retry"]


def test_a_non_provider_error_is_not_retried() -> None:
    # a bug in our own code must not be mistaken for a flaky network
    inner = FlakyClient(ValueError("bug in our code"))
    with pytest.raises(ValueError):
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    assert inner.call_count == 1


class BrokenObserver(ListObserver):
    def on_retry(self, attempt: int, error: Exception) -> None:
        raise RuntimeError("tracer down")


def test_an_observer_that_raises_does_not_stop_the_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = FlakyClient(ProviderUnavailable("503"))
    with caplog.at_level(logging.ERROR, logger="agentloop.observer"):
        response = RetryingClient(inner, policy=FAST, observer=BrokenObserver()).complete(
            REQUEST
        )
    assert response.attempts == 2
    assert response.content == OK.content
    assert inner.call_count == 2
    assert "tracer down" in caplog.text


def test_close_reaches_the_inner_client() -> None:
    class Closable(FlakyClient):
        closed = 0

        def close(self) -> None:
            self.closed += 1

    inner = Closable()
    RetryingClient(inner).close()
    assert inner.closed == 1
    RetryingClient(FlakyClient()).close()      # nothing to close: not an error


# --- the per-attempt log ---


def test_a_success_after_retries_carries_one_record_per_attempt() -> None:
    inner = FlakyClient(
        RateLimited("slow", provider="flaky", status_code=429, request_id="r1"),
        ProviderUnavailable("down", provider="flaky"),
    )
    client = RetryingClient(inner, policy=FAST)
    log = client.complete(REQUEST).attempt_log
    assert [r.attempt for r in log] == [1, 2, 3]
    assert [r.ok for r in log] == [False, False, True]
    assert [r.error_type for r in log] == ["RateLimited", "ProviderUnavailable", None]
    assert log[0].status_code == 429
    assert log[0].request_id == "r1"
    assert log[0].wait_seconds > 0
    assert log[2].wait_seconds == 0


def test_a_first_try_success_has_an_empty_log() -> None:
    assert RetryingClient(FlakyClient(), policy=FAST).complete(REQUEST).attempt_log == ()


def test_a_terminal_failure_carries_the_whole_log() -> None:
    inner = FlakyClient(ProviderUnavailable("a"), AuthError("b"))
    with pytest.raises(AuthError) as caught:
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    log = caught.value.attempt_log
    assert [r.error_type for r in log] == ["ProviderUnavailable", "AuthError"]
    assert log[-1].wait_seconds == 0


def test_the_log_never_holds_the_error_message() -> None:
    inner = FlakyClient(ProviderUnavailable("secret-token-123"))
    log = RetryingClient(inner, policy=FAST).complete(REQUEST).attempt_log
    assert "secret-token-123" not in log[0].model_dump_json()
