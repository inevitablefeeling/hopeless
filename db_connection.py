"""asyncpg connection pool. Connection problems become DatabaseUnavailable (safe to show to users)."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import asyncpg

from core_errors import DatabaseUnavailable

log = logging.getLogger("hopeless.db")

_TRANSIENT = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    OSError,
    asyncio.TimeoutError,
)


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10, command_timeout: float = 30.0) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._command_timeout = command_timeout
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self, *, attempts: int = 5, base_delay: float = 1.0) -> None:
        """Create the pool, retrying with exponential backoff (Railway databases can start slower than the bot)."""
        if self._pool is not None:
            return
        for attempt in range(1, attempts + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    command_timeout=self._command_timeout,
                )
                log.info("database pool ready")
                return
            except _TRANSIENT as exc:
                if attempt == attempts:
                    raise DatabaseUnavailable() from exc
                delay = base_delay * 2 ** (attempt - 1)
                log.warning("database connect attempt %s/%s failed (%s); retrying in %.0fs", attempt, attempts, type(exc).__name__, delay)
                await asyncio.sleep(delay)

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise DatabaseUnavailable()
        return self._pool

    async def _call(self, method: str, *args: Any) -> Any:
        try:
            return await getattr(self.pool, method)(*args)
        except _TRANSIENT as exc:
            log.error("database %s failed: %s", method, type(exc).__name__, exc_info=True)
            raise DatabaseUnavailable() from exc

    async def execute(self, query: str, *args: Any) -> str:
        return await self._call("execute", query, *args)

    async def executemany(self, query: str, args: list[tuple]) -> None:
        await self._call("executemany", query, args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        return await self._call("fetch", query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Optional[asyncpg.Record]:
        return await self._call("fetchrow", query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self._call("fetchval", query, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """A connection inside a transaction: commits on success, rolls back on any exception."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    yield conn
        except _TRANSIENT as exc:
            log.error("database transaction failed: %s", type(exc).__name__, exc_info=True)
            raise DatabaseUnavailable() from exc

    async def ping(self) -> Optional[float]:
        """Round-trip time in seconds, or None when the database can't be reached."""
        started = time.perf_counter()
        try:
            await self.fetchval("SELECT 1")
        except DatabaseUnavailable:
            return None
        return time.perf_counter() - started
