"""Emoji system. Every icon the bot shows is defined HERE, once.

To change or add an icon: edit EMOJIS below (paste a custom emoji's name and id), or drop an image named
emoji_<key>.png (or .gif) next to the code. At startup the bot copies each custom emoji into its own
*application emojis*, so they work in every server without anyone adding emojis anywhere. If something
can't be copied, a plain unicode emoji is used instead, so nothing ever shows as a broken icon.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

log = logging.getLogger("hopeless.emoji")
ASSETS_DIR = Path(__file__).resolve().parent
MAX_EMOJI_BYTES = 256 * 1024


@dataclass(frozen=True)
class EmojiSpec:
    name: str
    id: Optional[int]  # None = no custom emoji yet (unicode fallback, or a local emoji_<key>.png)
    animated: bool
    fallback: str

    @property
    def markup(self) -> str:
        return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"


# key -> spec. Keys match module keys in core.command_registry plus the status icons.
EMOJIS: dict[str, EmojiSpec] = {
    "help": EmojiSpec("helper", 1558712278839328860, False, "📖"),
    "home": EmojiSpec("member", 1558710983118168084, False, "👥"),
    "antinuke": EmojiSpec("lp_shield", 1558710335748313101, False, "🛡️"),
    "whitelist": EmojiSpec("lp_user_check", 1558710465159499856, False, "✅"),
    "utility": EmojiSpec("lp_manager", 1558710662404771842, False, "🛠️"),
    "automod": EmojiSpec("lp_gamepad", 1558711511113801758, False, "🤖"),
    "welcome": EmojiSpec("member", 1558710983118168084, False, "👥"),  # shares Home's emoji on purpose
    "prestige": EmojiSpec("bluecrown_trk", 1558711155478765588, True, "👑"),
    "logging": EmojiSpec("lp_file", 1558711264123813888, False, "📰"),
    "tickets": EmojiSpec("bluecross_trk", 1558712927601565779, True, "🎫"),
    "voice": EmojiSpec("lp_headset", 1558710866021589002, False, "🎧"),
    # Status icons: no custom emoji yet. Send me the emoji (or drop emoji_<key>.png) and they switch automatically.
    "success": EmojiSpec("hl_success", None, False, "🔔"),
    "error": EmojiSpec("hl_error", None, False, "🔔"),
    "info": EmojiSpec("hl_info", None, False, "🔔"),
    "toggle_on": EmojiSpec("hl_toggle_on", None, False, "🟢"),
    "toggle_off": EmojiSpec("hl_toggle_off", None, False, "🔴"),
    "check": EmojiSpec("hl_check", None, False, "✅"),
    "cross": EmojiSpec("hl_cross", None, False, "❌"),
    "bullet": EmojiSpec("hl_bullet", None, False, "›"),
    "gear": EmojiSpec("hl_gear", None, False, "⚙️"),
    "siren": EmojiSpec("hl_siren", None, False, "🚨"),
    "chevron": EmojiSpec("hl_chevron", None, False, "🔸"),  # the red arrow used in select menus
}

# Icons with no custom emoji at all.
UNICODE_ONLY: dict[str, str] = {"moderation": "🔨", "setup": "⚙️", "owner": "🔑"}


class EmojiResolver:
    """Knows which custom emoji the bot can really use right now."""

    def __init__(self) -> None:
        self._available: frozenset[int] = frozenset()
        self._resolved: dict[str, tuple[str, int, bool]] = {}

    def refresh(self, accessible_ids: Iterable[int]) -> None:
        self._available = frozenset(accessible_ids)

    def use(self, key: str, name: str, emoji_id: int, animated: bool) -> None:
        """Point a key at a real (application) emoji."""
        self._resolved[key] = (name, emoji_id, animated)

    def _custom(self, key: str) -> Optional[tuple[str, int, bool]]:
        if key in self._resolved:
            return self._resolved[key]
        spec = EMOJIS.get(key)
        if spec is not None and spec.id is not None and spec.id in self._available:
            return (spec.name, spec.id, spec.animated)
        return None

    def is_available(self, key: str) -> bool:
        return self._custom(key) is not None

    def get(self, key: str) -> str:
        """Text form for embed titles, descriptions and message content."""
        custom = self._custom(key)
        if custom is not None:
            name, emoji_id, animated = custom
            return f"<{'a' if animated else ''}:{name}:{emoji_id}>"
        spec = EMOJIS.get(key)
        return spec.fallback if spec is not None else UNICODE_ONLY.get(key, "")

    def component(self, key: str):
        """Value for a Button/SelectOption emoji: a PartialEmoji when usable, else unicode (or None)."""
        custom = self._custom(key)
        if custom is not None:
            import discord

            name, emoji_id, animated = custom
            return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)
        spec = EMOJIS.get(key)
        if spec is not None:
            return spec.fallback
        return UNICODE_ONLY.get(key) or None


ICONS = EmojiResolver()  # the one shared resolver (bot.icons is this object)


def _local_image(key: str) -> Optional[Path]:
    for folder in (ASSETS_DIR, ASSETS_DIR.parent):
        for ext in ("png", "gif"):
            path = folder / f"emoji_{key}.{ext}"
            if path.is_file():
                return path
    return None


async def _download(url: str) -> Optional[bytes]:
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                return await response.read() if response.status == 200 else None
    except Exception:
        return None


async def sync_application_emojis(
    bot, resolver: EmojiResolver = ICONS, *, fetch: Callable[[str], Awaitable[Optional[bytes]]] = _download
) -> dict[str, int]:
    """Copy every configured custom emoji into the bot's own application emojis (once), then use those.

    Existing application emojis are reused, so this only uploads on the first run. Any failure leaves that
    icon on its unicode fallback. Returns counts for the log.
    """
    stats = {"ready": 0, "uploaded": 0, "failed": 0}
    try:
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except Exception:
        log.info("application emojis aren't available; using fallbacks")
        return stats
    by_source: dict[object, object] = {}  # keys that share a source emoji share one upload
    for key, spec in EMOJIS.items():
        local = _local_image(key)
        if spec.id is None and local is None:
            continue
        source = spec.id if local is None else str(local)
        if source in by_source:
            emoji = by_source[source]
        else:
            app_name = f"hl_{key}"[:32]
            emoji = existing.get(app_name)
            if emoji is None:
                if local is not None:
                    data = local.read_bytes()
                else:
                    ext = "gif" if spec.animated else "png"
                    data = await fetch(f"https://cdn.discordapp.com/emojis/{spec.id}.{ext}?size=128")
                    if data is not None and len(data) > MAX_EMOJI_BYTES:
                        data = await fetch(f"https://cdn.discordapp.com/emojis/{spec.id}.{ext}?size=64")
                if not data or len(data) > MAX_EMOJI_BYTES:
                    stats["failed"] += 1
                    continue
                try:
                    emoji = await bot.create_application_emoji(name=app_name, image=data)
                    stats["uploaded"] += 1
                except Exception:
                    log.warning("could not upload emoji %s", key, exc_info=True)
                    stats["failed"] += 1
                    continue
            by_source[source] = emoji
        resolver.use(key, emoji.name, emoji.id, bool(emoji.animated))
        stats["ready"] += 1
    log.info("emoji sync: %s", stats)
    return stats
