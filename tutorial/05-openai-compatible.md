# 05 — The OpenAI-compatible adapter

Two providers, one adapter. This is divergence **D4**, and it's the single highest-leverage structural decision in the project.

Builds `src/harness/providers/_openai_compat.py`, plus two thin configs in `openrouter.py` and `modal.py`.

---

## Why one adapter covers two providers

OpenRouter is an OpenAI-compatible gateway. Modal, in the vLLM deployment chapter 9 uses, serves an OpenAI-compatible `/v1`. Same JSON body, same `tool_calls` array, same `finish_reason` vocabulary, same `usage` field names.

They differ in base URL, auth headers, and six behaviors:

```python
class Quirks(BaseModel):
    model_config = ConfigDict(frozen=True)

    supports_strict_tools: bool = True
    replays_reasoning: bool = False           # OpenRouter: True (same model only)
    reports_usage_details: bool = True        # vLLM: False -> Usage fields stay None
    errors_are_json: bool = False             # Modal proxy-auth 401 is PLAIN TEXT
    supports_parallel_tool_calls_param: bool = True
    default_parallel_tool_calls: bool = True  # small self-hosted models: False
```

The reference course half-acknowledges this already — its `openrouter` and `modal` branches both construct `OpenAIChatModel` and differ only in the client passed in. We make the sharing explicit.

We use `httpx` directly rather than the `openai` SDK. Two reasons: the request body is a dict either way, so the SDK buys us nothing here; and Modal's non-JSON error bodies (below) are easier to handle when you own the response object.

---

## The client

```python
class OpenAICompatClient:
    def __init__(self, *, name: str, model: str, base_url: str,
                 headers: Mapping[str, str], quirks: Quirks,
                 timeout: httpx.Timeout) -> None:
        self.name = name
        self.model = model
        self._quirks = quirks
        self._http = httpx.Client(base_url=base_url.rstrip("/"),
                                  headers=dict(headers), timeout=timeout)

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request_to_wire(request, model=self.model, quirks=self._quirks)
        response = self._http.post("/chat/completions", json=payload)
        if response.status_code >= 400:
            raise classify_http_error(response, provider=self.name, quirks=self._quirks)
        return response_from_wire(response.json(), provider=self.name,
                                  quirks=self._quirks)
```

As in chapter 4, the translation is **module-level functions**, not methods. Everything
they need arrives as an argument, so the entire quirk-handling layer is testable by
calling a function with a dict.

### The timeout is not optional

**`httpx`'s default timeout is 5 seconds.** For an LLM call that is catastrophic, and it is the classic footgun in hand-rolled clients — the symptom is intermittent `ReadTimeout` on exactly the slow, interesting requests.

Always construct one explicitly, with separate phases:

```python
httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
```

Modal gets very different numbers. See below.

---

## Canonical → wire

```python
def request_to_wire(request: ModelRequest, *, model: str,
                    quirks: Quirks) -> dict[str, Any]:
    """Canonical request -> an OpenAI-compatible chat/completions body."""
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})   # exactly one
    for message in request.messages:
        messages.extend(message_to_wire(message, quirks))

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_tokens,
    }
    if request.tools:
        payload["tools"] = [tool_to_wire(t, quirks) for t in request.tools]
        payload["tool_choice"] = request.tool_choice
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.stop_sequences:
        payload["stop"] = list(request.stop_sequences)[:4]     # OpenAI caps at 4
    if quirks.supports_parallel_tool_calls_param:
        payload["parallel_tool_calls"] = (
            request.parallel_tool_calls and quirks.default_parallel_tool_calls
        )
    return payload
```

### Exactly one system message, by construction

`request.system` is a *field*, so this function can emit at most one system message, and it's always first.

That matters more than it looks. vLLM renders messages through the model's Jinja chat template, and many templates call `raise_exception` when a system message is not first or when a second one appears — Qwen3's template explicitly rejects a non-leading system message. vLLM surfaces that as a 400 template-render error, which reads like a server bug and isn't.

Chapter 2's decision to drop `"system"` from `Role` is what makes this class of failure unreachable.

### Tools — the envelope, and where `strict` goes

