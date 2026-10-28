"""Small text helpers shared by cogs."""
from __future__ import annotations


def format_duration(seconds: float) -> str:
    """1h 2m 3s style duration. Always shows seconds; leading zero units are dropped."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    if days or hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if limit <= len(suffix):
        return text[:limit]
    return text if len(text) <= limit else text[: limit - len(suffix)] + suffix


def chunk_lines(lines: list[str], limit: int = 1000) -> list[str]:
    """Group lines into blocks no longer than `limit` characters (embed field values max out at 1024)."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = truncate(line, limit)
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def clean_inline(text: str, limit: int = 40) -> str:
    """Make user input safe to echo inside `code` spans in an error message."""
    flat = " ".join(text.replace("`", "").split())
    return truncate(flat, limit)


def long_duration(seconds: float) -> str:
    """'2 hours, 14 minutes'. Shows at most the two largest non-zero units; under a minute says so."""
    total = max(0, int(seconds))
    units = (("day", 86_400), ("hour", 3_600), ("minute", 60), ("second", 1))
    parts = []
    for name, size in units:
        count, total = divmod(total, size)
        if count:
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    if not parts:
        return "0 seconds"
    if len(parts) == 1 or parts[0].endswith(("second", "seconds")):
        return parts[0]
    if parts[0].split()[1].startswith("minute"):
        return parts[0]  # "5 minutes 20 seconds" reads better as "5 minutes"
    return ", ".join(parts[:2])
