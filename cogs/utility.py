"""General-purpose utility and information commands."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils import COLOR_INFO, build_embed


class Utility(commands.Cog):
    """Ping, server info, user info, and avatar lookups."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        embed = build_embed("Pong!", f"Latency: **{latency_ms}ms**", COLOR_INFO)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Show information about this server.")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        embed = build_embed(guild.name, color=COLOR_INFO)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(
            name="Channels",
            value=f"{text_channels} text / {voice_channels} voice",
            inline=True,
        )
        embed.add_field(
            name="Created",
            value=f"<t:{int(guild.created_at.timestamp())}:D>",
            inline=True,
        )
        embed.add_field(
            name="Boost Level", value=f"Level {guild.premium_tier}", inline=True
        )
        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Show information about a member.")
    @app_commands.describe(member="Member to look up (defaults to yourself)")
    @app_commands.guild_only()
    async def userinfo(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        assert isinstance(target, discord.Member)

        roles = [role.mention for role in reversed(target.roles) if not role.is_default()]
        roles_text = ", ".join(roles[:15]) if roles else "None"
        if len(roles) > 15:
            roles_text += f" (+{len(roles) - 15} more)"

        embed = build_embed(str(target), color=target.color or COLOR_INFO)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Joined Server",
            value=f"<t:{int(target.joined_at.timestamp())}:R>" if target.joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(target.created_at.timestamp())}:R>",
            inline=True,
        )
        embed.add_field(name="Top Role", value=target.top_role.mention, inline=True)
        embed.add_field(name=f"Roles [{len(roles)}]", value=roles_text, inline=False)
        embed.set_footer(text=f"User ID: {target.id}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Get a member's avatar.")
    @app_commands.describe(member="Member to look up (defaults to yourself)")
    async def avatar(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        embed = build_embed(f"{target}'s Avatar", color=COLOR_INFO)
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