```python
def tool_to_wire(tool: ToolSchema, quirks: Quirks) -> dict[str, Any]:
    fn: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,          # same bare schema Anthropic got
    }
    if tool.strict and quirks.supports_strict_tools:
        fn["strict"] = True                     # INSIDE function — cf. Anthropic
    return {"type": "function", "function": fn}
```

Compare with chapter 4: Anthropic puts `strict` **top-level on the tool object**; here it goes **inside `function`**. Same word, different place. Getting it wrong is silent.

`tool.parameters` is the *same dict* both adapters receive — the envelope is pure packaging.

Under `strict: true`, OpenAI-compatible servers require `additionalProperties: false` on every object and **every** property listed in `required`; optional parameters are expressed as `"type": ["string", "null"]`. Chapter 6 authors schemas in that dialect once.

And never rely on `strict` for correctness. From vLLM's own docs on the non-strict path: it extracts tool calls from raw text, so arguments may be malformed or violate the schema. You validate yourself.

### The lossy direction

```python
def message_to_wire(message: Message, quirks: Quirks) -> list[dict[str, Any]]:
    if message.role == "user":
        results = [p for p in message.content if isinstance(p, ToolResultPart)]
        if results:
            # Anthropic's ONE user message with N results -> N `role: "tool"` messages,
            # in call order (chapter 2 already sorted them).
            return [{"role": "tool", "tool_call_id": r.call_id,
                     "name": r.name, "content": r.content} for r in results]
        text = "".join(p.text for p in message.content if isinstance(p, TextPart))
        return [{"role": "user", "content": text}]

    # assistant: an ORDERED list collapses into content + a sibling array
    text = "".join(p.text for p in message.content if isinstance(p, TextPart))
    calls = [p for p in message.content if isinstance(p, ToolCallPart)]

    msg: dict[str, Any] = {"role": "assistant",
                           "content": text or None}     # None, NOT ""
    if calls:
        msg["tool_calls"] = [{
            "id": c.id, "type": "function",
            "function": {
                "name": c.name,
                # Replay the verbatim wire string when we have it — see F4 below.
                "arguments": c.raw_arguments
                             if c.raw_arguments is not None else json.dumps(c.arguments),
            },
        } for c in calls]
    if quirks.replays_reasoning:
        ...   # reasoning_details, verbatim, same-model only
    return [msg]
```

Three things here.

**The fan-out.** Chapter 2 stored tool results as one user message with N parts. Anthropic took that as-is; here it becomes N separate `role: "tool"` messages, **in `tool_calls` order**. `add_tool_results` already sorted them, so this is a plain comprehension.

This asymmetry — trivial to fan out, requires lookahead to coalesce — is on its own enough to justify canonicalizing on Anthropic's grouping.

**`content: None`, not `""`.** Some strict servers reject an empty-string `content` alongside `tool_calls`. `None` is the safe representation of "no text".

**Interleaving is unrepresentable.** A canonical turn of `[text, tool_call, text]` becomes `"text1text2"` plus the call. Information is genuinely lost. It's lost at the edge, in one function, converting *out* — which is the best available outcome and exactly why the canonical form isn't shaped like this.

### Reasoning replay

`replays_reasoning` is per-provider policy, never loop logic:

| Provider | Policy |
|---|---|
| **OpenRouter** | Replay `reasoning_details` verbatim, **only if provider and model are unchanged**. OpenRouter's docs are explicit that the sequence of consecutive reasoning blocks must match what the model produced — you cannot rearrange or modify them. |
| **Modal / vLLM** | Drop. `reasoning_content` (emitted when the server runs a `--reasoning-parser`) is generally not replayable; most chat templates discard it. |

`ThinkingPart` carries `provider` and `model` precisely so this check is a comparison rather than a guess.

---

## Wire → canonical

### Parsing arguments: the one asymmetry that matters

Anthropic hands you a parsed object. Here you get a **string**, which may be malformed, may be `""`, and on some gateways may already be a dict.

Parse **once**, here, and never let the failure escape:

