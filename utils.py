import re
from datetime import timedelta
import discord
from config import ACCENT_COLOR


def parse_duration(text: str) -> timedelta | None:
    pattern = (
        r"((?P<weeks>\d+)\s*w(?:eeks?)?)?"
        r"\s*((?P<days>\d+)\s*d(?:ays?)?)?"
        r"\s*((?P<hours>\d+)\s*h(?:ours?|rs?)?)?"
        r"\s*((?P<minutes>\d+)\s*m(?:inutes?|ins?)?)?"
        r"\s*((?P<seconds>\d+)\s*s(?:econds?|ecs?)?)?"
    )
    m = re.fullmatch(pattern, text.strip().lower())
    if not m:
        return None
    parts = {k: int(v) for k, v in m.groupdict(default="0").items()}
    delta = timedelta(**parts)
    return delta if delta.total_seconds() > 0 else None


def format_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    weeks = total // 604800
    days = (total % 604800) // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60

    parts = []
    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else "0s"


def base_embed(
    title: str | None = None,
    description: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=ACCENT_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="AU Schools")
    return embed


def success_embed(text: str) -> discord.Embed:
    return base_embed(description=f"\u2705 {text}")


def error_embed(text: str) -> discord.Embed:
    return base_embed(description=f"\u274c {text}")
