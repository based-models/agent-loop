# 08 — Resilience

One exception hierarchy, one retry decorator, one place.

Builds `src/harness/retry.py`, and the `classify_http_error` half of
`src/harness/providers/_openai_compat.py`. The exception types themselves came with the
seam in chapter 3.

---

## The hierarchy you already have

Chapter 3 defined `errors.py` as part of the seam contract — `ProviderError` with its
`retryable` class attribute, the four "your bug" subclasses, the four retryable ones, and
`ContextLengthExceeded` sitting under `BadRequestError`. Nothing to add here; go back and
re-read it if it isn't fresh.

This chapter builds the two things that *use* it: `classify_http_error` in
`providers/_openai_compat.py`, which turns a failed response into the right subclass, and
`retry.py`, which acts on `retryable`.

Recall the split, because it is what the rest of the chapter turns on:

- **Your bug** — `BadRequestError`, `AuthError`, `ModelNotFound`. Retrying is pure waste; fix the request.
- **Their problem, temporarily** — `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`. Retry.
- **Nobody's fault, structurally** — `ContextLengthExceeded`, `InsufficientCredits`. Retrying makes it worse.

---

## Classification, per provider

The table you will actually consult.

| | Retryable | Not retryable | Notes |
|---|---|---|---|
| **Anthropic** | 429 `rate_limit_error` (honor `retry-after`), 500 `api_error`, **529 `overloaded_error`**, 408, 409, connection errors | 400, 401, 403, 404, 413 | **The SDK already retries twice.** Don't stack. |
| **OpenRouter** | 429 (optional `Retry-After`), 502 (model down / bad upstream response), 503 (no provider meets routing requirements) | 400, 401, **402 insufficient credits**, 403 (moderation), 408 | **A 200 is not success** — see below |
| **Modal / vLLM** | 429 (Modal scale limits), 5xx (cold start, container death), **read timeouts** | 400 (template render, context length), 401 (proxy auth — **plain-text body**) | Cold starts masquerade as timeouts |

Three of those deserve more than a table row.

### 529 is Anthropic-specific

Not a standard HTTP code. `overloaded_error` means Anthropic's capacity, not yours — always retryable, and the one people most often miss because generic `5xx` handling written against `>= 500` happens to catch it by accident and `in (500, 502, 503)` doesn't.

### OpenRouter: a 200 is not success

Once a response is underway, failures arrive **inside a 200 body** — as `finish_reason: "error"` or a top-level `error` object. Chapter 5 checks for it before reading `choices`. Every generic HTTP client wrapper misses this, because everything about the response says it worked.

### Modal: the 401 that isn't JSON

Chapter 5 built `classify_http_error` and its `_extract_message` helper for exactly this:
Modal's proxy returns `modal-http: missing credentials for proxy authorization` as **plain
text**, not an OpenAI error envelope. An error handler that reaches straight for
`response.json()["error"]["message"]` raises `JSONDecodeError` *while reporting an auth
failure*, and you get a traceback pointing at your error handler instead of at your missing
header. That is what the `errors_are_json` quirk guards.

The lesson generalizes past Modal: **never assume an error body is JSON.** Proxies, load
balancers, and gateways all sit in front of your provider, and none of them speak its error
format.

---

## Context length: three shapes, one exception

Chapters 4 and 5 both raise `ContextLengthExceeded`. It is worth seeing the three shapes
side by side, because only one of them looks like an error at the HTTP level:

| Provider | How it arrives |
|---|---|
| **Anthropic** | `stop_reason: "model_context_window_exceeded"` — in an **HTTP 200** — or a 400 on input |
| **OpenAI / OpenRouter** | 400 with `error.code == "context_length_exceeded"` |
| **vLLM** | 400, message *"This model's maximum context length is 8192 tokens. However, you requested 254457 tokens…"* — and separately `"max_tokens must be at least 1, got -186"` |

Chapter 5's `_CONTEXT_MARKERS` handles the last two; chapter 4 raises it directly off the
stop reason. What matters here is what the loop does about it.

### Two rules

