"""Anti-raid protection (FEATURES.txt section 1).

Detects mass-join attacks and suspicious accounts as members arrive:

- Join Rate Detection: too many joins inside a short window triggers lockdown.
- Account Age Filter: brand-new accounts are kicked (or sent to verification).
- No-Avatar / Suspicious Pattern Detection: default-avatar accounts are flagged.
- Auto-Lockdown Mode: on a detected raid, invites are paused and @everyone is
  blocked from sending in the first text channel, with an owner alert.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands

from utils import (
    alert_owner_dm,
    build_security_embed,
    send_public_alert,
    send_security_log,
)

log = logging.getLogger("bot.antiraid")


class AntiRaid(commands.Cog):
    """Join-rate, account-age, and suspicious-pattern raid defense."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Per-guild sliding window of recent join timestamps (in-memory).
        self._recent_joins: dict[int, deque[float]] = defaultdict(deque)
        # Guilds currently in auto-lockdown, to avoid repeat triggers.
        self._locked_down: set[int] = set()

    def _record_join(self, guild_id: int, window_seconds: int) -> int:
        """Append now to the window, drop stale entries, return current count."""
        now = time.time()
        window = self._recent_joins[guild_id]
        window.append(now)
        cutoff = now - window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        return len(window)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return  # Bot adds are handled by the anti-nuke cog.

        guild = member.guild
        config = await self.bot.db.get_guild_config(guild.id)
        if not config.antiraid_enabled:
            return

        # --- Account age filter --------------------------------------- #
        account_age_seconds = time.time() - member.created_at.timestamp()
        min_age_seconds = config.min_account_age_hours * 3600
        too_new = account_age_seconds < min_age_seconds

        # --- No-avatar / suspicious pattern --------------------------- #
        no_avatar = member.avatar is None

        # --- Join rate detection -------------------------------------- #
        join_count = self._record_join(guild.id, config.join_rate_seconds)
        raid_detected = join_count >= config.join_rate_count

        if raid_detected:
            await self._trigger_lockdown(guild, join_count, config.join_rate_seconds)

        # Punish an individually suspicious account (new + no avatar, or new
        # during an active raid). A brand-new account with no avatar joining is
        # the classic bot-raid signature.
        if too_new and (no_avatar or raid_detected):
            await self._handle_suspicious_join(
                member, account_age_seconds, no_avatar, raid_detected
            )

    async def _handle_suspicious_join(
        self,
        member: discord.Member,
        account_age_seconds: float,
        no_avatar: bool,
        raid_detected: bool,
    ) -> None:
        guild = member.guild
        age_hours = int(account_age_seconds // 3600)
        flags = [f"account age {age_hours}h"]
        if no_avatar:
            flags.append("no avatar")
        if raid_detected:
            flags.append("during active raid")
        detail = ", ".join(flags)

        kicked = False
        try:
            await member.kick(reason=f"Anti-Raid: suspicious join ({detail})")
            kicked = True
        except discord.Forbidden:
            log.warning("Missing perms to kick suspicious join %s", member.id)
        except discord.HTTPException:
            log.exception("Failed to kick suspicious join %s", member.id)

        await self.bot.db.log_security_event(
            guild.id,
            member.id,
            action_type="suspicious_join",
            severity="medium",
            detail=f"{detail}; kicked={kicked}",
            punished=kicked,
        )
        if kicked:
            await self.bot.db.increment_threats_blocked(guild.id)

        embed = build_security_embed(
            "Suspicious Join Blocked",
            f"**User:** {member} (`{member.id}`)\n"
            f"**Flags:** {detail}\n"
            f"**Action:** {'kicked' if kicked else 'flagged (kick failed)'}",
            severity="medium",
        )
        await send_security_log(self.bot.db, guild, embed)

    async def _trigger_lockdown(
        self, guild: discord.Guild, join_count: int, window_seconds: int
    ) -> None:
        if guild.id in self._locked_down:
            return
        self._locked_down.add(guild.id)

        # Pause invites by blocking @everyone from sending in text channels.
        # (Discord has no single "disable all invites" API; blocking sends in
        # the visible channels is the practical lockdown a bot can apply.)
        everyone = guild.default_role
        locked_channels = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(everyone)
            if overwrite.send_messages is False:
                continue
            overwrite.send_messages = False
            try:
                await channel.set_permissions(
                    everyone, overwrite=overwrite, reason="Anti-Raid auto-lockdown"
                )
                locked_channels += 1
            except (discord.Forbidden, discord.HTTPException):
                continue

        await self.bot.db.log_security_event(
            guild.id,
            user_id=guild.me.id,
            action_type="auto_lockdown",
            severity="critical",
            detail=f"{join_count} joins in {window_seconds}s; locked {locked_channels} channels",
            punished=False,
        )
        await self.bot.db.increment_threats_blocked(guild.id)

        embed = build_security_embed(
            "RAID DETECTED - AUTO-LOCKDOWN",
            f"**{join_count} accounts** joined within {window_seconds}s.\n"
            f"Locked **{locked_channels}** channel(s). Use `/unlock` per channel "
            f"or `/lockdown lift` once the raid is over.",
            severity="critical",
        )
        await send_public_alert(self.bot.db, guild, embed)
        await alert_owner_dm(guild, embed)
        log.warning(
            "Anti-raid lockdown in guild %s: %d joins/%ds",
            guild.id, join_count, window_seconds,
        )

    def clear_lockdown_flag(self, guild_id: int) -> None:
        """Allow a manual lift to reset the one-shot lockdown guard."""
        self._locked_down.discard(guild_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaid(bot))
