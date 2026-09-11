# 00 — Architecture

Read this once before writing anything. It is the map; everything after is the territory.

---

## Where we start: the notebook loop

This is the whole agent, from Anthropic's tool-use course, lightly condensed:

```python
def run_conversation(messages):
    while True:
        response = chat(messages, tools=[get_current_datetime_schema])
        add_assistant_message(messages, response)
        if response.stop_reason != "tool_use":
            break
        tool_results = run_tools(response)
        add_user_message(messages, tool_results)
    return messages
```

That's it. That is a working agent, and it is worth sitting with how little there is to it. **Reason → act → observe → repeat, until the model stops asking for tools.** Everything else people build around agents is harness.

Supporting it:

```python
def run_tools(message):
    tool_requests = [b for b in message.content if b.type == "tool_use"]
    tool_result_blocks = []
    for tool_request in tool_requests:
        try:
            tool_output = run_tool(tool_request.name, tool_request.input)
            block = {"type": "tool_result", "tool_use_id": tool_request.id,
                     "content": json.dumps(tool_output), "is_error": False}
        except Exception as e:
            block = {"type": "tool_result", "tool_use_id": tool_request.id,
                     "content": f"Error: {e}", "is_error": True}
        tool_result_blocks.append(block)
    return tool_result_blocks
```

Three things here are already right, and we keep all three:

- **Errors become tool results, not exceptions.** The `except` branch produces a `tool_result` with `is_error: True` instead of propagating. Chapter 6 explains why this is the single most important line in the function.
- **All results go back in one user message.** `add_user_message(messages, tool_results)` — one message, N blocks.
- **The loop terminates on stop reason, not on content.** It doesn't sniff the text for "I'm done."

And three things will not survive contact with production:

| | Problem | Fixed in |
|---|---|---|
| `stop_reason != "tool_use"` | Only two outcomes are considered. Real responses also stop for `max_tokens`, `refusal`, `model_context_window_exceeded`, and on OpenAI-compatible servers sometimes `null` | ch. 3, 7 |
| `add_assistant_message` unconditional | A turn truncated mid-tool-call gets appended with half-written arguments, poisoning history permanently | ch. 7 (D8) |
| `temperature=1.0` | Returns a **400** on Claude Opus 5, Sonnet 5, Opus 4.8/4.7 and the Fable line — those parameters were removed | ch. 3, 4 (D9) |

And of course: it only speaks Anthropic. Making that not true is most of the work.

We also build this loop by hand rather than on an agent framework (**D1**), and swap the reference course's Gemini provider for Anthropic (**D3**) — which makes the seam harder in the useful direction, since Anthropic is the one provider that isn't OpenAI-shaped.

---

## The central claim

> **Two of your three providers speak the same wire protocol, so you write two adapters, not three. Anthropic's message shape carries strictly more information than OpenAI's, so the canonical transcript must be Anthropic-shaped — but the types must be yours. The seam is one pure function. Everything hard happens below it; the loop above it never names a vendor.**

Unpacking that in four parts, because each drives a chapter.

### Two adapters, not three

OpenRouter is an OpenAI-compatible gateway. Modal, in the vLLM deployment we use, serves an OpenAI-compatible `/v1`. Same JSON, same `tool_calls` array, same `finish_reason` vocabulary. They differ in base URL, auth headers, and about six behavioral quirks — not in protocol.

Anthropic is the genuinely different one, and it is different in the *instructive* direction: its shape is a superset.

### Anthropic-shaped, because OpenAI's shape is lossy

An Anthropic assistant turn is an ordered list of content blocks:

```json
{"role": "assistant", "content": [
  {"type": "text", "text": "Let me check both files."},
  {"type": "tool_use", "id": "toolu_01A", "name": "read", "input": {"path": "a.py"}},
  {"type": "tool_use", "id": "toolu_01B", "name": "read", "input": {"path": "b.py"}}
]}
```

An OpenAI assistant turn is a string plus a sibling array:

```json
{"role": "assistant", "content": "Let me check both files.",
 "tool_calls": [
   {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{\"path\":\"a.py\"}"}}
 ]}
```

The ordering of text *relative to* tool calls is unrepresentable in the second form. So is a turn that goes text → tool call → text. So is a reasoning block with a cryptographic signature that has to be replayed verbatim.

Converting Anthropic→OpenAI loses information you can reconstruct a reasonable approximation of. Converting OpenAI→Anthropic loses nothing, because there was less there. **Canonicalize on the richer shape**, and the lossy conversion happens once, at the edge, where you can see it.

### But the types must be *yours*

The tempting shortcut is to use `anthropic.types.Message` as the canonical type and make the Anthropic adapter the identity function. Don't:

