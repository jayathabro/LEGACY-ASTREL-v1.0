"""Anti-nuke protection (FEATURES.txt section 2).

Watches destructive administrative actions via Discord's audit log and gateway
events. When a single non-trusted actor performs too many dangerous actions
inside a short window (the "Action Threshold System"), the bot strips their
roles and bans them, then announces the neutralized threat.

The trust model is deliberately independent of Discord's Administrator
permission: a compromised admin account is still punished unless it is the
guild owner, the bot itself, or on the bot's own trusted_admins list. See
utils.is_antinuke_immune.
"""
from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

from utils import (
    alert_owner_dm,
    build_security_embed,
    is_antinuke_immune,
    send_public_alert,
    send_security_log,
    serialize_guild_structure,
)

log = logging.getLogger("bot.antinuke")

# Audit-log action types we treat as "dangerous" and their human labels.
CHANNEL_DELETE = "channel_delete"
ROLE_DELETE = "role_delete"
BAN = "ban"
KICK = "kick"
WEBHOOK_CREATE = "webhook_create"
BOT_ADD = "bot_add"

ACTION_LABELS = {
    CHANNEL_DELETE: "delete channels",
    ROLE_DELETE: "delete roles",
    BAN: "mass-ban members",
    KICK: "mass-kick members",
    WEBHOOK_CREATE: "create webhooks",
    BOT_ADD: "add bots",
}