```python
def parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None, str | None]:
    """-> (arguments, raw_arguments, parse_error)"""
    if isinstance(raw, dict):                      # some gateways pre-parse
        return raw, None, None
    if not (raw or "").strip():                    # zero-arg tools send ""
        return {}, raw, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, raw, str(e)                     # keep the part; record the failure
    if not isinstance(parsed, dict):
        return {}, raw, f"expected an object, got {type(parsed).__name__}"
    return parsed, raw, None
```

Three invariants, each load-bearing:

1. **The failure never propagates.** No caller of `complete()` handles `JSONDecodeError`.
2. **A failed parse still produces a `ToolCallPart`** — with `arguments={}` and `parse_error` set. This is what keeps chapter 2's pairing invariant intact: the call exists, so chapter 6 can return an error *result* for it. Dropping the part would manufacture an unpaired-call 400.
3. **`raw_arguments` is replayed verbatim**, which is why `message_to_wire` prefers it over `json.dumps(arguments)`. Rewriting history to `{}` — something the model never said — confuses the model *and* invalidates any reasoning signature that covered the original bytes.

### Tool-call ID normalization

```python
for i, tc in enumerate(msg.get("tool_calls") or []):
    call_id = tc.get("id") or f"call_{i}"      # some servers omit ids
    if call_id in seen:
        call_id = f"{call_id}_{i}"             # ...and some duplicate them
    seen.add(call_id)
```

The loop is allowed to assume IDs are non-empty and unique within a turn. This is where that becomes true.

### Stop reasons

```python
_FINISH = {
    "stop":            StopReason.END_TURN,
    "tool_calls":      StopReason.TOOL_USE,
    "length":          StopReason.MAX_TOKENS,
    "content_filter":  StopReason.REFUSAL,
    "error":           StopReason.ERROR,        # OpenRouter, inside a 200
    "function_call":   StopReason.TOOL_USE,     # deprecated but still seen
}

stop = _FINISH.get(choice.get("finish_reason"), StopReason.UNKNOWN)
if any(isinstance(p, ToolCallPart) for p in parts):
    stop = StopReason.TOOL_USE                  # Rule 1
```

**Rule 1 earns its keep right here.** The most common self-hosted failure is a vLLM tool-call parser populating `tool_calls` while `finish_reason` comes back `"stop"` or `null`. Without the override, the loop sees `END_TURN`, stops, and the tool call silently never runs — no error, no log, just a turn that did nothing.

`raw_stop_reason` keeps the wire value. OpenRouter also returns `native_finish_reason` carrying the upstream provider's own value — log it, never branch on it.

### A 200 is not success

Specific to OpenRouter and worth its own guard: once a response is underway, failures can arrive as `finish_reason: "error"` or an `error` object in a 200 body.

```python
if (err := body.get("error")) is not None:
    raise classify_error_body(err, provider=provider)
```

Check for it before you look at `choices`.

### The empty-turn guard

```python
try:
    return ModelResponse(
        content=tuple(parts),
        stop_reason=stop,
        raw_stop_reason=choice.get("finish_reason"),
        usage=usage_from_wire(body.get("usage"), quirks),
        model=body.get("model", ""),
        provider=provider,
        request_id=body.get("id"),
    )
except ValidationError as e:
    raise ResponseInvalid(
        f"{provider} returned a response we cannot normalize "
        f"(finish_reason={choice.get('finish_reason')!r}): {e}",
        provider=provider,
    ) from e
```

Same rule as chapter 4: **pydantic errors are converted at the seam**, so the loop only
ever sees `ProviderError` subclasses.

vLLM and OpenRouter both occasionally return `content: null`, `tool_calls: null`,
`finish_reason: "stop"`. `ModelResponse.content` carries `Field(min_length=1)`
(chapter 3), so that response cannot be constructed, and this turns the failure into a
retryable `ResponseInvalid`. Chapter 8 retries once. An empty turn is a transient server
bug, not a model decision.

### Usage

