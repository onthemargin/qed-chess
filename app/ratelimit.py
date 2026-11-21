"""In-memory per-IP sliding window and a process-wide daily LLM cap."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone


class RateLimiter:
    def __init__(self, per_hour: int, now=time.monotonic):
        self.per_hour = per_hour
        self.now = now
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        t = self.now()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and t - dq[0] >= 3600:
                dq.popleft()
            if len(dq) >= self.per_hour:
                return False
            dq.append(t)
            if len(self._hits) > 10000:        # crude memory bound
                self._hits = {k: v for k, v in self._hits.items() if v and t - v[-1] < 3600}
            return True


class DailyCap:
    def __init__(self, limit: int, today=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")):
        self.limit = limit
        self.today = today
        self._day = None
        self._count = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            d = self.today()
            if d != self._day:
                self._day, self._count = d, 0
            if self._count >= self.limit:
                return False
            self._count += 1
            return True
