"""Security control panel, status dashboard, and visible presence.

Covers FEATURES.txt sections 5 (logging & alerts), 6 (visible presence /
psychological deterrent), and 8 (admin-side extras):

- /securitystatus  public protection dashboard
- /security        admin config group (channels, toggles, thresholds)
- /trust           bot-level trusted-admin whitelist management
- /lockdown        panic button (lock/lift all text channels)
- /freeze          emergency mode: suspend dangerous perms via bot-level trust
- weekly digest DM to the owner
- failed-permission tracking (flag users probing restricted commands)
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    COLOR_INFO,
    COLOR_SUCCESS,
    build_embed,
    build_security_embed,
    alert_owner_dm,
    send_security_log,
)

log = logging.getLogger("bot.security")

FAILED_ATTEMPT_WINDOW_SECONDS = 300
FAILED_ATTEMPT_ALERT_THRESHOLD = 3


class Security(commands.Cog):
    """Security configuration, status dashboard, panic controls, and digest."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.weekly_digest.start()

    async def cog_unload(self) -> None:
        self.weekly_digest.cancel()

    # ------------------------------------------------------------------ #
    # Public status dashboard (FEATURES.txt section 6)
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="securitystatus",
        description="Show this server's protection dashboard.",
    )
    @app_commands.guild_only()
    async def securitystatus(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        config = await self.bot.db.get_guild_config(guild.id)

        def onoff(flag: bool) -> str:
            return "🟢 ACTIVE" if flag else "🔴 OFF"

        frozen = (
            config.frozen_until is not None and config.frozen_until > int(time.time())
        )
        level = "MAXIMUM" if (config.antinuke_enabled and config.antiraid_enabled) else "PARTIAL"

        embed = build_security_embed(
            f"Protection Status: {level}",
            f"Security dashboard for **{guild.name}**",
            severity="info",
        )
        embed.add_field(name="Anti-Nuke", value=onoff(config.antinuke_enabled), inline=True)
        embed.add_field(name="Anti-Raid", value=onoff(config.antiraid_enabled), inline=True)
        embed.add_field(
            name="Verification Gate",
            value=onoff(config.verification_enabled),
            inline=True,
        )
        embed.add_field(
            name="Threats Blocked (week)",
            value=str(config.threats_blocked_week),
            inline=True,
        )
        embed.add_field(
            name="Threats Blocked (total)",
            value=str(config.threats_blocked_total),
            inline=True,
        )
        embed.add_field(
            name="Emergency Freeze",
            value="🧊 ENGAGED" if frozen else "off",
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ #
    # Config group: channels, toggles, thresholds
    # ------------------------------------------------------------------ #

    security_group = app_commands.Group(
        name="security",
        description="Configure the security system.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @security_group.command(
        name="setlog", description="Set the private security-log channel."
    )
    @app_commands.describe(channel="Channel for detailed security logs")
    async def set_log(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_security_log_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            f"Security-log channel set to {channel.mention}.", ephemeral=True
        )

    @security_group.command(
        name="setalerts", description="Set the public security-alerts channel."
    )
    @app_commands.describe(channel="Channel for public threat announcements")
    async def set_alerts(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_alerts_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            f"Public alerts channel set to {channel.mention}.", ephemeral=True
        )

    @security_group.command(name="antinuke", description="Enable or disable anti-nuke.")
    @app_commands.describe(enabled="Turn anti-nuke on or off")
    async def toggle_antinuke(
        self, interaction: discord.Interaction, enabled: bool
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_antinuke_enabled(interaction.guild.id, enabled)
        await interaction.response.send_message(
            f"Anti-nuke is now **{'ON' if enabled else 'OFF'}**.", ephemeral=True
        )

    @security_group.command(name="antiraid", description="Enable or disable anti-raid.")
    @app_commands.describe(enabled="Turn anti-raid on or off")
    async def toggle_antiraid(
        self, interaction: discord.Interaction, enabled: bool
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_antiraid_enabled(interaction.guild.id, enabled)
        await interaction.response.send_message(
            f"Anti-raid is now **{'ON' if enabled else 'OFF'}**.", ephemeral=True
        )

    @security_group.command(
        name="minage", description="Set the minimum account age (hours) for new joins."
    )
    @app_commands.describe(hours="Minimum account age in hours (e.g. 168 = 7 days)")
    async def set_min_age(
        self,
        interaction: discord.Interaction,
        hours: app_commands.Range[int, 0, 8760],
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_min_account_age_hours(interaction.guild.id, hours)
        await interaction.response.send_message(
            f"Minimum account age set to **{hours}h**.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Trusted-admin whitelist (FEATURES.txt section 2 / 8)
    # ------------------------------------------------------------------ #

    trust_group = app_commands.Group(
        name="trust",
        description="Manage the bot-level trusted-admin whitelist.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @trust_group.command(name="add", description="Add a trusted admin (anti-nuke exempt).")
    @app_commands.describe(member="Member to trust")
    async def trust_add(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        # Only the owner can grant trust: it exempts a member from anti-nuke.
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Only the server owner can manage the trust list.", ephemeral=True
            )
            return
        await self.bot.db.add_trusted_admin(guild.id, member.id, interaction.user.id)
        await interaction.response.send_message(
            f"{member.mention} is now a trusted admin (exempt from anti-nuke).",
            ephemeral=True,
        )

    @trust_group.command(name="remove", description="Remove a trusted admin.")
    @app_commands.describe(member="Member to remove from the trust list")
    async def trust_remove(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Only the server owner can manage the trust list.", ephemeral=True
            )
            return
        removed = await self.bot.db.remove_trusted_admin(guild.id, member.id)
        msg = (
            f"Removed {member.mention} from the trust list."
            if removed
            else f"{member.mention} was not on the trust list."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @trust_group.command(name="list", description="List all trusted admins.")
    async def trust_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        ids = await self.bot.db.list_trusted_admins(guild.id)
        if not ids:
            await interaction.response.send_message(
                "No trusted admins configured. The server owner is always trusted.",
                ephemeral=True,
            )
            return
        lines = "\n".join(f"<@{uid}> (`{uid}`)" for uid in ids)
        embed = build_embed("Trusted Admins", lines, COLOR_INFO)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Panic button: lockdown (FEATURES.txt section 5)
    # ------------------------------------------------------------------ #

    lockdown_group = app_commands.Group(
        name="lockdown",
        description="Server-wide panic lock controls.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @lockdown_group.command(
        name="engage", description="Instantly lock all text channels."
    )
    async def lockdown_engage(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True)
        locked = await self._set_all_channels_locked(guild, locked=True)

        embed = build_security_embed(
            "SERVER LOCKED DOWN",
            f"All channels locked by {interaction.user.mention}.\n"
            f"Locked **{locked}** channel(s). Run `/lockdown lift` to reverse.",
            severity="critical",
        )
        await send_security_log(self.bot.db, guild, embed)
        await interaction.followup.send(
            f"Locked {locked} channel(s).", ephemeral=True
        )

    @lockdown_group.command(name="lift", description="Unlock all text channels.")
    async def lockdown_lift(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True)
        unlocked = await self._set_all_channels_locked(guild, locked=False)

        antiraid = self.bot.get_cog("AntiRaid")
        if antiraid is not None:
            antiraid.clear_lockdown_flag(guild.id)

        await interaction.followup.send(
            f"Unlocked {unlocked} channel(s).", ephemeral=True
        )

    async def _set_all_channels_locked(
        self, guild: discord.Guild, *, locked: bool
    ) -> int:
        everyone = guild.default_role
        changed = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(everyone)
            overwrite.send_messages = False if locked else None
            try:
                await channel.set_permissions(
                    everyone,
                    overwrite=overwrite,
                    reason="Lockdown engage" if locked else "Lockdown lift",
                )
                changed += 1
            except (discord.Forbidden, discord.HTTPException):
                continue
        return changed

    # ------------------------------------------------------------------ #
    # Emergency freeze (FEATURES.txt section 8)
    # ------------------------------------------------------------------ #

    freeze_group = app_commands.Group(
        name="freeze",
        description="Emergency freeze: suspend admin trust during a suspected hack.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @freeze_group.command(
        name="on", description="Engage emergency freeze (owner only)."
    )
    @app_commands.describe(minutes="How long to stay frozen (default 30)")
    async def freeze_on(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 1440] = 30,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Only the server owner can engage emergency freeze.", ephemeral=True
            )
            return
        until = int(time.time()) + minutes * 60
        await self.bot.db.set_freeze(guild.id, until, interaction.user.id)

        embed = build_security_embed(
            "EMERGENCY FREEZE ENGAGED",
            f"Freeze active for **{minutes} minute(s)**.\n"
            "During freeze, only the owner and the bot are treated as trusted. "
            "Any non-trusted admin performing a dangerous action is punished "
            "immediately, regardless of the normal threshold.",
            severity="critical",
        )
        await send_security_log(self.bot.db, guild, embed)
        await interaction.response.send_message(
            f"Emergency freeze engaged for {minutes} minute(s).", ephemeral=True
        )

    @freeze_group.command(name="off", description="Lift emergency freeze (owner only).")
    async def freeze_off(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Only the server owner can lift emergency freeze.", ephemeral=True
            )
            return
        await self.bot.db.set_freeze(guild.id, None, None)
        await interaction.response.send_message(
            "Emergency freeze lifted.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Failed-permission tracker (FEATURES.txt section 8)
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_app_command_completion(self, *_args) -> None:  # noqa: D401
        # Placeholder so completion doesn't get mistaken for a failure. Actual
        # failure tracking happens in the global error handler in bot.py, which
        # calls record_failed_attempt below.
        return

    async def record_failed_attempt(
        self, guild: discord.Guild, user_id: int, command_name: str
    ) -> None:
        """Record a permission-denied command attempt and alert on a pattern."""
        await self.bot.db.log_failed_permission_attempt(guild.id, user_id, command_name)
        since = int(time.time()) - FAILED_ATTEMPT_WINDOW_SECONDS
        count = await self.bot.db.count_recent_failed_attempts(guild.id, user_id, since)
        if count == FAILED_ATTEMPT_ALERT_THRESHOLD:
            embed = build_security_embed(
                "Repeated Restricted-Command Attempts",
                f"<@{user_id}> (`{user_id}`) has tried restricted commands "
                f"**{count} times** in the last "
                f"{FAILED_ATTEMPT_WINDOW_SECONDS // 60} minutes.",
                severity="medium",
            )
            await send_security_log(self.bot.db, guild, embed)
            await alert_owner_dm(guild, embed)

    # ------------------------------------------------------------------ #
    # Weekly digest DM (FEATURES.txt section 8)
    # ------------------------------------------------------------------ #

    @tasks.loop(hours=24 * 7)
    async def weekly_digest(self) -> None:
        for guild in self.bot.guilds:
            try:
                config = await self.bot.db.get_guild_config(guild.id)
                events = await self.bot.db.get_recent_security_events(guild.id, limit=10)
                lines = "\n".join(
                    f"• <t:{e.created_at}:d> `{e.action_type}` ({e.severity})"
                    for e in events
                ) or "No security events this week."
                embed = build_security_embed(
                    f"Weekly Security Digest — {guild.name}",
                    f"**Threats blocked (total):** {config.threats_blocked_total}\n"
                    f"**Threats blocked (this week):** {config.threats_blocked_week}\n\n"
                    f"**Recent events:**\n{lines}",
                    severity="info",
                )
                await alert_owner_dm(guild, embed)
            except Exception:
                log.exception("Weekly digest failed for guild %s", guild.id)

    @weekly_digest.before_loop
    async def _before_digest(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))
