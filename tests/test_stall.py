"""Stall detection counts repeats; it never keeps or exposes what was repeated."""

from agentloop.stall import RepeatTracker
from agentloop.transcript import ToolCallPart


def call(call_id: str, tool: str = "search", **arguments: object) -> ToolCallPart:
    return ToolCallPart(id=call_id, tool_name=tool, arguments=dict(arguments))


def test_identical_calls_are_counted_and_distinct_ones_are_not() -> None:
    tracker = RepeatTracker()
    tracker.observe([call("1", q="a"), call("2", q="b"), call("3", q="a")])
    assert tracker.max_repeats == 2


def test_argument_order_does_not_make_two_calls_different() -> None:
    tracker = RepeatTracker()
    tracker.observe([call("1", a=1, b=2), call("2", b=2, a=1)])
    assert tracker.max_repeats == 2


def test_the_same_arguments_to_a_different_tool_are_a_different_call() -> None:
    tracker = RepeatTracker()
    tracker.observe([call("1", "search", q="a"), call("2", "fetch", q="a")])
    assert tracker.max_repeats == 1


def test_a_threshold_reports_each_distinct_call_once() -> None:
    tracker = RepeatTracker(threshold=3)
    assert tracker.observe([call("1", q="a"), call("2", q="a")]) == []
    assert tracker.observe([call("3", q="a")]) == [("search", 3)]
    assert tracker.observe([call("4", q="a")]) == []  # already reported
    assert tracker.stalled
    assert tracker.max_repeats == 4
    assert tracker.observe([call("5", q="b")] * 3) == [("search", 3)]  # a second one


def test_without_a_threshold_nothing_is_reported_but_repeats_are_still_counted() -> None:
    tracker = RepeatTracker()
    assert tracker.observe([call(str(i), q="a") for i in range(5)]) == []
    assert not tracker.stalled
    assert tracker.max_repeats == 5


def test_unparseable_calls_compare_by_their_raw_text() -> None:
    bad = ToolCallPart(
        id="1", tool_name="search", arguments={}, raw_arguments="{oops", parse_error="x"
    )
    same = bad.model_copy(update={"id": "2"})
    other = bad.model_copy(update={"id": "3", "raw_arguments": "{different"})
    tracker = RepeatTracker()
    tracker.observe([bad, same, other])
    assert tracker.max_repeats == 2


def test_nothing_about_the_calls_is_exposed() -> None:
    tracker = RepeatTracker(threshold=2)
    tracker.observe([call("1", q="hunter2"), call("2", q="hunter2")])
    assert "hunter2" not in repr(vars(tracker))
