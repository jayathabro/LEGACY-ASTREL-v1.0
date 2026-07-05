"""Shared helpers used across cogs: embeds and permission/hierarchy checks."""
from __future__ import annotations

import datetime as dt
import json

import discord

# Consistent color palette for embeds across the bot.
COLOR_SUCCESS = discord.Color.green()
COLOR_ERROR = discord.Color.red()
COLOR_INFO = discord.Color.blurple()
COLOR_WARN = discord.Color.orange()

# Security-specific palette: deliberately more alarming than regular
# moderation colors so anti-nuke/anti-raid actions read as "serious" at a
# glance (Legacy Astrel visual identity, see FEATURES.txt section 6).
COLOR_SECURITY_CRITICAL = discord.Color.dark_red()
COLOR_SECURITY_HIGH = discord.Color.red()
COLOR_SECURITY_MEDIUM = discord.Color.orange()
COLOR_SECURITY_INFO = discord.Color.dark_teal()

BOT_BRAND_NAME = "Legacy Astrel v.1.0"

SEVERITY_COLORS: dict[str, discord.Color] = {
    "critical": COLOR_SECURITY_CRITICAL,
    "high": COLOR_SECURITY_HIGH,
    "medium": COLOR_SECURITY_MEDIUM,
    "info": COLOR_SECURITY_INFO,
}

SEVERITY_EMOJI: dict[str, str] = {
    "critical": "\N{SHIELD}\N{VARIATION SELECTOR-16}",
    "high": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
    "medium": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
    "info": "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}",
}


def build_embed(
    title: str,
    description: str = "",
    color: discord.Color = COLOR_INFO,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = dt.datetime.now(dt.timezone.utc)
    return embed


def build_security_embed(
    title: str,
    description: str = "",
    severity: str = "high",
) -> discord.Embed:
    """Build an embed styled for anti-raid/anti-nuke output.

    Uses the dark-red/red/orange security palette and a shield/warning emoji
    plus a consistent branded footer, per FEATURES.txt section 6 ("Strong
    Visual Identity"). `severity` must be one of: critical, high, medium, info.
    """
    color = SEVERITY_COLORS.get(severity, COLOR_SECURITY_HIGH)
    emoji = SEVERITY_EMOJI.get(severity, SEVERITY_EMOJI["high"])
    embed = discord.Embed(title=f"{emoji} {title}", description=description, color=color)
    embed.timestamp = dt.datetime.now(dt.timezone.utc)
    embed.set_footer(text=f"{BOT_BRAND_NAME} SECURITY SYSTEM")
    return embed


class HierarchyError(Exception):
    """Raised when a moderation action would violate Discord's role hierarchy."""


def ensure_can_moderate(
    *,
    actor: discord.Member,
    target: discord.Member,
    bot_member: discord.Member,
) -> None:
    """Validate that `actor` and the bot both outrank `target`.

    Raises HierarchyError with a user-facing message if the action is not
    permitted by Discord's role hierarchy. The guild owner is exempt from
    being targeted entirely (nobody outranks the owner).
    """
    if target.id == actor.guild.owner_id:
        raise HierarchyError("You cannot moderate the server owner.")
    if target.id == actor.id:
        raise HierarchyError("You cannot target yourself.")
    if target.id == bot_member.id:
        raise HierarchyError("You cannot target the bot.")
    if actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
        raise HierarchyError(
            "You cannot moderate someone with an equal or higher role than you."
        )
    if target.top_role >= bot_member.top_role:
        raise HierarchyError(
            "My highest role is not above that member's highest role, "
            "so Discord won't let me perform this action."
        )


async def is_antinuke_immune(db, member: discord.Member) -> bool:
    """Check whether `member` is exempt from anti-nuke punishment.

    This is the "Independent Permission System" from FEATURES.txt section 2:
    Discord's Administrator permission is NOT enough on its own. A compromised
    admin account still gets punished unless they are the guild owner, the bot
    itself, or explicitly present in the bot's own `trusted_admins` table.
    """
    if member.id == member.guild.owner_id:
        return True
    if member.bot and member.id == member.guild.me.id:
        return True
    return await db.is_trusted_admin(member.guild.id, member.id)


# --------------------------------------------------------------------------- #
# Security dispatch helpers (logging channel, public alerts, owner DM)
# --------------------------------------------------------------------------- #


async def _resolve_text_channel(
    guild: discord.Guild, channel_id: int | None
) -> discord.TextChannel | discord.Thread | None:
    if channel_id is None:
        return None
    channel = guild.get_channel(channel_id)
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        return channel
    return None


async def send_security_log(
    db, guild: discord.Guild, embed: discord.Embed
) -> None:
    """Send an embed to the configured security-log channel, if any."""
    config = await db.get_guild_config(guild.id)
    channel = await _resolve_text_channel(guild, config.security_log_channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def send_public_alert(
    db, guild: discord.Guild, embed: discord.Embed
) -> None:
    """Send a public 'threat neutralized' style embed to the alerts channel.

    Falls back to the security-log channel if no dedicated alerts channel is
    configured, so a raid announcement is never silently dropped.
    """
    config = await db.get_guild_config(guild.id)
    channel = await _resolve_text_channel(guild, config.alerts_channel_id)
    if channel is None:
        channel = await _resolve_text_channel(guild, config.security_log_channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def alert_owner_dm(guild: discord.Guild, embed: discord.Embed) -> None:
    """DM the guild owner for high-severity incidents. Best-effort only."""
    owner = guild.owner
    if owner is None:
        try:
            owner = await guild.fetch_member(guild.owner_id)
        except (discord.NotFound, discord.HTTPException):
            return
    try:
        await owner.send(embed=embed)
    except discord.Forbidden:
        pass


# --------------------------------------------------------------------------- #
# Snapshot serialization (backup & recovery, snapshot-before-destroy)
# --------------------------------------------------------------------------- #


def serialize_guild_structure(guild: discord.Guild) -> str:
    """Serialize the guild's roles, categories, and channels to JSON.

    Message history is intentionally NOT captured (Discord API limitation,
    see FEATURES.txt section 4). Only structural data needed to recreate the
    server skeleton is stored.
    """
    roles = [
        {
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "permissions": role.permissions.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "position": role.position,
        }
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        if not role.is_default() and not role.managed
    ]

    channels = []
    for channel in guild.channels:
        entry = {
            "id": channel.id,
            "name": channel.name,
            "type": channel.type.value,
            "position": channel.position,
            "category_id": channel.category_id,
        }
        if isinstance(channel, discord.TextChannel):
            entry["topic"] = channel.topic
            entry["nsfw"] = channel.is_nsfw()
            entry["slowmode_delay"] = channel.slowmode_delay
        channels.append(entry)

    return json.dumps({"roles": roles, "channels": channels})