```python
def usage_from_wire(raw: Mapping[str, Any] | None, quirks: Quirks) -> Usage:
    if not raw:
        return Usage()
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    detailed = quirks.reports_usage_details
    return Usage(
        input_tokens=raw.get("prompt_tokens", 0),
        output_tokens=raw.get("completion_tokens", 0),
        cache_read_tokens=prompt_details.get("cached_tokens") if detailed else None,
        cache_write_tokens=prompt_details.get("cache_write_tokens") if detailed else None,
        reasoning_tokens=completion_details.get("reasoning_tokens") if detailed else None,
        cost_usd=raw.get("cost"),        # OpenRouter reports this; vLLM doesn't
    )
```

vLLM omits `prompt_tokens_details` entirely, so those stay `None` — "not reported", per chapter 3. OpenRouter now returns full usage on every response; the older `usage: {include: true}` opt-in is deprecated.

---

## Turning a failed response into an exception

`complete()` above calls `classify_http_error`. It lives here, in the adapter, because
deciding what a 402 means is provider knowledge — and `errors.py` (chapter 3) must stay
free of HTTP concepts. Chapter 8 covers what to *do* with these; this is where they are
made.

```python
def classify_http_error(response: httpx.Response, *, provider: str,
                        quirks: Quirks) -> ProviderError:
    """Turn a failed HTTP response into the right exception subclass."""
    status = response.status_code
    detail = _extract_message(response, quirks)
    retry_after = _parse_retry_after(response.headers.get("retry-after"))
    kwargs = {"provider": provider, "status_code": status, "retry_after": retry_after}

    if status in (401, 403):
        return AuthError(detail, **kwargs)
    if status == 402:
        return InsufficientCredits(detail, **kwargs)
    if status == 404:
        return ModelNotFound(detail, **kwargs)
    if status == 408:
        return ProviderTimeout(detail, **kwargs)
    if status == 429:
        return RateLimited(detail, **kwargs)
    if status == 400:
        if _looks_like_context_overflow(detail):
            return ContextLengthExceeded(detail, **kwargs)
        return BadRequestError(detail, **kwargs)
    if status >= 500:
        return ProviderUnavailable(detail, **kwargs)
    return ProviderError(detail, **kwargs)
```

> **Python note.** `**kwargs` unpacks a dictionary into keyword arguments, so
> `AuthError(detail, **kwargs)` means
> `AuthError(detail, provider=provider, status_code=status, retry_after=retry_after)`.
> Building it once keeps nine branches readable.

### The part that must not assume JSON

```python
def _extract_message(response: httpx.Response, quirks: Quirks) -> str:
    if not quirks.errors_are_json:
        return response.text[:500]
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]          # a proxy answered, not the provider
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:500]
    return str(error or body)[:500]
```

This is the `errors_are_json=False` quirk earning its place. **Modal's proxy returns a
401 whose body is the plain string** `modal-http: missing credentials for proxy
authorization` — not an error envelope. Code that does
`response.json()["error"]["message"]` raises `JSONDecodeError` while trying to report an
auth failure, and the traceback points at your error handler instead of your missing
header.

Note the second `try` is there even on the JSON path: a load balancer can return an HTML
error page for a provider that normally speaks JSON.

### Context overflow has no machine-readable code on vLLM

```python
_CONTEXT_MARKERS = (
    "context_length_exceeded",          # OpenAI / OpenRouter error.code
    "maximum context length",           # vLLM message text
    "max_tokens must be at least 1",    # vLLM, when the budget went negative
)


def _looks_like_context_overflow(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _CONTEXT_MARKERS)
```

Matching on an error message is fragile, and it is the only option vLLM leaves: its error
body carries `"code": 400` — the **integer**, not a string error code — so there is
nothing else to branch on. The alternative is worse: a context overflow classified as a
generic `BadRequestError` looks like a schema bug, and you spend an afternoon auditing
tool definitions.

The `max_tokens must be at least 1, got -186` variant is the interesting one. It means
`prompt_tokens + max_tokens` exceeded the model's window, so vLLM computed a negative
budget. It reads like a validation bug in your code and is actually a context overflow.

### OpenRouter: a 200 is not always success

```python
def classify_error_body(error: Mapping[str, Any], *, provider: str) -> ProviderError:
    """OpenRouter can report failure inside an HTTP 200 body."""
    message = str(error.get("message") or error)
    code = error.get("code")
    if code == 429:
        return RateLimited(message, provider=provider, status_code=200)
    return ProviderError(message, provider=provider, status_code=200)
```

