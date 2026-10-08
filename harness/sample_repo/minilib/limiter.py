"""A token-bucket rate limiter with an injectable clock."""
import math
import time

from .errors import LimitExceeded

HISTORY_LIMIT = 8


class RateLimiter:
    """Token bucket holding at most `capacity` tokens, refilled at `rate` tokens per second.

    Every method that looks at the clock accepts an explicit `now` (seconds).
    """

    def __init__(self, capacity, rate=1.0, clock=None, start_full=True):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if rate < 0:
            raise ValueError("rate must not be negative")
        self.capacity = capacity
        self.rate = rate
        self._clock = clock or time.monotonic
        self.tokens = float(capacity) if start_full else 0.0
        self._last = None
        self.granted = 0
        self.denied = 0
        self.history = []

    def _refill(self, now):
        if self._last is None:
            self._last = now
            return
        elapsed = now - self._last
        if elapsed > 0:
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
            self._last = now

    def _note(self, outcome, n):
        self.history.append((outcome, n))
        if len(self.history) > HISTORY_LIMIT:
            del self.history[:-HISTORY_LIMIT]

    def consume(self, n=1, now=None):
        """Take `n` tokens if available; return whether they were granted."""
        if n > self.capacity:
            raise LimitExceeded(n, self.capacity)
        if n <= 0:
            raise ValueError("n must be positive")
        now = self._clock() if now is None else now
        self._refill(now)
        if self.tokens >= n:
            self.tokens -= n
            self.granted += n
            self._note("ok", n)
            return True
        self.denied += 1
        self._note("denied", n)
        return False

    def wait_time(self, n=1, now=None):
        """Seconds until `n` tokens are available (0.0 when they already are)."""
        if n > self.capacity:
            raise LimitExceeded(n, self.capacity)
        now = self._clock() if now is None else now
        self._refill(now)
        missing = n - self.tokens
        if missing <= 0:
            return 0.0
        if self.rate == 0:
            return math.inf
        return round(missing / self.rate, 6)

    def reset(self, full=True):
        """Forget the history; refill the bucket unless `full` is false."""
        self.tokens = float(self.capacity) if full else 0.0
        self._last = None
        self.granted = self.denied = 0
        self.history = []

    def stats(self):
        """Counters as a plain dict."""
        total = self.granted + self.denied
        return {
            "capacity": self.capacity,
            "tokens": round(self.tokens, 3),
            "granted": self.granted,
            "denied": self.denied,
            "deny_ratio": round(self.denied / total, 3) if total else 0.0,
        }

    def __len__(self):
        return int(self.tokens)

    def __repr__(self):
        return "RateLimiter(capacity=%r, rate=%r, tokens=%.2f)" % (self.capacity, self.rate, self.tokens)
