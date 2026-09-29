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


class Source(BaseModel):
    """A page a provider-hosted tool consulted. Readable metadata only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    title: str | None = None


class Citation(BaseModel):
    """Evidence the model attached to its own text.

    Anthropic quotes the source (`cited_text`); OpenAI points at a span of the
    text instead (`start`/`end`). Each fills what its API returns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    title: str | None = None
    cited_text: str | None = None
    start: int | None = None
    end: int | None = None


class TextPart(Part):
    """
    A text part, which is just a string.
    """

    type: Literal["text"] = "text"
    text: str = Field(..., description="The text content of this part.")
    citations: tuple[Citation, ...] = ()


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


class HostedToolPart(Part):
    """A tool the PROVIDER ran during this turn: a web search, say.

    A readable summary for observers and applications. It is never dispatched
    and never paired with a result. The bytes a provider needs to continue the
    conversation (encrypted results, citation indices) live in `Message.origin`,
    not here.
    """

    type: Literal["hosted_tool"] = "hosted_tool"
    kind: str = Field(min_length=1)
    call_id: str | None = None
    query: str | None = None
    sources: tuple[Source, ...] = ()
    error: str | None = None


# tells pydantic to go to the matching class vs try them all
type ContentPart = Annotated[
    TextPart | ToolCallPart | ToolResultPart | ThinkingPart | HostedToolPart,
    Field(discriminator="type"),
]


class Origin(BaseModel):
    """Where an assistant message came from, and its exact wire form.

    The canonical parts are a portable VIEW of the turn. Some providers need the
    turn back byte for byte -- Anthropic rejects altered search results, OpenAI
    needs reasoning items and their order -- so the adapter that produced it
    keeps its own blocks here. An adapter whose `(provider, api)` matches replays
    `raw`; any other adapter builds from the parts and drops what cannot move.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    api: str
    model: str
    raw: tuple[dict[str, Any], ...]

    def matches(self, provider: str | None, api: str) -> bool:
        return provider is not None and (self.provider, self.api) == (provider, api)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Role = Field(..., description="The role of the message sender.")
    content: tuple[ContentPart, ...] = Field(
        min_length=1, description="The content of the message, as a tuple of parts."
    )
    origin: Origin | None = None


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

    def add_assistant_message(
        self, parts: Sequence[ContentPart], origin: Origin | None = None
    ) -> None:
        """
        Add an assistant message to the transcript.
        """

        self.messages.append(Message(role="assistant", content=tuple(parts), origin=origin))

    def extend_last_assistant(
        self, parts: Sequence[ContentPart], origin: Origin | None = None
    ) -> None:
        """Continue the last assistant message: a turn the provider paused.

        One logical turn stays one message, so its wire form (`origin.raw`) is
        the paused blocks followed by the continuation, in order.
        """
        if not self.messages or self.messages[-1].role != "assistant":
            raise TranscriptError("no assistant message to continue")
        last = self.messages[-1]
        merged: Origin | None = None
        if last.origin is not None and origin is not None and last.origin.matches(
            origin.provider, origin.api
        ):
            merged = last.origin.model_copy(update={"raw": last.origin.raw + origin.raw})
        self.messages[-1] = Message(
            role="assistant", content=last.content + tuple(parts), origin=merged
        )

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

            if message.role != "assistant" and any(
                isinstance(p, HostedToolPart) for p in message.content
            ):
                raise TranscriptError(
                    f"message {index} is from the {message.role} but carries a "
                    "provider-hosted tool call"
                )

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

def join_text(parts: Iterable[ContentPart]) -> str:
    """The text parts of a message, joined into one readable string.

    A space goes between two parts only where the seam needs one. Separate
    sentences ("I'll search." then "The answer is...") get one. Anthropic's
    citation splits break mid-sentence and keep their own spacing ("Based on the
    results, " + "Shannon was..."), or end right before punctuation ("born in
    1916" + "."), so a seam that already has whitespace, or that meets closing
    punctuation or an opening bracket, is joined as-is.
    """
    joined = ""
    for part in parts:
        if not isinstance(part, TextPart) or not part.text:
            continue
        if joined and _needs_space(joined[-1], part.text[0]):
            joined += " "
        joined += part.text
    return joined


def _needs_space(before: str, after: str) -> bool:
    return not (
        before.isspace()
        or after.isspace()
        or after in _NO_SPACE_BEFORE
        or before in _NO_SPACE_AFTER
    )


_NO_SPACE_BEFORE = frozenset(".,;:!?)]}%\u2019\u201d")
_NO_SPACE_AFTER = frozenset("([{\u2018\u201c")


def final_text(transcript: Transcript) -> str:
    """The text of the last assistant message. Empty if it said nothing."""
    for message in reversed(transcript.messages):
        if message.role == "assistant":
            return join_text(message.content)
    return ""