Called from `response_from_wire` before it looks at `choices`. Every generic HTTP wrapper
misses this case, because everything about the response says it worked.

---

## The two configs

### OpenRouter

```python
def build_openrouter_client(*, api_key: str, model: str = "openrouter/free",
                            app_url: str | None = None,
                            app_name: str | None = None) -> OpenAICompatClient:
    headers = {"Authorization": f"Bearer {api_key}"}
    if app_url:  headers["HTTP-Referer"] = app_url      # optional attribution
    if app_name: headers["X-Title"] = app_name
    return OpenAICompatClient(
        name="openrouter", model=model,
        base_url="https://openrouter.ai/api/v1",
        headers=headers,
        quirks=Quirks(replays_reasoning=True),
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
    )
```

`openrouter/free` spreads across free models and auto-filters for tool-calling support, so a single congested upstream can't hard-block you with 429s. Pin a specific `:free` model id if you want a stricter guarantee about what you're talking to.

### Modal

```python
def build_modal_client(*, endpoint_url: str, model: str,
                       proxy_token_id: str = "", proxy_token_secret: str = "",
                       ) -> OpenAICompatClient:
    headers: dict[str, str] = {}
    if proxy_token_id and proxy_token_secret:
        headers = {"Modal-Key": proxy_token_id, "Modal-Secret": proxy_token_secret}
    return OpenAICompatClient(
        name="modal", model=model,
        base_url=f"{endpoint_url.rstrip('/')}/v1",
        headers=headers,
        quirks=Quirks(
            reports_usage_details=False,        # vLLM omits prompt_tokens_details
            errors_are_json=False,              # proxy-auth 401 is plain text
            default_parallel_tool_calls=False,  # small models get this wrong
        ),
        timeout=httpx.Timeout(connect=60.0, read=900.0, write=30.0, pool=10.0),
    )
```

Three Modal-specific things, each of which will cost you an hour if you meet it without warning.

**Proxy auth is two headers, not a Bearer token.** `Modal-Key: wk-...` and `Modal-Secret: ws-...`, both or neither. These are *endpoint* proxy tokens, distinct from your `MODAL_TOKEN_*` account credentials. An endpoint deployed with `unauthenticated=True` needs neither.

**A 401 from the proxy is plain text, not JSON.** The body is literally:

```
modal-http: missing credentials for proxy authorization
```

Not an OpenAI error envelope. An error parser that does `r.json()["error"]["message"]` raises `JSONDecodeError` while trying to report an auth failure, and you get a stack trace pointing at your error handler instead of at your missing header. That is what `errors_are_json=False` guards — chapter 8 has the classifier.

**Cold starts take minutes.** `connect=60.0, read=900.0` — an order of magnitude beyond the other two. The first request after idle spins up a container and loads weights, and it *presents as a timeout*. Chapter 8 treats a read timeout on Modal as retryable for exactly this reason.

`default_parallel_tool_calls=False` is a judgment call worth stating: parallel tool-call quality varies sharply by model. vLLM's docs note that Llama 3 doesn't support it, Llama 4 does, and Mistral 7B struggles to generate parallel calls correctly. For a self-hosted small model, turning it off removes a whole class of flakiness at almost no cost.

---

## Checkpoint

`tests/test_ch05_openai_compat.py`:

