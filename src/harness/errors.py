"""Every exception this library raises."""


class HarnessError(Exception):
    """Base for everything this library raises."""


class TranscriptError(HarnessError):
    """The transcript is in a state no provider will accept."""
