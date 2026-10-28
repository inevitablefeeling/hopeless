"""PostgreSQL implementations of the store protocols used by the services.

Every guild-specific query filters on guild_id. Guild ids always come from the command context
(never from user-typed text) and are validated again in the service layer.
"""
from __future__ import annotations

from typing import Optional

from core_permissions import GlobalRole
from core_whitelist_logic import WhitelistEntry
from db_connection import Database
from svc_afk_service import AfkRecord

_ENSURE_GUILD = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING"


class PgPrefixStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def load_all(self) -> dict[int, str]:
        rows = await self.db.fetch("SELECT guild_id, prefix FROM guild_settings WHERE prefix IS NOT NULL")
        return {int(row["guild_id"]): row["prefix"] for row in rows}

    async def save(self, guild_id: int, prefix: str) -> None:
        await self.db.execute(
            """
            INSERT INTO guild_settings (guild_id, prefix) VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET prefix = EXCLUDED.prefix, updated_at = now()
            """,
            guild_id, prefix,
        )

    async def clear(self, guild_id: int) -> None:
        await self.db.execute(
            "UPDATE guild_settings SET prefix = NULL, updated_at = now() WHERE guild_id = $1", guild_id
        )


class PgStaffStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def load_all(self) -> dict[int, GlobalRole]:
        rows = await self.db.fetch("SELECT user_id, role FROM global_staff")
        return {int(row["user_id"]): GlobalRole(row["role"]) for row in rows}

    async def save(self, user_id: int, role: GlobalRole, added_by: int) -> None:
        await self.db.execute(
            """
            INSERT INTO global_staff (user_id, role, added_by) VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role, added_by = EXCLUDED.added_by, added_at = now()
            """,
            user_id, role.value, added_by,
        )

    async def delete(self, user_id: int) -> bool:
        status = await self.db.execute("DELETE FROM global_staff WHERE user_id = $1", user_id)
        return _affected(status) > 0


class PgWhitelistStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def load_guild(self, guild_id: int) -> list[WhitelistEntry]:
        rows = await self.db.fetch(
            """
            SELECT guild_id, target_type, target_id, scope, added_by, added_at
            FROM whitelist_entries WHERE guild_id = $1 ORDER BY added_at, id
            """,
            guild_id,
        )
        return [
            WhitelistEntry(
                int(r["guild_id"]), r["target_type"], int(r["target_id"]), r["scope"], int(r["added_by"]), r["added_at"]
            )
            for r in rows
        ]

    async def add(self, entry: WhitelistEntry) -> bool:
        async with self.db.transaction() as conn:
            await conn.execute(_ENSURE_GUILD, entry.guild_id)
            new_id = await conn.fetchval(
                """
                INSERT INTO whitelist_entries (guild_id, target_type, target_id, scope, added_by)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT ON CONSTRAINT whitelist_entries_unique DO NOTHING
                RETURNING id
                """,
                entry.guild_id, entry.target_type, entry.target_id, entry.scope, entry.added_by,
            )
        return new_id is not None

    async def remove(self, guild_id: int, target_type: str, target_id: int, scope: Optional[str]) -> int:
        if scope is None:
            status = await self.db.execute(
                "DELETE FROM whitelist_entries WHERE guild_id = $1 AND target_type = $2 AND target_id = $3",
                guild_id, target_type, target_id,
            )
        else:
            status = await self.db.execute(
                """
                DELETE FROM whitelist_entries
                WHERE guild_id = $1 AND target_type = $2 AND target_id = $3 AND scope = $4
                """,
                guild_id, target_type, target_id, scope,
            )
        return _affected(status)


class PgAfkStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def load_all(self) -> list[AfkRecord]:
        rows = await self.db.fetch("SELECT guild_id, user_id, reason, since FROM afk_status")
        return [AfkRecord(int(r["guild_id"]), int(r["user_id"]), r["reason"], r["since"]) for r in rows]

    async def save(self, record: AfkRecord) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(_ENSURE_GUILD, record.guild_id)
            await conn.execute(
                """
                INSERT INTO afk_status (guild_id, user_id, reason, since) VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET reason = EXCLUDED.reason, since = EXCLUDED.since
                """,
                record.guild_id, record.user_id, record.reason, record.since,
            )

    async def delete(self, guild_id: int, user_id: int) -> bool:
        status = await self.db.execute(
            "DELETE FROM afk_status WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return _affected(status) > 0


class PgUsageStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add_usage(self, rows: list[tuple[int, str, int]]) -> None:
        await self.db.executemany(
            """
            INSERT INTO command_usage (guild_id, command, uses) VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, command)
            DO UPDATE SET uses = command_usage.uses + EXCLUDED.uses, last_used = now()
            """,
            rows,
        )

    async def summary(self) -> tuple[int, list[tuple[str, int]]]:
        total = await self.db.fetchval("SELECT COALESCE(SUM(uses), 0) FROM command_usage")
        top = await self.db.fetch(
            "SELECT command, SUM(uses)::BIGINT AS uses FROM command_usage GROUP BY command ORDER BY uses DESC LIMIT 5"
        )
        return int(total), [(row["command"], int(row["uses"])) for row in top]


def _affected(status: str) -> int:
    """asyncpg returns tags like 'DELETE 3'."""
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0
