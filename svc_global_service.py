"""Bot-wide administration: global staff permissions, global bans and the audit trail.

A global ban is a bot-wide restriction ONLY. It never bans anyone from a Discord server and never removes
messages. The owner (BOT_OWNER_ID) can never be banned or locked out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core_errors import InvalidInput, NotAuthorized
from core_global_logic import DEFAULT_ADMIN_PERMS, GLOBAL_PERMS, ban_denied, is_active, validate_perm
from core_permissions import GlobalRole

log = logging.getLogger("hopeless.global")


class GlobalService:
    def __init__(self, db, permissions) -> None:
        self.db = db
        self.perm = permissions
        self._bans: dict[int, tuple[int, Optional[datetime], str]] = {}  # user -> (case id, expires, reason)
        self._perms: dict[int, frozenset[str]] = {}

    # --- loading -----------------------------------------------------------------------------
    async def load(self) -> None:
        await self.db.execute(
            "UPDATE global_bans SET active = FALSE, lifted_at = now(), lift_reason = 'expired' "
            "WHERE active AND expires_at IS NOT NULL AND expires_at <= now()")
        rows = await self.db.fetch("SELECT case_id, user_id, reason, expires_at FROM global_bans WHERE active")
        self._bans = {int(r["user_id"]): (int(r["case_id"]), r["expires_at"], r["reason"]) for r in rows}
        staff = await self.db.fetch("SELECT user_id, perms FROM global_staff")
        self._perms = {int(r["user_id"]): frozenset(r["perms"]) for r in staff}

    # --- fast lookups used on every command --------------------------------------------------
    def ban_for(self, user_id: int) -> Optional[tuple[int, Optional[datetime], str]]:
        if self.perm.is_owner(user_id):
            return None
        entry = self._bans.get(user_id)
        if entry is None or not is_active(entry[1], datetime.now(timezone.utc)):
            return None
        return entry

    def ban_message(self, user_id: int) -> Optional[str]:
        entry = self.ban_for(user_id)
        if entry is None:
            return None
        case, expires, reason = entry
        until = f" It expires <t:{int(expires.timestamp())}:R>." if expires else ""
        return f"You are banned from using HOPELESS (case #{case}). Reason: {reason}.{until}"

    def perms_of(self, user_id: int) -> frozenset[str]:
        return frozenset(GLOBAL_PERMS) if self.perm.is_owner(user_id) else self._perms.get(user_id, frozenset())

    def has_perm(self, user_id: int, perm: str) -> bool:
        if self.perm.is_owner(user_id):
            return True
        return self.perm.global_role(user_id) is not None and perm in self._perms.get(user_id, frozenset())

    # --- audit -------------------------------------------------------------------------------
    async def audit(self, actor_id: int, action: str, target_id: Optional[int] = None, detail: Optional[str] = None) -> None:
        try:
            await self.db.execute("INSERT INTO global_audit (actor_id, action, target_id, detail) VALUES ($1,$2,$3,$4)",
                                  actor_id, action, target_id, (detail or "")[:500])
        except Exception:
            log.warning("could not write the global audit entry %s", action, exc_info=True)

    async def recent_audit(self, limit: int = 200):
        return await self.db.fetch("SELECT actor_id, action, target_id, detail, created_at FROM global_audit ORDER BY id DESC LIMIT $1", limit)

    # --- staff permissions -------------------------------------------------------------------
    async def reset_perms(self, actor_id: int, user_id: int, role: GlobalRole) -> None:
        """Called when someone is added or changes role: administrators start with the default, moderators with none."""
        perms = sorted(DEFAULT_ADMIN_PERMS) if role is GlobalRole.ADMINISTRATOR else []
        await self.db.execute("UPDATE global_staff SET perms = $2 WHERE user_id = $1", user_id, perms)
        self._perms[user_id] = frozenset(perms)
        await self.audit(actor_id, f"staff.{role.value}.set", user_id, "permissions reset to: " + (", ".join(perms) or "none"))

    async def change_perm(self, actor_id: int, user_id: int, perm: str, grant: bool) -> frozenset[str]:
        if not self.perm.is_owner(actor_id):
            raise NotAuthorized("Only the bot owner can change global permissions.")
        perm = validate_perm(perm)
        if self.perm.global_role(user_id) is None:
            raise InvalidInput("That person isn't global staff. Add them with /administrator or /mod first.")
        current = set(self._perms.get(user_id, frozenset()))
        current.add(perm) if grant else current.discard(perm)
        await self.db.execute("UPDATE global_staff SET perms = $2 WHERE user_id = $1", user_id, sorted(current))
        self._perms[user_id] = frozenset(current)
        await self.audit(actor_id, "perm.grant" if grant else "perm.revoke", user_id, perm)
        return self._perms[user_id]

    def forget_staff(self, user_id: int) -> None:
        self._perms.pop(user_id, None)

    # --- bans --------------------------------------------------------------------------------
    async def ban(self, *, actor_id: int, target_id: int, reason: str, seconds: Optional[int], bot_id: int) -> int:
        role = self.perm.global_role(target_id)
        denied = ban_denied(actor_id=actor_id, actor_is_owner=self.perm.is_owner(actor_id), actor_has_gban=self.has_perm(actor_id, "gban"),
                            target_id=target_id, target_is_owner=self.perm.is_owner(target_id),
                            target_role=role.value if role else None, bot_id=bot_id)
        if denied:
            raise NotAuthorized(denied)
        reason = (reason or "No reason provided").strip()[:300]
        expires = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
        try:
            async with self.db.transaction() as conn:
                await conn.execute(
                    "UPDATE global_bans SET active = FALSE, lifted_at = now(), lift_reason = 'expired' "
                    "WHERE user_id = $1 AND active AND expires_at IS NOT NULL AND expires_at <= now()", target_id)
                case = await conn.fetchval(
                    "INSERT INTO global_bans (user_id, reason, issued_by, expires_at) VALUES ($1,$2,$3,$4) RETURNING case_id",
                    target_id, reason, actor_id, expires)
                await conn.execute("INSERT INTO global_audit (actor_id, action, target_id, detail) VALUES ($1,'gban',$2,$3)",
                                   actor_id, target_id, f"case {case}: {reason}")
        except Exception as exc:
            if exc.__class__.__name__ == "UniqueViolationError":
                raise InvalidInput("That user is already globally banned.") from exc
            raise
        self._bans[target_id] = (int(case), expires, reason)
        return int(case)

    async def unban(self, *, actor_id: int, target_id: int, reason: str) -> int:
        if not self.has_perm(actor_id, "gban"):
            raise NotAuthorized("You aren't authorised to lift global bans.")
        case = await self.db.fetchval(
            "UPDATE global_bans SET active = FALSE, lifted_by = $2, lifted_at = now(), lift_reason = $3 "
            "WHERE user_id = $1 AND active RETURNING case_id", target_id, actor_id, (reason or "No reason provided")[:300])
        if case is None:
            raise InvalidInput("That user isn't globally banned.")
        self._bans.pop(target_id, None)
        await self.audit(actor_id, "gunban", target_id, f"case {case}: {reason}")
        return int(case)

    async def get_cases(self, *, user_id: Optional[int] = None, case_id: Optional[int] = None, active_only: bool = False, limit: int = 200):
        clauses, args = [], []
        for text, value in (("user_id = $%d", user_id), ("case_id = $%d", case_id)):
            if value is not None:
                args.append(value)
                clauses.append(text % len(args))
        if active_only:
            clauses.append("active")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        return await self.db.fetch(f"SELECT * FROM global_bans {where} ORDER BY case_id DESC LIMIT ${len(args)}", *args)