```python
from harness.provider import ModelRequest, StopReason, ToolSchema
from harness.providers._openai_compat import (
    Quirks, message_to_wire, parse_arguments, request_to_wire,
    response_from_wire, tool_to_wire,
)
from harness.transcript import Message, ToolCallPart, ToolResultPart

DEFAULT = Quirks()
EMPTY_SCHEMA = {"type": "object", "properties": {},
                "required": [], "additionalProperties": False}


def wire(**kwargs) -> dict:
    return request_to_wire(ModelRequest(messages=(), **kwargs),
                           model="m", quirks=DEFAULT)


def body(finish_reason, message, usage=None) -> dict:
    """The minimum shape of a chat/completions response."""
    return {"choices": [{"finish_reason": finish_reason, "message": message}],
            "model": "m",
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 2}}


def test_one_user_message_fans_out_to_n_tool_messages():
    out = message_to_wire(Message(role="user", content=(
        ToolResultPart(call_id="c1", name="read", content="a"),
        ToolResultPart(call_id="c2", name="read", content="b"),
    )), DEFAULT)
    assert [m["role"] for m in out] == ["tool", "tool"]
    assert [m["tool_call_id"] for m in out] == ["c1", "c2"]    # call order preserved


def test_assistant_with_no_text_sends_none_not_empty_string():
    out = message_to_wire(Message(role="assistant", content=(
        ToolCallPart(id="c1", name="read", arguments={}),
    )), DEFAULT)
    assert out[0]["content"] is None        # "" is rejected by some strict servers


def test_exactly_one_system_message_and_it_is_first():
    messages = wire(system="be brief")["messages"]
    assert [m["role"] for m in messages].count("system") == 1
    assert messages[0]["role"] == "system"


def test_strict_goes_inside_function_not_top_level():
    tool = tool_to_wire(ToolSchema(name="read", description="d",
                                   parameters=EMPTY_SCHEMA), DEFAULT)
    assert tool["function"]["strict"] is True
    assert "strict" not in tool             # cf. Anthropic, where it IS top-level


def test_malformed_arguments_keep_the_call_and_record_the_error():
    arguments, raw, error = parse_arguments('{"path": "a.py"')      # truncated
    assert arguments == {}
    assert raw == '{"path": "a.py"'         # kept verbatim for replay
    assert error is not None


def test_empty_arguments_string_is_not_an_error():
    assert parse_arguments("") == ({}, "", None)      # zero-argument tools send ""


def test_already_parsed_arguments_pass_through():
    assert parse_arguments({"path": "a.py"}) == ({"path": "a.py"}, None, None)


def test_raw_arguments_are_replayed_verbatim():
    call = ToolCallPart(id="c1", name="read", arguments={},
                        raw_arguments='{"path": "a.py"', parse_error="truncated")
    out = message_to_wire(Message(role="assistant", content=(call,)), DEFAULT)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"'


def test_tool_calls_override_a_stop_finish_reason():
    response = response_from_wire(
        body("stop", {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": '{"path":"a.py"}'}}]}),
        provider="test", quirks=DEFAULT,
    )
    assert response.stop_reason is StopReason.TOOL_USE      # Rule 1
    assert response.raw_stop_reason == "stop"


def test_missing_tool_call_ids_are_generated():
    response = response_from_wire(
        body("tool_calls", {"content": None, "tool_calls": [
            {"type": "function", "function": {"name": "read", "arguments": "{}"}},
            {"type": "function", "function": {"name": "read", "arguments": "{}"}}]}),
        provider="test", quirks=DEFAULT,
    )
    ids = [p.id for p in response.content]
    assert all(ids) and len(set(ids)) == 2      # non-empty and unique


def test_missing_usage_details_stay_none():
    usage = response_from_wire(
        body("stop", {"content": "hi", "tool_calls": None}),
        provider="test", quirks=Quirks(reports_usage_details=False),
    ).usage
    assert usage.input_tokens == 5              # reported
    assert usage.cache_read_tokens is None      # NOT reported — not zero
```

```bash
pytest tests/test_ch05_openai_compat.py -v
```

Eleven pass, no network, and no client is ever constructed. Together with chapter 4's, these are your round-trip translation tables — chapter 11 extends them with real recorded response bodies.

---

Next: [06-tools.md](06-tools.md) — the registry, and the one function that must never raise.

**Sources.** [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling) · [OpenRouter reasoning tokens](https://openrouter.ai/docs/use-cases/reasoning-tokens) · [OpenRouter usage accounting](https://openrouter.ai/docs/use-cases/usage-accounting) · [OpenAI function calling / strict mode](https://developers.openai.com/api/docs/guides/function-calling) · [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) · [Modal proxy auth](https://modal.com/docs/guide/webhook-proxy-auth) · [Modal vLLM example](https://modal.com/docs/examples/vllm_inference)
