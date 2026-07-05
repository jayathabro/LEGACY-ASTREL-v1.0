"""Backup & recovery (FEATURES.txt section 4).

Periodically snapshots the server's structure (roles, categories, channels)
to the database. A snapshot can be replayed with /restore to recreate deleted
roles and channels after a nuke. Message history is NOT restorable — that's a
Discord API limitation, stated honestly in the command output.
"""
from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import (
    COLOR_SUCCESS,
    build_embed,
    build_security_embed,
    serialize_guild_structure,
)

log = logging.getLogger("bot.backup")

SNAPSHOT_INTERVAL_HOURS = 6


class Backup(commands.Cog):
    """Periodic structural snapshots plus manual backup/restore commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.periodic_snapshot.start()

    async def cog_unload(self) -> None:
        self.periodic_snapshot.cancel()

    @tasks.loop(hours=SNAPSHOT_INTERVAL_HOURS)
    async def periodic_snapshot(self) -> None:
        for guild in self.bot.guilds:
            try:
                data = serialize_guild_structure(guild)
                await self.bot.db.save_snapshot(guild.id, trigger="periodic", data=data)
                await self.bot.db.prune_old_snapshots(guild.id, keep=10)
            except Exception:
                log.exception("Periodic snapshot failed for guild %s", guild.id)

    @periodic_snapshot.before_loop
    async def _before_snapshot(self) -> None:
        await self.bot.wait_until_ready()

    backup_group = app_commands.Group(
        name="backup",
        description="Server structure backup and recovery.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @backup_group.command(name="now", description="Take a snapshot of the server structure now.")
    async def backup_now(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        data = serialize_guild_structure(interaction.guild)
        await self.bot.db.save_snapshot(interaction.guild.id, trigger="manual", data=data)
        await self.bot.db.prune_old_snapshots(interaction.guild.id, keep=10)
        parsed = json.loads(data)
        await interaction.followup.send(
            f"Snapshot saved: {len(parsed['roles'])} role(s), "
            f"{len(parsed['channels'])} channel(s).",
            ephemeral=True,
        )

    @backup_group.command(
        name="info", description="Show details of the most recent snapshot."
    )
    async def backup_info(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        snapshot = await self.bot.db.get_latest_snapshot(interaction.guild.id)
        if snapshot is None:
            await interaction.response.send_message(
                "No snapshots have been taken yet.", ephemeral=True
            )
            return
        parsed = json.loads(snapshot.data)
        embed = build_embed(
            "Latest Snapshot",
            f"**Taken:** <t:{snapshot.created_at}:R>\n"
            f"**Trigger:** {snapshot.trigger}\n"
            f"**Roles:** {len(parsed['roles'])}\n"
            f"**Channels:** {len(parsed['channels'])}",
            COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @backup_group.command(
        name="restore",
        description="Recreate missing roles and channels from the latest snapshot.",
    )
    async def backup_restore(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        # Restore is owner-only: it creates roles/channels and is high-impact.
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Only the server owner can run a restore.", ephemeral=True
            )
            return

        snapshot = await self.bot.db.get_latest_snapshot(guild.id)
        if snapshot is None:
            await interaction.response.send_message(
                "No snapshot available to restore from.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        parsed = json.loads(snapshot.data)

        existing_role_names = {r.name for r in guild.roles}
        existing_channel_names = {c.name for c in guild.channels}

        roles_created = 0
        for role_data in reversed(parsed["roles"]):  # low->high so positions stack up
            if role_data["name"] in existing_role_names:
                continue
            try:
                await guild.create_role(
                    name=role_data["name"],
                    permissions=discord.Permissions(role_data["permissions"]),
                    colour=discord.Colour(role_data["color"]),
                    hoist=role_data["hoist"],
                    mentionable=role_data["mentionable"],
                    reason="Restore from snapshot",
                )
                roles_created += 1
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Failed to recreate role %s", role_data["name"])

        channels_created = 0
        for channel_data in sorted(parsed["channels"], key=lambda c: c["position"]):
            if channel_data["name"] in existing_channel_names:
                continue
            try:
                ctype = discord.ChannelType(channel_data["type"])
                if ctype == discord.ChannelType.category:
                    await guild.create_category(
                        channel_data["name"], reason="Restore from snapshot"
                    )
                    channels_created += 1
                elif ctype == discord.ChannelType.voice:
                    await guild.create_voice_channel(
                        channel_data["name"], reason="Restore from snapshot"
                    )
                    channels_created += 1
                else:
                    await guild.create_text_channel(
                        channel_data["name"],
                        topic=channel_data.get("topic"),
                        nsfw=channel_data.get("nsfw", False),
                        reason="Restore from snapshot",
                    )
                    channels_created += 1
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Failed to recreate channel %s", channel_data["name"])

        embed = build_security_embed(
            "Restore Complete",
            f"**Roles recreated:** {roles_created}\n"
            f"**Channels recreated:** {channels_created}\n\n"
            "_Note: message history cannot be restored (Discord API limitation). "
            "Recreated channels/roles may need permissions and positions adjusted._",
            severity="info",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Backup(bot))
