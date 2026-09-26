"""The canonical conversation types.

Every provider's wire format converts into these; nothing in here knows which
provider it came from.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentloop.errors import TranscriptError

type Role = Literal["user", "assistant"]


class Part(BaseModel):
    """
    Base for every content part: immutable,
    and rejects unknown fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class TextPart(Part):
    """
    A text part, which is just a string.
    """

    type: Literal["text"] = "text"
    text: str = Field(..., description="The text content of this part.")


class ToolCallPart(Part):
    """
    A tool call part, which is a dict with a tool name and arguments.
    `arguments` is ALWAYS a parsed dict. When a provider sends a malformed JSON
    string, `parse_error` is set and `raw_arguments` keeps the original bytes.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1, description="The ID of the tool call.")
    tool_name: str = Field(min_length=1, description="The name of the tool to call.")
    arguments: dict[str, Any] = Field(description="The arguments for the tool call.")
    # ALWAYS parsed - never a JSON string
    raw_arguments: str | None = None
    parse_error: str | None = None


class ToolResultPart(Part):
    """
    nswer to one ToolCallPart. `call_id` must match that call's `id`.
    """

    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(
        min_length=1, description="The ID of the tool call that produced this result."
    )
    tool_name: str = Field(min_length=1, description="The name of the tool that was called.")
    content: str = Field(description="The result content of the tool call.")
    is_error: bool = Field(default=False, description="Whether the tool call resulted in an error.")


class ThinkingPart(Part):
    """
    Reasoning emitted by the model.

    `text` is usually "" (providers omit the readable trace by default). The
    `signature` is the part that matters: it must be replayed byte-for-byte.
    """

    type: Literal["thinking"] = "thinking"
    text: str = Field(
        description="The thought content of this part."
    )  # often empty if there's no reasoning
    signature: str | None = None
    provider: str | None = None
    model: str | None = None
    raw: Any | None = Field(default=None, description="The whole original block, verbatim.")


# tells pydantic to go to the matching class vs try all four
type ContentPart = Annotated[
    TextPart | ToolCallPart | ToolResultPart | ThinkingPart, Field(discriminator="type")
]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Role = Field(..., description="The role of the message sender.")
    content: tuple[ContentPart, ...] = Field(
        min_length=1, description="The content of the message, as a tuple of parts."
    )


class Transcript(BaseModel):
    """
    The running conversation.
    """

    messages: list[Message] = Field(
        default_factory=list, description="The list of messages in the transcript."
    )

    def add_user_message(self, text: str) -> None:
        """
        Add a user message to the transcript.
        """

        self.messages.append(Message(role="user", content=(TextPart(text=text),)))

    def add_assistant_message(self, parts: Sequence[ContentPart]) -> None:
        """
        Add an assistant message to the transcript.
        """

        self.messages.append(Message(role="assistant", content=tuple(parts)))

    def add_tool_results(self, results: Sequence[ToolResultPart]) -> None:
        """
        Append results for the pending calls.
        Raises if they don't match.
        """

        pending = self.pending_tool_calls()
        if not pending:
            raise TranscriptError("no pending tool calls to answer")

        got_ids = [result.call_id for result in results]
        duplicates = sorted(i for i, n in Counter(got_ids).items() if n > 1)
        if duplicates:
            raise TranscriptError(f"more than one result for tool calls {duplicates}")

        want = {call.id for call in pending}
        got = set(got_ids)
        if want != got:
            raise TranscriptError(
                f"tool result mismatch: calls={sorted(want)} results={sorted(got)}"
                f"(missing={sorted(want - got)} unexpected={sorted(got - want)})"
            )
        order = {call.id: index for index, call in enumerate(pending)}
        ordered = tuple(sorted(results, key=lambda result: order[result.call_id]))

        self.messages.append(Message(role="user", content=ordered))

    # --- queries ---

    def pending_tool_calls(self) -> tuple[ToolCallPart, ...]:
        """
        Tool calls in the last assistant message;
        () if there are none.
        """

        if not self.messages:
            return ()

        last = self.messages[-1]
        if last.role != "assistant":
            return ()
        # if the last message is an assistant message, return all ToolCallParts in it
        return tuple(part for part in last.content if isinstance(part, ToolCallPart))

    def check_invariants(self) -> None:
        """
        Raise TranscriptError if this transcript
        is not safe to send."""

        # The ids the previous message called, or None if it called nothing.
        # Results are legal ONLY in the message right after a call.
        awaiting: list[str] | None = None

        for index, message in enumerate(self.messages):
            results = [p.call_id for p in message.content if isinstance(p, ToolResultPart)]

            if awaiting is not None:
                if message.role != "user":
                    raise TranscriptError(
                        f"message {index - 1} calls {sorted(awaiting)} but message "
                        f"{index} is from the {message.role}, not the tool results"
                    )
                if Counter(results) != Counter(awaiting):
                    raise TranscriptError(
                        f"message {index - 1} calls {sorted(awaiting)} "
                        f"but message {index} answers {sorted(results)}"
                    )
            elif results:
                # Also covers an assistant message carrying results: nothing it
                # could be answering precedes it.
                raise TranscriptError(
                    f"message {index} answers {sorted(results)}, but does not directly "
                    "follow a message that made tool calls"
                )

            ids = [p.id for p in message.content if isinstance(p, ToolCallPart)]
            if len(set(ids)) != len(ids):
                raise TranscriptError(f"message {index} has duplicate tool_call ids: {ids}")
            awaiting = ids if message.role == "assistant" and ids else None

        if awaiting is not None:
            raise TranscriptError(
                f"message {len(self.messages) - 1} ends with unanswered tool calls "
                f"{awaiting}; the transcript is not sendable"
            )


def to_jsonl(messages: Sequence[Message]) -> str:
    """Render messages as JSONL: one message per line, trailing newline.

    Returns a string rather than writing a file. The caller owns the file, which
    keeps this module free of encoding, newline, fsync and concurrency policy it
    has no standing to set -- and keeps the library free of I/O entirely.
    """
    return "".join(message.model_dump_json() + "\n" for message in messages)


def from_jsonl(lines: Iterable[str]) -> Transcript:
    """Parse JSONL lines back into a Transcript. Blank lines are skipped.

    Takes any iterable of lines: `splitlines()`, an open file, a generator.
    Deliberately strict -- a truncated final line raises rather than being
    dropped, because silently losing the last turn of a crashed run is worse
    than being told the log is damaged.

    Does NOT call check_invariants(): a log captured mid-turn is a legitimate
    thing to load. Checking is the caller's decision, at the moment they send.
    """
    return Transcript(
        messages=[
            Message.model_validate_json(line) for line in lines if line.strip()
        ]
    )

def final_text(transcript: Transcript) -> str:
    """The text of the last assistant message. Empty if it said nothing."""
    for message in reversed(transcript.messages):
        if message.role == "assistant":
            return "".join(
                part.text for part in message.content if isinstance(part, TextPart)
            )
    return ""

