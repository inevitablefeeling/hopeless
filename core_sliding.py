"""Bounded sliding-window event counter."""
from __future__ import annotations

from collections import OrderedDict, deque
from typing import Deque, Hashable


class SlidingCounter:
    def __init__(self, max_keys: int = 5000) -> None:
        self._events: "OrderedDict[Hashable, Deque[float]]" = OrderedDict()
        self._max_keys = max_keys

    def add(self, key: Hashable, now: float, window: float) -> int:
        """Record one event and return how many fall inside the window (including this one)."""
        queue = self._events.get(key)
        if queue is None:
            queue = deque()
            self._events[key] = queue
        self._events.move_to_end(key)
        queue.append(now)
        cutoff = now - window
        while queue and queue[0] < cutoff:
            queue.popleft()
        while len(self._events) > self._max_keys:
            self._events.popitem(last=False)
        return len(queue)

    def count(self, key: Hashable, now: float, window: float) -> int:
        queue = self._events.get(key)
        if not queue:
            return 0
        cutoff = now - window
        return sum(1 for t in queue if t >= cutoff)

    def reset(self, key: Hashable) -> None:
        self._events.pop(key, None)
