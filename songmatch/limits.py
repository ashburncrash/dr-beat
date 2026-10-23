"""Abuse/quota guards for the public deployment (thread-safe, in-memory).

RateLimiter  — per-visitor request caps (per minute and per day).
DailyBudget  — global cap on LLM calls per UTC day, so strangers can't burn the whole
               free Gemini quota. When it runs out the app still answers from the catalogue.
"""
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_minute=6, per_day=60, clock=time.time):
        self.per_minute, self.per_day, self.clock = per_minute, per_day, clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= 86400:
                q.popleft()
            recent = sum(1 for t in q if now - t < 60)
            if recent >= self.per_minute or len(q) >= self.per_day:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # keep memory bounded
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 86400}
            return True


class DailyBudget:
    def __init__(self, limit, clock=time.time):
        self.limit, self.clock = limit, clock
        self.day, self.used = None, 0
        self._lock = threading.Lock()

    def take(self):
        day = int(self.clock() // 86400)
        with self._lock:
            if day != self.day:
                self.day, self.used = day, 0
            if self.used >= self.limit:
                return False
            self.used += 1
            return True

    def remaining(self):
        with self._lock:
            if self.day != int(self.clock() // 86400):
                return self.limit
            return max(0, self.limit - self.used)