**Non-retryable and loop-terminating.** Compaction is out of scope (chapter 12), so the honest behavior is to **fail loudly**. A loop that retries this spins until the budget runs out, having achieved nothing.

**The adapter must not mutate the transcript on this path.** Tempting — drop the oldest messages and retry. Don't: the caller owns recovery, and a half-trimmed transcript is exactly how you manufacture F2 (an assistant turn whose tool results got trimmed away).

When you do add compaction, this exception is the trigger you already raise.

---

## Timeouts

```python
httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)     # OpenRouter
httpx.Timeout(connect=60.0, read=900.0, write=30.0, pool=10.0)     # Modal
```

**`httpx`'s default timeout is 5 seconds.** For an LLM call that is catastrophic and it is the classic hand-rolled-client footgun. The symptom is intermittent `ReadTimeout` on exactly the slow, interesting requests — the ones where the model was thinking hardest.

Separate phases, not one number:

- `connect` — TCP + TLS. Normally fast. **Except on Modal**, where the first request after idle spins up a container and loads weights.
- `read` — waiting for the response. This is the one that must be generous.
- `write` / `pool` — rarely the problem; set them so they're not a mystery later.

**Modal's cold start presents as a timeout.** Minutes, not seconds. Hence `connect=60`, `read=900`, and hence `ProviderTimeout.retryable = True` — the retry is usually just the container finally being warm.

For comparison, the Anthropic SDK defaults to **600 seconds** (Python: seconds; TypeScript: milliseconds, if you ever port this). That's a reasonable number for this workload, and a useful reference point for how wrong 5 seconds is.

### Three independent budgets

Keep these distinct in your head — they compose multiplicatively, which surprises people:

| Budget | Owner | Bounds |
|---|---|---|
| Per-request timeout | The HTTP client | One network round trip |
| Retry budget | `retry.py` | Attempts per `complete()` call |
| Loop budget | `run_conversation` (F10) | Iterations and wall clock for the whole run |

Wall clock for one `complete()` can reach `timeout × (attempts + 1)`. Chapter 7's `deadline_s` exists because `max_iterations × that` is not a bound you want to discover in production.

---

## The retry decorator

Retry is a **wrapper client**, not a modified one. `RetryingClient` holds another
`LLMClient` and satisfies the same protocol, so the loop cannot tell the difference:

```python
class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=1.0, gt=0)
    max_delay: float = Field(default=30.0, gt=0)


class RetryingClient:
    """Wraps any LLMClient, retrying the errors that are worth retrying.

    Satisfies LLMClient itself, so it can be used anywhere the inner client can.
    """

    def __init__(self, inner: LLMClient, *, policy: RetryPolicy | None = None,
                 observer: LoopObserver | None = None) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy()
        self._observer = observer or NullObserver()

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: ModelRequest) -> ModelResponse:
        policy = self._policy
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self._inner.complete(request)
            except ProviderError as error:
                is_last_attempt = attempt == policy.max_attempts
                if not error.retryable or is_last_attempt:
                    raise
                delay = self._delay_for(attempt, error)
                self._observer.on_retry(attempt, error)
                logger.warning(
                    "%s (attempt %d/%d); retrying in %.1fs",
                    error, attempt, policy.max_attempts, delay,
                )
                time.sleep(delay)
        raise AssertionError("unreachable: the loop above always returns or raises")

    def _delay_for(self, attempt: int, error: ProviderError) -> float:
        """Honor the server's Retry-After if it sent one; otherwise back off."""
        if error.retry_after is not None:
            return error.retry_after
        backoff = self._policy.base_delay * (2 ** (attempt - 1))
        jitter = random.uniform(0, 0.5)
        return min(backoff + jitter, self._policy.max_delay)
```

> **Python note.** `@property` turns a method into something you read like a plain
> attribute — `client.name`, not `client.name()`. That is what lets `RetryingClient`
> stand in for the client it wraps: chapter 3's protocol says `name` and `model` are
> attributes, and a property satisfies that.

Five things.

**It wraps the client, it doesn't modify it.** `build_client()` composes them (chapter 9):