class AntiNuke(commands.Cog):
    """Threshold-based destructive-action detection and auto-response."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Core threshold + punishment pipeline
    # ------------------------------------------------------------------ #

    async def _handle_dangerous_action(
        self,
        guild: discord.Guild,
        actor: discord.Member | discord.User | None,
        action_type: str,
    ) -> None:
        """Record a dangerous action and punish the actor if over threshold."""
        if actor is None:
            return

        config = await self.bot.db.get_guild_config(guild.id)
        if not config.antinuke_enabled:
            return

        # Resolve to a Member so we can check trust / strip roles.
        member = guild.get_member(actor.id)
        if member is None:
            # Actor already left (or is only a User); nothing to punish safely.
            return

        if await is_antinuke_immune(self.bot.db, member):
            return

        now = int(time.time())
        window_start = now - config.action_threshold_seconds

        await self.bot.db.log_security_event(
            guild.id,
            member.id,
            action_type,
            severity="high",
            detail=f"Performed action: {action_type}",
            punished=False,
        )

        # During emergency freeze, a single dangerous action by a non-trusted
        # actor is punished immediately -- the normal threshold is bypassed
        # (FEATURES.txt section 8, Emergency Freeze Mode).
        frozen = config.frozen_until is not None and config.frozen_until > now
        if frozen:
            await self._punish(guild, member, action_type, count=1)
            return

        # Count this actor's dangerous actions across ALL monitored types in
        # the window -- a nuke mixes channel + role deletes, so we sum them.
        total = 0
        for kind in ACTION_LABELS:
            total += await self.bot.db.count_recent_actions(
                guild.id, member.id, kind, window_start
            )

        if total < config.action_threshold_count:
            return

        await self._punish(guild, member, action_type, total)

    async def _punish(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action_type: str,
        count: int,
    ) -> None:
        """Strip roles and ban a member who tripped the anti-nuke threshold."""
        label = ACTION_LABELS.get(action_type, action_type)
        reason = f"Anti-Nuke: attempted to {label} ({count} actions in window)"

        # Snapshot-before-destroy: capture current structure so a follow-up
        # /restore has something to work from (FEATURES.txt section 8).
        try:
            data = serialize_guild_structure(guild)
            await self.bot.db.save_snapshot(guild.id, trigger="antinuke", data=data)
            await self.bot.db.prune_old_snapshots(guild.id, keep=10)
        except Exception:
            log.exception("Failed to save pre-punish snapshot for guild %s", guild.id)

        stripped = False
        removable = [r for r in member.roles if not r.is_default() and not r.managed]
        if removable:
            try:
                await member.remove_roles(*removable, reason=reason)
                stripped = True
            except discord.Forbidden:
                log.warning("Missing perms to strip roles from %s", member.id)
            except discord.HTTPException:
                log.exception("Failed to strip roles from %s", member.id)

        banned = False
        try:
            await member.ban(reason=reason, delete_message_seconds=0)
            banned = True
        except discord.Forbidden:
            log.warning("Missing perms to ban %s", member.id)
        except discord.HTTPException:
            log.exception("Failed to ban %s", member.id)

        await self.bot.db.log_security_event(
            guild.id,
            member.id,
            action_type,
            severity="critical",
            detail=f"THRESHOLD TRIPPED: {reason}. Roles stripped={stripped}, banned={banned}",
            punished=True,
        )
        await self.bot.db.increment_threats_blocked(guild.id)

        outcome = []
        if stripped:
            outcome.append("roles stripped")
        if banned:
            outcome.append("banned")
        outcome_text = ", ".join(outcome) if outcome else "no action possible (missing permissions)"

        # Private security log (detailed).
        log_embed = build_security_embed(
            "THREAT NEUTRALIZED",
            f"**User:** {member} (`{member.id}`)\n"
            f"**Attempted:** {label}\n"
            f"**Actions in window:** {count}\n"
            f"**Response:** {outcome_text}",
            severity="critical",
        )
        await send_security_log(self.bot.db, guild, log_embed)

        # Public deterrent announcement (FEATURES.txt section 6).
        public_embed = build_security_embed(
            "THREAT NEUTRALIZED",
            f"**{member}** attempted to {label}.\n"
            f"Action reversed. User {outcome_text}.",
            severity="critical",
        )
        await send_public_alert(self.bot.db, guild, public_embed)

        # Instant owner DM for a critical event.
        dm_embed = build_security_embed(
            f"Critical Security Event in {guild.name}",
            f"**User:** {member} (`{member.id}`)\n"
            f"**Attempted:** {label} ({count} actions)\n"
            f"**Response:** {outcome_text}",
            severity="critical",
        )
        await alert_owner_dm(guild, dm_embed)

        log.warning(
            "Anti-nuke punished %s in guild %s (%s): %s",
            member.id, guild.id, action_type, outcome_text,
        )

    # ------------------------------------------------------------------ #
    # Audit-log resolution helpers
    # ------------------------------------------------------------------ #

    async def _recent_audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int | None = None,
    ) -> discord.Member | discord.User | None:
        """Find who performed the most recent matching audit-log action.

        Only entries created in the last ~10 seconds are considered, so we
        don't attribute a fresh gateway event to a stale audit entry.
        """
        if not guild.me.guild_permissions.view_audit_log:
            return None
        cutoff = time.time() - 10
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if entry.created_at.timestamp() < cutoff:
                    break
                if target_id is not None and entry.target is not None:
                    if getattr(entry.target, "id", None) != target_id:
                        continue
                return entry.user
        except discord.Forbidden:
            return None
        except discord.HTTPException:
            log.exception("Audit-log fetch failed for guild %s", guild.id)
        return None

    # ------------------------------------------------------------------ #
    # Gateway event listeners
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        actor = await self._recent_audit_actor(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id
        )
        await self._handle_dangerous_action(channel.guild, actor, CHANNEL_DELETE)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor = await self._recent_audit_actor(
            role.guild, discord.AuditLogAction.role_delete, role.id
        )
        await self._handle_dangerous_action(role.guild, actor, ROLE_DELETE)

    @commands.Cog.listener()
    async def on_member_ban(
        self, guild: discord.Guild, user: discord.User | discord.Member
    ) -> None:
        actor = await self._recent_audit_actor(
            guild, discord.AuditLogAction.ban, user.id
        )
        await self._handle_dangerous_action(guild, actor, BAN)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # A kick shows up as a member_remove with a matching audit entry.
        # A voluntary leave has no audit entry, so _recent_audit_actor returns
        # None and _handle_dangerous_action no-ops.
        actor = await self._recent_audit_actor(
            member.guild, discord.AuditLogAction.kick, member.id
        )
        if actor is None:
            return
        await self._handle_dangerous_action(member.guild, actor, KICK)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._recent_audit_actor(
            channel.guild, discord.AuditLogAction.webhook_create
        )
        await self._handle_dangerous_action(channel.guild, actor, WEBHOOK_CREATE)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # Detect a newly added bot (FEATURES.txt section 2: "අලුත් bot එකක් add").
        if not member.bot:
            return
        actor = await self._recent_audit_actor(
            member.guild, discord.AuditLogAction.bot_add, member.id
        )
        await self._handle_dangerous_action(member.guild, actor, BOT_ADD)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNuke(bot))
