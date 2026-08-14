"""Priority-aware serialization for turns that share conversation history."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(order=True)
class _Waiter:
    priority: int
    sequence: int
    future: asyncio.Future[None] = field(compare=False)


class SessionTurnCoordinator:
    """Serialize turns per session while favoring lower numeric priorities."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._waiters: dict[str, list[_Waiter]] = {}
        self._sequence = itertools.count()

    def _promote(self, session_key: str) -> None:
        if session_key in self._active:
            return

        waiters = self._waiters.get(session_key, [])
        while waiters:
            waiter = heapq.heappop(waiters)
            if waiter.future.cancelled():
                continue
            self._active.add(session_key)
            waiter.future.set_result(None)
            break

        if not waiters:
            self._waiters.pop(session_key, None)

    async def acquire(self, session_key: str, priority: int) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        heapq.heappush(
            self._waiters.setdefault(session_key, []),
            _Waiter(priority, next(self._sequence), future),
        )
        self._promote(session_key)

        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                self.release(session_key)
            else:
                future.cancel()
            raise

    def release(self, session_key: str) -> None:
        if session_key not in self._active:
            raise RuntimeError(f"session turn is not active: {session_key}")
        self._active.remove(session_key)
        self._promote(session_key)

    @asynccontextmanager
    async def turn(self, session_key: str, priority: int):
        await self.acquire(session_key, priority)
        try:
            yield
        finally:
            self.release(session_key)
