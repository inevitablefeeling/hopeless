"""Bot owner and global staff. The owner comes only from BOT_OWNER_ID and is checked on every call."""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from core_errors import InvalidInput, NotAuthorized
from core_permissions import GlobalRole

log = logging.getLogger("hopeless.permissions")


class StaffStore(Protocol):
    async def load_all(self) -> dict[int, GlobalRole]: ...
    async def save(self, user_id: int, role: GlobalRole, added_by: int) -> None: ...
    async def delete(self, user_id: int) -> bool: ...


class PermissionService:
    def __init__(self, settings, store: StaffStore) -> None:
        self._settings = settings  # read at call time so the owner id is never cached
        self._store = store
        self._staff: dict[int, GlobalRole] = {}

    async def load(self) -> None:
        self._staff = dict(await self._store.load_all())

    def is_owner(self, user_id: int) -> bool:
        return user_id == self._settings.bot_owner_id

    def global_role(self, user_id: int) -> Optional[GlobalRole]:
        return None if self.is_owner(user_id) else self._staff.get(user_id)

    def is_global_admin(self, user_id: int) -> bool:
        return self.global_role(user_id) is GlobalRole.ADMINISTRATOR

    def is_global_staff(self, user_id: int) -> bool:
        return self.is_owner(user_id) or user_id in self._staff

    def list_staff(self, role: Optional[GlobalRole] = None) -> list[tuple[int, GlobalRole]]:
        return sorted(
            ((uid, r) for uid, r in self._staff.items() if role is None or r is role),
            key=lambda item: item[0],
        )

    def _require_owner(self, actor_id: int) -> None:
        if not self.is_owner(actor_id):
            raise NotAuthorized("Only the bot owner can manage global staff.")

    async def add_staff(self, *, actor_id: int, target_id: int, role: GlobalRole) -> str:
        """Returns "added", "changed" (moved to another role) or "unchanged"."""
        self._require_owner(actor_id)
        if self.is_owner(target_id):
            raise InvalidInput("The bot owner already has every permission.")
        current = self._staff.get(target_id)
        if current is role:
            return "unchanged"
        await self._store.save(target_id, role, actor_id)
        self._staff[target_id] = role
        log.info("global staff %s: user=%s role=%s by=%s", "added" if current is None else "changed", target_id, role.value, actor_id)
        return "added" if current is None else "changed"

    async def remove_staff(self, *, actor_id: int, target_id: int, role: GlobalRole) -> bool:
        """Removes the user only if they currently hold exactly `role`."""
        self._require_owner(actor_id)
        if self._staff.get(target_id) is not role:
            return False
        await self._store.delete(target_id)
        self._staff.pop(target_id, None)
        log.info("global staff removed: user=%s role=%s by=%s", target_id, role.value, actor_id)
        return True
