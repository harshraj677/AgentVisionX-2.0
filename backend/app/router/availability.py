"""In-memory runtime facts about models: are they currently failing, and how fast have they really been?

Nothing here is persisted or invented. Latency is the median of this process's own recent successful calls;
until enough samples exist it is reported as unknown.
"""
import statistics
import time
from collections import deque
from typing import Callable

ModelKey = tuple[str, str]  # (provider, model)

# Failures that say the MODEL/PROVIDER is unhealthy (as opposed to the request being bad).
PERMANENT_FAILURES = {"provider_auth_error", "model_not_found"}
TRANSIENT_FAILURES = {"provider_timeout", "provider_error", "rate_limited"}


class AvailabilityTracker:
    """Takes a model out of rotation for a cooldown after repeated failures (immediately for auth/model errors)."""

    def __init__(self, threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures: dict[ModelKey, int] = {}
        self._blocked_until: dict[ModelKey, float] = {}

    def is_available(self, key: ModelKey) -> bool:
        until = self._blocked_until.get(key)
        if until is None:
            return True
        if self._clock() >= until:  # cooldown over: allow a fresh attempt
            del self._blocked_until[key]
            self._failures.pop(key, None)
            return True
        return False

    def record_success(self, key: ModelKey) -> None:
        self._failures.pop(key, None)
        self._blocked_until.pop(key, None)

    def record_failure(self, key: ModelKey, code: str) -> None:
        if code not in PERMANENT_FAILURES | TRANSIENT_FAILURES:
            return  # e.g. bad request / blocked content: says nothing about model health
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if code in PERMANENT_FAILURES or count >= self._threshold:
            self._blocked_until[key] = self._clock() + self._cooldown


class LatencyStats:
    def __init__(self, window: int = 20):
        self._window = window
        self._samples: dict[ModelKey, deque[float]] = {}

    def record(self, key: ModelKey, latency_ms: float) -> None:
        self._samples.setdefault(key, deque(maxlen=self._window)).append(latency_ms)

    def median(self, key: ModelKey, min_samples: int) -> tuple[float | None, int]:
        samples = self._samples.get(key)
        n = len(samples) if samples else 0
        if n < min_samples:
            return None, n
        return statistics.median(samples), n  # type: ignore[arg-type]
