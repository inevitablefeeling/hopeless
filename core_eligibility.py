"""Who may enter a giveaway or vote in a poll: an optional required role and an optional minimum message count."""
from __future__ import annotations

from typing import Iterable, Optional


def check_eligibility(*, role_ids: Iterable[int], required_role_id: Optional[int], messages: int, min_messages: int,
                      tracking_note: str = "") -> Optional[str]:
    """None when the member qualifies; otherwise a short sentence saying exactly which requirement they haven't met."""
    if required_role_id and required_role_id not in set(role_ids):
        return f"You need the <@&{required_role_id}> role to take part."
    if min_messages and messages < min_messages:
        extra = f" {tracking_note}" if tracking_note else ""
        return f"You need at least **{min_messages}** messages (I've counted **{messages}** from you).{extra}"
    return None
