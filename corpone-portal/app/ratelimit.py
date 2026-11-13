"""In-process sliding-window rate limiter.

Per-process only: behind several instances, swap for a shared store (Redis / ElastiCache)
or enforce limits at Amazon API Gateway usage plans.
"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, window_seconds: float = 60.0):
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> tuple[bool, int, float]:
        """Record a hit. Returns (allowed, remaining, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= limit:
                return False, 0, max(0.0, q[0] + self.window - now)
            q.append(now)
            return True, limit - len(q), 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
