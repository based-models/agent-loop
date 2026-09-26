# Backlog

Work that was deliberately deferred, with enough context to pick it up cold.

## OpenRouter reasoning replay

**Status:** not implemented. The `Quirks.replays_reasoning` flag was removed from
`src/agentloop/providers/_openai_compat.py` because nothing read it. OpenRouter's builder
set it to `True`, but the shared adapter never parsed reasoning or sent it back, so the
setting was misleading.

**What was promised:** `tutorial/05-openai-compatible.md`, in the `replays_reasoning`
section, says OpenRouter replays reasoning, but only when `ThinkingPart.provider` and
`.model` are unchanged, and that Modal does not. `tutorial/DIVERGENCES.md` and the
chapter 5 code listings still show the flag. Update them when this lands, or drop the
promise.

**What implementing it takes:**

1. **Parse.** In `_openai_compat._normalize`, read `message["reasoning"]` (text) and
   `message["reasoning_details"]` (a list of blocks) into `ThinkingPart`s. Set `provider`
   and `model`, and keep the original block verbatim in `raw`. The parts must come before
   the text and tool calls, in the order received.
2. **Replay.** In `message_to_wire`, for an assistant message, re-emit `reasoning_details`
   from the `raw` blocks, byte for byte, in their original order. OpenRouter's docs forbid
   reordering or modifying them. Replay only when the part's `provider` and `model` match
   the current request's. Otherwise drop the part, because a different model cannot use it.
3. **Gate.** Reintroduce a quirk flag, so Modal/vLLM never replays: vLLM's
   `reasoning_content` is generally not replayable, and most chat templates discard it.
   The OpenRouter builder turns the flag on.
4. **Usage.** `completion_tokens_details.reasoning_tokens` is already mapped. Nothing to do.
5. **Tests.**
   - A body with `reasoning_details` round-trips through
     `response_from_wire` → `message_to_wire` unchanged.
   - A part from a different model is dropped on replay.
   - Modal quirks never emit reasoning.

**Why it was deferred:** the `src/agentloop` review (September 2026) fixed defects first.
This is a feature, not a fix.
