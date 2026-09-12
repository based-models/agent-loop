"""The canonical conversation types.

Every provider's wire format converts into these; nothing in here knows which
provider it came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.errors import TranscriptError

type Role = Literal["user", "assistant"]


class Part(BaseModel):
    """Base for every content part: immutable, and rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TextPart(Part):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(Part):
    """Reasoning emitted by the model.

    `text` is usually "" (providers omit the readable trace by default). The
    `signature` is the part that matters: it must be replayed byte-for-byte.
    """

    type: Literal["thinking"] = "thinking"
    text: str
    signature: str | None = None
    provider: str | None = None
    model: str | None = None
    raw: Any = None


class ToolCallPart(Part):
    """A tool call the model made.

    `arguments` is ALWAYS a parsed dict. When a provider sends a malformed JSON
    string, `parse_error` is set and `raw_arguments` keeps the original bytes.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]
    raw_arguments: str | None = None
    parse_error: str | None = None


class ToolResultPart(Part):
    """Our answer to one ToolCallPart. `call_id` must match that call's `id`."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    name: str
    content: str
    is_error: bool = False


type ContentPart = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: tuple[ContentPart, ...] = Field(min_length=1)


class Transcript(BaseModel):
    """The running conversation. Append-only — see Part 4."""

    messages: list[Message] = Field(default_factory=list)

    # --- the only three ways to change it ---

    def add_user_text(self, text: str) -> None:
        self.messages.append(Message(role="user", content=(TextPart(text=text),)))

    def add_assistant(self, parts: Sequence[ContentPart]) -> None:
        self.messages.append(Message(role="assistant", content=tuple(parts)))

    def add_tool_results(self, results: Sequence[ToolResultPart]) -> None:
        """Append results for the pending calls. Raises if they don't match."""
        pending = self.pending_tool_calls()
        want = {call.id for call in pending}
        got = {result.call_id for result in results}
        if want != got:
            raise TranscriptError(
                f"tool result mismatch: calls={sorted(want)} results={sorted(got)} "
                f"(missing={sorted(want - got)} unexpected={sorted(got - want)})"
            )
        order = {call.id: index for index, call in enumerate(pending)}
        ordered = tuple(sorted(results, key=lambda result: order[result.call_id]))
        self.messages.append(Message(role="user", content=ordered))

    # --- queries ---

    def pending_tool_calls(self) -> tuple[ToolCallPart, ...]:
        """Tool calls in the last assistant message; () if there are none."""
        if not self.messages:
            return ()
        last = self.messages[-1]
        if last.role != "assistant":
            return ()
        return tuple(p for p in last.content if isinstance(p, ToolCallPart))

    def check_invariants(self) -> None:
        """Raise TranscriptError if this transcript is not safe to send."""
        for index, message in enumerate(self.messages):
            if message.role != "assistant":
                continue
            calls = [p for p in message.content if isinstance(p, ToolCallPart)]
            if not calls:
                continue

            ids = [call.id for call in calls]
            if len(set(ids)) != len(ids):
                raise TranscriptError(
                    f"message {index} has duplicate tool_call ids: {ids}"
                )

            if index + 1 >= len(self.messages):
                raise TranscriptError(
                    f"message {index} ends with unanswered tool calls {ids}; "
                    "the transcript is not sendable"
                )
            answered = {
                p.call_id
                for p in self.messages[index + 1].content
                if isinstance(p, ToolResultPart)
            }
            if answered != set(ids):
                raise TranscriptError(
                    f"message {index} calls {sorted(set(ids))} "
                    f"but message {index + 1} answers {sorted(answered)}"
                )


def append_jsonl(path: Path, messages: Sequence[Message]) -> None:
    """Append messages to a JSONL log, one per line. See Part 5."""
    with path.open("a") as handle:
        for message in messages:
            handle.write(message.model_dump_json() + "\n")


def load_jsonl(path: Path) -> Transcript:
    """Read a JSONL log back into a Transcript. See Part 5."""
    return Transcript(
        messages=[
            Message.model_validate_json(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]
    )
