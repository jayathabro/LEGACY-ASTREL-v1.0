"""Moderation and server-maintenance commands.

All commands are Discord slash (application) commands. Permission checks are
enforced both by Discord (via app_commands.default_permissions, so the
command is hidden from members who lack permission) and defensively in code
(role hierarchy, self-target, bot-target) since Discord's UI-level hiding can
be bypassed by permission changes that haven't refreshed client-side yet.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    COLOR_SUCCESS,
    COLOR_WARN,
    HierarchyError,
    build_embed,
    ensure_can_moderate,
)


def _fmt_duration(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not days:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else "0s"


class Moderation(commands.Cog):
    """Kick, ban, timeout, warn, purge, channel lock, and mod-log configuration."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    async def _log_action(
        self,
        guild: discord.Guild,
        *,
        title: str,
        description: str,
        color: discord.Color = COLOR_WARN,
    ) -> None:
        config = await self.bot.db.get_guild_config(guild.id)
        if config.mod_log_channel_id is None:
            return
        channel = guild.get_channel(config.mod_log_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embed = build_embed(title, description, color)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass  # Bot lost access to the log channel; nothing else to do.

    def _check_hierarchy(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ) -> None:
        assert isinstance(interaction.user, discord.Member)
        assert interaction.guild is not None
        bot_member = interaction.guild.me
        assert bot_member is not None
        ensure_can_moderate(actor=interaction.user, target=target, bot_member=bot_member)

    # ------------------------------------------------------------------ #
    # Kick / Ban / Unban
    # ------------------------------------------------------------------ #

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ) -> None:
        try:
            self._check_hierarchy(interaction, member)
        except HierarchyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        try:
            await member.kick(reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to kick that member.", ephemeral=True
            )
            return

        embed = build_embed(
            "Member Kicked",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)
        assert interaction.guild is not None
        await self._log_action(
            interaction.guild,
            title="Member Kicked",
            description=embed.description or "",
        )

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.describe(
        member="Member to ban",
        reason="Reason for the ban",
        delete_message_days="Days of message history to delete (0-7)",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        try:
            self._check_hierarchy(interaction, member)
        except HierarchyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        try:
            await member.ban(
                reason=f"{interaction.user} ({interaction.user.id}): {reason}",
                delete_message_seconds=delete_message_days * 86400,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to ban that member.", ephemeral=True
            )
            return

        embed = build_embed(
            "Member Banned",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)
        assert interaction.guild is not None
        await self._log_action(
            interaction.guild,
            title="Member Banned",
            description=embed.description or "",
        )

    @app_commands.command(name="unban", description="Unban a user by their ID.")
    @app_commands.describe(user_id="The ID of the user to unban", reason="Reason for the unban")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "No reason provided",
    ) -> None:
        assert interaction.guild is not None
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("That's not a valid user ID.", ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(
                user, reason=f"{interaction.user} ({interaction.user.id}): {reason}"
            )
        except discord.NotFound:
            await interaction.response.send_message(
                "That user isn't banned or doesn't exist.", ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to unban users.", ephemeral=True
            )
            return

        embed = build_embed(
            "Member Unbanned",
            f"**User:** {user.mention} (`{user.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)
        await self._log_action(
            interaction.guild, title="Member Unbanned", description=embed.description or ""
        )

    # ------------------------------------------------------------------ #
    # Timeout
    # ------------------------------------------------------------------ #

    @app_commands.command(name="timeout", description="Time out a member (mute them temporarily).")
    @app_commands.describe(
        member="Member to time out",
        minutes="Duration in minutes (max 40320 = 28 days)",
        reason="Reason for the timeout",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "No reason provided",
    ) -> None:
        try:
            self._check_hierarchy(interaction, member)
        except HierarchyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        duration = dt.timedelta(minutes=minutes)
        try:
            await member.timeout(
                duration, reason=f"{interaction.user} ({interaction.user.id}): {reason}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to time out that member.", ephemeral=True
            )
            return

        embed = build_embed(
            "Member Timed Out",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Duration:** {_fmt_duration(int(duration.total_seconds()))}\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)
        assert interaction.guild is not None
        await self._log_action(
            interaction.guild, title="Member Timed Out", description=embed.description or ""
        )

    @app_commands.command(name="untimeout", description="Remove an active timeout from a member.")
    @app_commands.describe(member="Member to remove the timeout from")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        if member.timed_out_until is None:
            await interaction.response.send_message(
                f"{member.mention} is not currently timed out.", ephemeral=True
            )
            return
        try:
            await member.timeout(
                None, reason=f"Timeout removed by {interaction.user} ({interaction.user.id})"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to modify that member's timeout.", ephemeral=True
            )
            return

        embed = build_embed(
            "Timeout Removed",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {interaction.user.mention}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ #
    # Warnings
    # ------------------------------------------------------------------ #

    warn_group = app_commands.Group(
        name="warn", description="Manage member warnings.", guild_only=True
    )

    @warn_group.command(name="add", description="Warn a member.")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    @app_commands.default_permissions(moderate_members=True)
    async def warn_add(
        self, interaction: discord.Interaction, member: discord.Member, reason: str
    ) -> None:
        try:
            self._check_hierarchy(interaction, member)
        except HierarchyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        assert interaction.guild is not None
        warning_id = await self.bot.db.add_warning(
            interaction.guild.id, member.id, interaction.user.id, reason
        )
        count = len(await self.bot.db.get_warnings(interaction.guild.id, member.id))

        embed = build_embed(
            "Member Warned",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Warning ID:** {warning_id} | **Total warnings:** {count}",
            COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed)
        await self._log_action(
            interaction.guild, title="Member Warned", description=embed.description or ""
        )

        try:
            await member.send(
                f"You were warned in **{interaction.guild.name}** for: {reason}"
            )
        except discord.Forbidden:
            pass  # Member has DMs disabled; the in-server log is authoritative.

    @warn_group.command(name="list", description="List a member's warnings.")
    @app_commands.describe(member="Member to check")
    @app_commands.default_permissions(moderate_members=True)
    async def warn_list(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        assert interaction.guild is not None
        warnings = await self.bot.db.get_warnings(interaction.guild.id, member.id)
        if not warnings:
            await interaction.response.send_message(
                f"{member.mention} has no warnings.", ephemeral=True
            )
            return

        lines = [
            f"`#{w.id}` <t:{w.created_at}:R> by <@{w.moderator_id}>: {w.reason}"
            for w in warnings[:25]
        ]
        embed = build_embed(
            f"Warnings for {member}",
            "\n".join(lines),
            COLOR_WARN,
        )
        if len(warnings) > 25:
            embed.set_footer(text=f"Showing 25 of {len(warnings)} warnings.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @warn_group.command(name="remove", description="Remove a single warning by its ID.")
    @app_commands.describe(warning_id="The warning ID shown in /warn list")
    @app_commands.default_permissions(moderate_members=True)
    async def warn_remove(
        self, interaction: discord.Interaction, warning_id: int
    ) -> None:
        assert interaction.guild is not None
        removed = await self.bot.db.remove_warning(interaction.guild.id, warning_id)
        if removed:
            await interaction.response.send_message(
                f"Removed warning `#{warning_id}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No warning with ID `#{warning_id}` was found in this server.",
                ephemeral=True,
            )

    @warn_group.command(name="clear", description="Clear all warnings for a member.")
    @app_commands.describe(member="Member whose warnings should be cleared")
    @app_commands.default_permissions(moderate_members=True)
    async def warn_clear(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        assert interaction.guild is not None
        count = await self.bot.db.clear_warnings(interaction.guild.id, member.id)
        await interaction.response.send_message(
            f"Cleared {count} warning(s) for {member.mention}.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Purge
    # ------------------------------------------------------------------ #

    @app_commands.command(name="purge", description="Bulk-delete recent messages in this channel.")
    @app_commands.describe(
        amount="Number of messages to delete (1-100)",
        member="Only delete messages from this member",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
        member: discord.Member | None = None,
    ) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        def check(msg: discord.Message) -> bool:
            return member is None or msg.author.id == member.id

        try:
            deleted = await interaction.channel.purge(limit=amount, check=check)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to delete messages here.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Deleted {len(deleted)} message(s).", ephemeral=True
        )
        assert interaction.guild is not None
        target_note = f" from {member.mention}" if member else ""
        await self._log_action(
            interaction.guild,
            title="Messages Purged",
            description=(
                f"**Channel:** {interaction.channel.mention}\n"
                f"**Deleted:** {len(deleted)} message(s){target_note}\n"
                f"**Moderator:** {interaction.user.mention}"
            ),
        )

    # ------------------------------------------------------------------ #
    # Channel lock / unlock
    # ------------------------------------------------------------------ #

    @app_commands.command(name="lock", description="Prevent @everyone from sending messages in this channel.")
    @app_commands.describe(reason="Reason for locking the channel")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def lock(
        self, interaction: discord.Interaction, reason: str = "No reason provided"
    ) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        assert interaction.guild is not None
        everyone = interaction.guild.default_role
        overwrite = interaction.channel.overwrites_for(everyone)
        overwrite.send_messages = False
        try:
            await interaction.channel.set_permissions(
                everyone,
                overwrite=overwrite,
                reason=f"{interaction.user} ({interaction.user.id}): {reason}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to edit this channel's permissions.", ephemeral=True
            )
            return

        embed = build_embed(
            "Channel Locked",
            f"**Channel:** {interaction.channel.mention}\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}",
            COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed)
        await self._log_action(
            interaction.guild, title="Channel Locked", description=embed.description or ""
        )

    @app_commands.command(name="unlock", description="Allow @everyone to send messages in this channel again.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def unlock(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        assert interaction.guild is not None
        everyone = interaction.guild.default_role
        overwrite = interaction.channel.overwrites_for(everyone)
        overwrite.send_messages = None
        try:
            await interaction.channel.set_permissions(
                everyone,
                overwrite=overwrite,
                reason=f"Unlocked by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to edit this channel's permissions.", ephemeral=True
            )
            return

        embed = build_embed(
            "Channel Unlocked",
            f"**Channel:** {interaction.channel.mention}\n"
            f"**Moderator:** {interaction.user.mention}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)
        await self._log_action(
            interaction.guild, title="Channel Unlocked", description=embed.description or ""
        )

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    @app_commands.command(name="setmodlog", description="Set the channel where moderation actions are logged.")
    @app_commands.describe(channel="Text channel to send mod-log messages to")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setmodlog(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_mod_log_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            f"Mod-log channel set to {channel.mention}.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Cog-level error handling for app_commands
    # ------------------------------------------------------------------ #

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You don't have permission to use this command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "I don't have the permissions required to do that."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used in a server."
        else:
            message = "Something went wrong while running that command."
            raise error

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    # Note: warn_group is an app_commands.Group defined as a class attribute,
    # so discord.py auto-registers it to the tree when the cog is added.
    # Do not also call bot.tree.add_command() for it -- that would register
    # it twice and raise CommandAlreadyRegistered.
    await bot.add_cog(Moderation(bot))
