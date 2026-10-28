"""Deleted/edited message memory. In RAM only, short-lived and bounded - never written to disk."""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from typing import Callable, Deque, Optional

MAX_CONTENT = 1500
DEFAULT_TTL = 3600.0  # one hour
DEFAULT_PER_CHANNEL = 10
DEFAULT_MAX_CHANNELS = 500


@dataclass(frozen=True)
class SnipedMessage:
    author_id: int
    author_name: str
    content: str
    attachment_names: tuple[str, ...] = ()
    after_content: Optional[str] = None  # only set for edits
    created_ts: float = 0.0  # unix time the original message was sent


class SnipeService:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL,
        per_channel: int = DEFAULT_PER_CHANNEL,
        max_channels: int = DEFAULT_MAX_CHANNELS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._per_channel = per_channel
        self._max_channels = max_channels
        self._clock = clock
        self._deleted: OrderedDict[int, Deque[tuple[float, SnipedMessage]]] = OrderedDict()
        self._edited: OrderedDict[int, Deque[tuple[float, SnipedMessage]]] = OrderedDict()

    # --- recording -------------------------------------------------------------------------
    def record_delete(self, channel_id: int, message: SnipedMessage) -> None:
        self._record(self._deleted, channel_id, message)

    def record_edit(self, channel_id: int, message: SnipedMessage) -> None:
        self._record(self._edited, channel_id, message)

    def _record(self, store, channel_id: int, message: SnipedMessage) -> None:
        message = replace(
            message,
            content=message.content[:MAX_CONTENT],
            after_content=None if message.after_content is None else message.after_content[:MAX_CONTENT],
        )
        queue = store.get(channel_id)
        if queue is None:
            queue = deque(maxlen=self._per_channel)
            store[channel_id] = queue
        store.move_to_end(channel_id)
        queue.appendleft((self._clock(), message))
        while len(store) > self._max_channels:
            store.popitem(last=False)

    # --- reading ---------------------------------------------------------------------------
    def get_deleted(self, channel_id: int, index: int = 1) -> Optional[SnipedMessage]:
        return self._get(self._deleted, channel_id, index)

    def get_edited(self, channel_id: int, index: int = 1) -> Optional[SnipedMessage]:
        return self._get(self._edited, channel_id, index)

    def _get(self, store, channel_id: int, index: int) -> Optional[SnipedMessage]:
        queue = store.get(channel_id)
        if not queue:
            return None
        self._expire(queue)
        if not queue:
            store.pop(channel_id, None)
            return None
        if index < 1 or index > len(queue):
            return None
        return queue[index - 1][1]

    # --- housekeeping ----------------------------------------------------------------------
    def _expire(self, queue: Deque[tuple[float, SnipedMessage]]) -> int:
        removed = 0
        cutoff = self._clock() - self._ttl
        while queue and queue[-1][0] < cutoff:  # oldest entries sit on the right
            queue.pop()
            removed += 1
        return removed

    def clear(self, channel_id: int) -> int:
        count = 0
        for store in (self._deleted, self._edited):
            queue = store.pop(channel_id, None)
            if queue:
                count += len(queue)
        return count

    def prune(self) -> int:
        removed = 0
        for store in (self._deleted, self._edited):
            for channel_id in list(store):
                removed += self._expire(store[channel_id])
                if not store[channel_id]:
                    del store[channel_id]
        return removed
