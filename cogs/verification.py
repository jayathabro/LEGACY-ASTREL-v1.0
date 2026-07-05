"""Verification gate (FEATURES.txt section 3).

New members must click a button to receive the configured "verified" role and
gain access to the server's channels. A persistent view survives bot restarts
by using a fixed custom_id, so the button in an old message keeps working.

Server setup (channel permissions so unverified members can only see the
verification channel) is left to the server admin; this cog handles the role
grant and pending-state tracking.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    COLOR_SUCCESS,
    build_embed,
    build_security_embed,
)

log = logging.getLogger("bot.verification")

VERIFY_BUTTON_CUSTOM_ID = "legacy_astrel:verify"


class VerifyView(discord.ui.View):
    """Persistent single-button view that grants the verified role."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)  # Persistent view: never times out.
        self.bot = bot

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        custom_id=VERIFY_BUTTON_CUSTOM_ID,
    )
    async def verify(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Verification only works inside a server.", ephemeral=True
            )
            return

        config = await self.bot.db.get_guild_config(guild.id)
        if config.verified_role_id is None:
            await interaction.response.send_message(
                "Verification isn't fully configured yet. Ask an admin to run "
                "`/verification setup`.",
                ephemeral=True,
            )
            return

        role = guild.get_role(config.verified_role_id)
        if role is None:
            await interaction.response.send_message(
                "The verified role no longer exists. Ask an admin to reconfigure.",
                ephemeral=True,
            )
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                "You're already verified.", ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(role, reason="Passed verification gate")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't assign the verified role. My role may be below it.",
                ephemeral=True,
            )
            return

        await self.bot.db.remove_pending_verification(guild.id, interaction.user.id)
        await interaction.response.send_message(
            "You're verified. Welcome!", ephemeral=True
        )


class Verification(commands.Cog):
    """Button-based verification gate and its setup command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.bot.db.get_guild_config(member.guild.id)
        if config.verification_enabled:
            await self.bot.db.add_pending_verification(member.guild.id, member.id)

    verification_group = app_commands.Group(
        name="verification",
        description="Configure the verification gate.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @verification_group.command(
        name="setup", description="Post the verification button and enable the gate."
    )
    @app_commands.describe(
        channel="Channel where the verification message is posted",
        verified_role="Role granted when a member verifies",
    )
    async def setup_verification(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        verified_role: discord.Role,
    ) -> None:
        assert interaction.guild is not None

        if verified_role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "That role is above my highest role, so I couldn't assign it. "
                "Move my role above it and try again.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_verification_channel(interaction.guild.id, channel.id)
        await self.bot.db.set_verified_role(interaction.guild.id, verified_role.id)
        await self.bot.db.set_verification_enabled(interaction.guild.id, True)

        embed = build_security_embed(
            "Server Verification Required",
            "Click the button below to verify and gain access to the server.\n\n"
            "This server is protected by Legacy Astrel v.1.0.",
            severity="info",
        )
        try:
            await channel.send(embed=embed, view=VerifyView(self.bot))
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't send messages in {channel.mention}.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Verification gate enabled. Button posted in {channel.mention}, "
            f"granting {verified_role.mention}.",
            ephemeral=True,
        )

    @verification_group.command(
        name="disable", description="Disable the verification gate."
    )
    async def disable_verification(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.db.set_verification_enabled(interaction.guild.id, False)
        await interaction.response.send_message(
            "Verification gate disabled.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    # Register the persistent view so buttons keep working after a restart.
    bot.add_view(VerifyView(bot))
    await bot.add_cog(Verification(bot))