```python
return RetryingClient(inner_client, policy=RetryPolicy(max_attempts=settings.max_retries))
```

The obvious shortcut here is to reassign the method — `client.complete = with_retry(client.complete)`.
Don't. It needs a `# type: ignore` to get past a type checker, it leaves two objects that
behave differently depending on whether you call the method or look it up, and it breaks
if the client ever uses `__slots__`. A wrapper class costs eight more lines and has none
of those properties.

So the loop calls one function, and retry policy is per client — which is what lets the
Anthropic client opt out entirely.

**Blind retry is only safe because `complete()` is pure.** Chapter 3 said this was one of two reasons for the shape; this is the other one arriving. If `complete()` mutated the transcript, or appended anything, retrying would double it. It doesn't, so it can't.

**Honor `retry-after` when the server sends it.** Both Anthropic and OpenRouter do on 429. The server knows when it will have capacity; your exponential backoff is a guess.

**Jitter.** `random.uniform(0, 0.5)`. Without it, N concurrent agents that all got rate-limited at the same moment retry in lockstep and rate-limit each other again. Cheap insurance.

**`self._observer.on_retry`.** This is why chapter 7 put it on the observer. When chapter 10 arrives, retries appear as events *inside* the model span — otherwise a retried request looks like one very slow call and you'll go looking for a slow model.

### Don't stack retries on the Anthropic SDK

```python
# config.py — the Anthropic client is NOT wrapped in RetryingClient
return AnthropicClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
```

The Anthropic SDK retries **twice by default**, with backoff, on 408/409/429/5xx and connection errors.

Wrap it in `RetryingClient(max_attempts=3)` and you get 3 × 3 = **nine requests** for one transient 429, over a wall clock long enough that a caller assumes a hang. Either set `max_retries=0` on the SDK client and use `RetryingClient`, or leave the SDK's retries alone and skip the wrapper. This tutorial does the latter — fewer moving parts, and the SDK's implementation already honors `retry-after`.

The OpenAI-compatible adapter is raw `httpx` and has no retry logic of its own, so it gets wrapped.

---

## `ResponseInvalid` is retryable, deliberately

Chapters 4 and 5 both raise it on an empty assistant turn:

```python
if not parts:
    raise ResponseInvalid(f"{self.name} returned an empty assistant turn")
```

Retrying a *successful* HTTP response feels wrong, so it's worth stating why it's right: an empty turn is almost always a transient server bug — a tool-call parser that found nothing, a race in a streaming aggregator — not a decision the model made. One retry usually produces a normal response. And the alternative is worse: an empty turn appended to the transcript violates chapter 2's invariants and fails on the *next* request, far from the cause.

If it happens repeatedly, `max_attempts` surfaces it as an error with a clear name rather than as a mysterious 400 two turns later.

---

## Checkpoint

`tests/test_ch08_resilience.py`:

