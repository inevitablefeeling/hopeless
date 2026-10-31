"""What the help panel shows, as plain data (no Discord imports, so it is unit-tested).

Everything comes from the command registry, which the startup parity check compares against the commands the bot
really has, so the lists and counts here are the real ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core_command_registry import CommandSpec, ModuleSpec, registry

HOME = "home"
ALL = "all"
TEXT_BUDGET = 3500  # a Components V2 message may hold at most 4000 characters of text in total


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    summary: str
    blurb: str
    commands: tuple[CommandSpec, ...]


def categories(staff: bool = False) -> list[Category]:
    """Categories that really have commands. Restricted ones (global administration) appear only for global staff."""
    result = []
    for module in registry.visible_modules(include_restricted=staff):
        if module.key == HOME:
            continue
        specs = tuple(s for s in registry.commands(module.key, implemented_only=True) if s.name)
        if specs:
            result.append(Category(module.key, module.title, module.summary, module.blurb, specs))
    return result


def total_commands(staff: bool = False) -> int:
    return sum(len(c.commands) for c in categories(staff))


def category_by_key(key: str, staff: bool = False) -> Optional[Category]:
    return next((c for c in categories(staff) if c.key == key), None)


def command_line(spec: CommandSpec, prefix: str) -> str:
    return f"`{prefix}{spec.name}` - {spec.description}"


def fit(blocks: list[str], limit: int = TEXT_BUDGET) -> list[str]:
    """Keep blocks while they fit the text budget; say how many were left out instead of failing to send."""
    used, kept = 0, []
    for index, block in enumerate(blocks):
        if used + len(block) > limit:
            kept.append(f"-# …and {len(blocks) - index} more. Use `help <command>` to look one up.")
            break
        kept.append(block)
        used += len(block)
    return kept


def category_blocks(category: Category, prefix: str, per_block: int = 7) -> list[str]:
    """Command lines grouped a few at a time, so separators can break a long page into readable parts."""
    lines = [command_line(spec, prefix) for spec in category.commands]
    return fit(["\n".join(lines[i:i + per_block]) for i in range(0, len(lines), per_block)])


def all_blocks(prefix: str, staff: bool = False) -> list[str]:
    """One block per category: the title and the command names only, for a full overview."""
    blocks = []
    for cat in categories(staff):
        names = ", ".join(f"`{prefix}{s.name}`" for s in cat.commands)
        blocks.append(f"**{cat.title}**\n{names}")
    return fit(blocks)


def home_lines(prefix: str, staff: bool = False) -> tuple[str, str]:
    """(facts, tips) for the home page."""
    facts = (f"**Prefix:** `{prefix}`\n**Help:** `{prefix}help`\n"
             f"**Categories:** `{len(categories(staff))}`\n**Total Commands:** `{total_commands(staff)}`")
    tips = ("Use the dropdown to browse categories.\nUse **All Commands** for a full overview.\n"
            f"Use `{prefix}help <command>` for details.")
    return facts, tips
