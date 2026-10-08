"""Exception classes raised by minilib."""


class MinilibError(Exception):
    """Base class of every error raised on purpose by minilib."""


class ParseError(MinilibError, ValueError):
    """A duration or size string could not be parsed."""

    def __init__(self, text, reason="unrecognised format"):
        super().__init__(text, reason)
        self.text = text
        self.reason = reason

    def __str__(self):
        return "cannot parse %r: %s" % (self.text, self.reason)


class LimitExceeded(MinilibError):
    """A request can never be satisfied because it exceeds the limiter capacity."""

    def __init__(self, requested, capacity):
        super().__init__("requested %s tokens but the capacity is %s" % (requested, capacity))
        self.requested = requested
        self.capacity = capacity