```python
import time

import pytest

from harness.errors import (
    AuthError, BadRequestError, ContextLengthExceeded,
    ProviderUnavailable, RateLimited, ResponseInvalid,
)
from harness.loop import ListObserver
from harness.provider import ModelRequest, ModelResponse, StopReason, Usage
from harness.retry import RetryPolicy, RetryingClient
from harness.transcript import TextPart

FAST = RetryPolicy(base_delay=0.01)
OK = ModelResponse(content=(TextPart("ok"),), stop_reason=StopReason.END_TURN,
                   usage=Usage(), model="m", provider="flaky")


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


def test_retryable_is_a_property_of_the_class():
    assert RateLimited("x").retryable is True
    assert ProviderUnavailable("x").retryable is True
    assert ResponseInvalid("x").retryable is True
    assert BadRequestError("x").retryable is False
    assert AuthError("x").retryable is False
    assert ContextLengthExceeded("x").retryable is False


def test_context_length_is_a_bad_request_not_a_rate_limit():
    # so `except BadRequestError` catches it, and retry never does
    assert isinstance(ContextLengthExceeded("x"), BadRequestError)


def test_retries_then_succeeds():
    inner = FlakyClient(ProviderUnavailable("529 overloaded"),
                        ProviderUnavailable("529 overloaded"))
    assert RetryingClient(inner, policy=FAST).complete(REQUEST) is OK
    assert inner.call_count == 3


def test_does_not_retry_a_client_error():
    inner = FlakyClient(BadRequestError("400 your schema is wrong"))
    with pytest.raises(BadRequestError):
        RetryingClient(inner, policy=FAST).complete(REQUEST)
    assert inner.call_count == 1                 # exactly one attempt


def test_honors_retry_after_over_its_own_backoff():
    inner = FlakyClient(RateLimited("429", retry_after=0.05))
    slow = RetryPolicy(base_delay=10.0)          # backoff would wait 10 seconds
    started = time.monotonic()
    RetryingClient(inner, policy=slow).complete(REQUEST)
    assert 0.04 < time.monotonic() - started < 1.0


def test_gives_up_and_reraises_the_last_error():
    inner = FlakyClient(*[RateLimited("429 always")] * 5)
    with pytest.raises(RateLimited, match="always"):
        RetryingClient(inner, policy=RetryPolicy(max_attempts=2,
                                                 base_delay=0.01)).complete(REQUEST)
    assert inner.call_count == 2


def test_observer_sees_each_retry():
    observer = ListObserver()
    inner = FlakyClient(ProviderUnavailable("503"))
    RetryingClient(inner, policy=FAST, observer=observer).complete(REQUEST)
    assert [kind for kind, _ in observer.events] == ["retry"]


def test_the_wrapper_is_transparent():
    inner = FlakyClient()
    wrapped = RetryingClient(inner, policy=FAST)
    assert (wrapped.name, wrapped.model) == (inner.name, inner.model)
```

`REQUEST` is a shared empty request — put it in `tests/conftest.py` so every chapter can use it:

```python
# tests/conftest.py
from harness.provider import ModelRequest

REQUEST = ModelRequest(messages=())
```

Plus one classification test per provider — these are the ones that catch a real regression:

```python
import httpx
from harness.providers._openai_compat import Quirks, classify_http_error

def test_modal_plain_text_401_does_not_crash_the_error_parser():
    r = httpx.Response(401, text="modal-http: missing credentials for proxy authorization",
                       request=httpx.Request("POST", "http://x/v1/chat/completions"))
    err = classify_http_error(r, provider="modal", quirks=Quirks(errors_are_json=False))
    assert isinstance(err, AuthError)
    assert "missing credentials" in str(err)     # the message survived

def test_vllm_context_overflow_is_classified_by_message():
    body = {"object": "error", "code": 400,     # NOTE: integer, not a string code
            "message": "This model's maximum context length is 8192 tokens. "
                       "However, you requested 254457 tokens"}
    r = httpx.Response(400, json=body,
                       request=httpx.Request("POST", "http://x/v1/chat/completions"))
    err = classify_http_error(r, provider="modal", quirks=Quirks(errors_are_json=True))
    assert isinstance(err, ContextLengthExceeded)

def test_openrouter_402_is_not_retryable():
    r = httpx.Response(402, json={"error": {"message": "Insufficient credits"}},
                       request=httpx.Request("POST", "http://x/v1/chat/completions"))
    assert classify_http_error(r, provider="openrouter",
                               quirks=Quirks()).retryable is False
```

```bash
pytest tests/test_ch08_resilience.py -v
```

Eleven pass. `test_modal_plain_text_401_does_not_crash_the_error_parser` is the one that will save you an afternoon — it fails loudly the moment someone "simplifies" `_extract_message` into a bare `r.json()`.

---

Next: [09-wire-up-and-run.md](09-wire-up-and-run.md) — it finally runs.

**Sources.** [Anthropic error codes](https://platform.claude.com/docs/en/api/errors) · [OpenRouter error codes](https://openrouter.ai/docs/api-reference/errors) · [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/) · [vLLM issue #4667](https://github.com/vllm-project/vllm/issues/4667) (the `got -186` shape) · [Modal proxy auth](https://modal.com/docs/guide/webhook-proxy-auth) · [httpx timeouts](https://www.python-httpx.org/advanced/timeouts/)