- `anthropic.types.Message` is a Pydantic model with required `id`, `type`, `role`, `model`, `content`, `stop_reason`, `stop_sequence`, `usage`. Every fake response in every test becomes twelve lines of ceremony — which alone kills the "testable with a ten-line fake" property we're building toward.
- It makes `anthropic` a hard dependency for someone who only wants Modal. Chapter 1 makes it an optional extra; that only works if core never touches it.
- SDK types drift across versions, and your persisted transcripts and golden tests drift with them.
- New block types keep appearing in the content union. Owning your types means a new one breaks exactly one `match` in one adapter, not code that never meant to be vendor-aware.

### One pure function

```python
class LLMClient(Protocol):
    name: str
    model: str
    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

One method. In, a normalized request. Out, a normalized response. No hidden state.

Being *pure* is not aesthetics — it's what makes blind retry safe in chapter 8. Being *one method* is what makes the fake a ten-line class in chapter 11.

---

## Module map

```
src/harness/
  errors.py          the exception hierarchy       ch. 2, 3   [stdlib only]
  transcript.py      message types + invariants    ch. 2      [pydantic]
  provider.py        LLMClient, ModelRequest/Response  ch. 3  [pydantic]
  observer.py        LoopObserver + NullObserver   ch. 6      [pydantic]
  tools.py           ToolSpec, registry, run_tools ch. 6      [pydantic]
  loop.py            run_conversation              ch. 7      [stdlib only]
  retry.py           RetryingClient + RetryPolicy  ch. 8      [pydantic]
  config.py          Settings + build_client()   (lazy imports)
  tracing.py         OTelObserver               [opentelemetry-api]
  cli.py             console entry point
  py.typed
  providers/
    anthropic.py        the native adapter          ch. 4      [anthropic]
    _openai_compat.py   shared adapter + Quirks     ch. 5      [httpx]
    openrouter.py       base_url + Bearer + attribution  ch. 5
    modal.py            base_url + Modal headers + long timeouts  ch. 5
    fake.py             scripted responses          ch. 11     [0 deps]
```

The chapter column is the build order — each module is complete before anything imports
it, so you can run the tests at every step.

The dependency annotations are the architecture, stated as a constraint. Nothing above
the `providers/` line imports a provider SDK — pydantic is fine, `anthropic` is not.
Chapter 11 turns that from a comment into a **failing test**: a dozen lines of `ast` that
walk every module in `harness/` outside `providers/` and assert none of them import
`anthropic`, `openai`, `httpx`, or `requests`.

The line is drawn at **"knows about a specific LLM provider"**, not at "has a
dependency". Pydantic models your data and has no opinion about Anthropic or vLLM; the
provider SDKs are the entire point of the adapters and must not leak past them.

That test is the most valuable thing in the repo, because it is the only thing standing between you and a slow leak of vendor concepts upward into the loop. And because `anthropic` is an optional extra, it's also what keeps a Modal-only install from crashing on import.

---

## Reading order, and what runs when

```
02 transcript ──▶ 03 provider seam ──┬──▶ 04 anthropic
                                     └──▶ 05 openai-compatible
                      06 tools ──▶ 07 loop ──▶ 08 resilience
                                                    │
                                     09 wire up ────┴──▶ it runs
                                          │
                                     10 tracing    11 testing    12 next
```

**Nothing runs end-to-end until chapter 9.** That is deliberate, and it is the part most likely to feel wrong while you're in it.

You are building a seam. A seam designed *after* something has already crossed it isn't a seam, it's a wrapper around whatever leaked through first. So chapters 2 through 8 are all contract, verified by tests against a fake, and the first real HTTP request happens only once the shape is settled.

You do get a checkpoint every chapter — it just runs `pytest`, not the agent.

---

## Scope

**In:** the transcript, the provider seam, two adapters, four tools (`read`, `write`, `edit`, `bash`), the loop, error handling and retries, config and a CLI, tracing, and tests.

**Out** (**D12**): TUI, permission framework, sandboxing, context compaction, subagents, LSP, skills, memory, streaming, async.

One exception: a `before_tool` approval hook, defaulting to allow-all. Four lines, and shipping a `bash` tool with no gate at all is not a habit worth forming.

"Out of scope" is doing real work in that list, so it's worth being precise about what it means. Two of those items would be *expensive* to retrofit, so v1 is shaped for them even though it doesn't build them:

- **Streaming** — `complete_stream()` is reserved as a sibling of `complete()`, and `on_text_delta` is reserved on the observer. Note it's a *sync* generator; streaming does not require async.
- **Mid-turn steering** — the reason the reference architecture has its whole `Boundary`-and-dual-queue apparatus. Chapter 7 marks the single place a steering message would fold in.

Everything else on the out list attaches to a seam that already exists by chapter 9 — the `before_tool` hook, the tool handlers, the would-stop point, the observer. Chapter 12 walks through each.

---

**Checkpoint:** none — you haven't written anything yet. On to [01-scaffold.md](01-scaffold.md).
