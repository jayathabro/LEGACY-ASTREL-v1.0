"""Passive deterrent presence (psychological deterrent, FEATURES.txt section 6).

Every so often, at a randomised interval, posts a low-key "security is
watching" embed to each guild's public alerts channel. The goal is to make
members feel a security system is actively monitoring the server, without
pinging anyone or spamming the channel.

Design choices to keep it non-annoying:
- Never mentions/tags anyone (allowed_mentions is stripped to none).
- Fires on a RANDOM interval per guild (DETERRENT_MIN..DETERRENT_MAX), so it
  doesn't feel like clockwork.
- Rotates through varied messages so it doesn't repeat the same line.
- Only posts to the already-configured alerts channel; if none is set, the
  guild is silently skipped (no new channels are created, nothing is forced).
- Enabled only while anti-nuke or anti-raid is on, so a server that turned
  protection off doesn't get reminder spam.
"""
from __future__ import annotations

import asyncio
import logging
import random

import discord
from discord.ext import commands

from utils import build_security_embed, _resolve_text_channel

log = logging.getLogger("bot.presence")

# Randomised gap between deterrent posts, per guild (in seconds).
DETERRENT_MIN_SECONDS = 2 * 3600   # 2 hours
DETERRENT_MAX_SECONDS = 6 * 3600   # 6 hours

# Pool of deterrent lines: (title, body). Picked at random each time.
DETERRENT_MESSAGES: tuple[tuple[str, str], ...] = (
    (
        "Security Scan Complete",
        "Routine perimeter scan finished. No threats detected. "
        "Anti-Nuke and Anti-Raid systems remain armed.",
    ),
    (
        "Monitoring Active",
        "This server is under continuous protection. All administrative "
        "actions are being logged and audited in real time.",
    ),
    (
        "Systems Armed",
        "Anti-Nuke threshold detection is online. Any mass channel/role "
        "deletion, ban, or unauthorised bot add is caught automatically.",
    ),
    (
        "Raid Defence Online",
        "Join-rate and account-age filters are active. Suspicious mass joins "
        "trigger an automatic lockdown.",
    ),
    (
        "Audit Trail Recording",
        "Every moderation and administrative action is time-stamped and stored. "
        "Nothing goes unnoticed.",
    ),
    (
        "Standing Guard",
        "Legacy Astrel is watching over this server 24/7. Malicious activity "
        "is neutralised on detection.",
    ),
)


class Presence(commands.Cog):
    """Posts randomised, tag-free 'security is watching' deterrent messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._deterrent_loop())

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _deterrent_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                # Randomised gap so posts don't feel like a fixed schedule.
                delay = random.randint(DETERRENT_MIN_SECONDS, DETERRENT_MAX_SECONDS)
                await asyncio.sleep(delay)
                await self._post_to_all_guilds()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Deterrent loop crashed")

    async def _post_to_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._post_deterrent(guild)
            except Exception:
                log.exception("Deterrent post failed for guild %s", guild.id)

    async def _post_deterrent(self, guild: discord.Guild) -> None:
        config = await self.bot.db.get_guild_config(guild.id)

        # Only remind when protection is actually on; otherwise stay quiet.
        if not (config.antinuke_enabled or config.antiraid_enabled):
            return

        channel = await _resolve_text_channel(guild, config.alerts_channel_id)
        if channel is None:
            return  # No alerts channel configured; skip silently.

        title, body = random.choice(DETERRENT_MESSAGES)
        embed = build_security_embed(title, body, severity="info")
        try:
            # allowed_mentions=none guarantees no one is ever pinged/tagged.
            await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.Forbidden:
            pass  # Lost access to the channel; nothing to do.


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Presence(bot))
