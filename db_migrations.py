"""Plain-SQL migrations.

Files live in migrations/ and are named NNNN_description.sql (0001, 0002, ...). On startup every
file that has not been applied yet runs, in order, inside one transaction guarded by an advisory
lock (so two instances starting together can't both migrate). Applied files are checksummed: edit
an applied migration and startup stops, because history must not be rewritten - add a new file.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("hopeless.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
_LOCK_ID = 7_263_541_001

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration file name: {path.name} (expected NNNN_description.sql)")
        found.append(Migration(int(match["version"]), match["name"], path.read_text(encoding="utf-8")))
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError("migration versions must be contiguous, starting at 0001")
    return found


async def run_migrations(db, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this run."""
    migrations = discover_migrations(directory)
    applied_now: list[int] = []
    async with db.transaction() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_ID)
        await conn.execute(_CREATE_TABLE)
        rows = await conn.fetch("SELECT version, checksum FROM schema_migrations ORDER BY version")
        applied = {row["version"]: row["checksum"] for row in rows}

        known = {m.version for m in migrations}
        ahead = sorted(set(applied) - known)
        if ahead:
            log.warning("database has migrations this code doesn't know about: %s (older code on a newer database?)", ahead)

        for migration in migrations:
            recorded = applied.get(migration.version)
            if recorded is not None:
                if recorded != migration.checksum:
                    raise MigrationError(
                        f"migration {migration.version:04d}_{migration.name} was edited after it was applied; "
                        "add a new migration instead"
                    )
                continue
            log.info("applying migration %04d_%s", migration.version, migration.name)
            await conn.execute(migration.sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                migration.version, migration.name, migration.checksum,
            )
            applied_now.append(migration.version)
    return applied_now
