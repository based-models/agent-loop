"""Retry policy, in one provider-agnostic place.

`RetryingClient` wraps another LLMClient and satisfies the same protocol, so
the loop cannot tell the difference. Blind retry is safe here only because
`complete()` is pure: it makes a request and returns a value, changing nothing.
"""

from __future__ import annotations

import logging
import random
import time

from pydantic import BaseModel, ConfigDict, Field

from agentloop.errors import ProviderError
from agentloop.observer import LoopObserver, guarded
from agentloop.provider import (
    AttemptRecord,
    LLMClient,
    ModelRequest,
    ModelResponse,
    supported_hosted_tools,
)

logger = logging.getLogger(__name__)

JITTER_SECONDS = 0.5


class RetryPolicy(BaseModel):
    """How hard to try. Defaults suit an interactive session."""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=1.0, gt=0)
    max_delay: float = Field(default=30.0, gt=0)


class RetryingClient:
    """An LLMClient that retries the errors worth retrying."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        policy: RetryPolicy | None = None,
        observer: LoopObserver | None = None,
    ) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy()
        self._observer = guarded(observer)

    # Properties, not attributes: this is how the wrapper stays substitutable.
    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def hosted_tools(self) -> frozenset[str]:
        return supported_hosted_tools(self._inner)

    @property
    def api(self) -> str | None:
        """Which of a provider's APIs the inner client speaks, if it has several."""
        return getattr(self._inner, "api", None)

    def close(self) -> None:
        """Close the wrapped client, if it has anything to close."""
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    def complete(self, request: ModelRequest) -> ModelResponse:
        policy = self._policy
        waited = 0.0  # seconds spent sleeping between attempts
        log: list[AttemptRecord] = []
        for attempt in range(1, policy.max_attempts + 1):
            try:
                response = self._inner.complete(request)
            except ProviderError as error:
                is_last_attempt = attempt == policy.max_attempts
                if not error.retryable or is_last_attempt:
                    # So a summary can count retries for a call that never answered.
                    error.attempts = attempt
                    error.retry_wait_seconds = waited
                    log.append(AttemptRecord.from_error(attempt, error))
                    error.attempt_log = tuple(log)
                    raise
                delay = self._delay_for(attempt, error)
                log.append(AttemptRecord.from_error(attempt, error, wait=delay))
                self._observer.on_retry(attempt, error)
                logger.warning(
                    "%s (attempt %d/%d); retrying in %.1fs",
                    error,
                    attempt,
                    policy.max_attempts,
                    delay,
                )
                time.sleep(delay)
                waited += delay
            else:
                if attempt == 1:
                    return response
                log.append(
                    AttemptRecord(attempt=attempt, ok=True, request_id=response.request_id)
                )
                return response.model_copy(
                    update={
                        "attempts": attempt,
                        "retry_wait_seconds": waited,
                        "attempt_log": tuple(log),
                    }
                )
        raise AssertionError("unreachable: the loop always returns or raises")

    def _delay_for(self, attempt: int, error: ProviderError) -> float:
        """Honour the server's Retry-After if it sent one; otherwise back off.

        Still capped at `max_delay`: a server-supplied value is a request, not
        an override of the caller's own ceiling -- a hostile or misconfigured
        `Retry-After: 3600` should not block far past what the policy allows.
        """
        if error.retry_after is not None:
            return min(error.retry_after, self._policy.max_delay)
        backoff = self._policy.base_delay * (2.0 ** (attempt - 1))
        # Jitter stops N rate-limited agents retrying in lockstep.
        return min(backoff + random.uniform(0, JITTER_SECONDS), self._policy.max_delay)
