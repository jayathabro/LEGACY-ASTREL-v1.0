"""Entrypoint: builds the bot, loads cogs, syncs slash commands, and runs it."""
from __future__ import annotations

import asyncio
import itertools
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Config
from database import Database
from utils import build_security_embed, send_security_log

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

INITIAL_EXTENSIONS = (
    "cogs.moderation",
    "cogs.utility",
    "cogs.antinuke",
    "cogs.antiraid",
    "cogs.verification",
    "cogs.backup",
    "cogs.security",
    "cogs.presence",
)

# Rotating security-themed presence lines. Each is shown as "Watching <text>".
# They cycle every ROTATION_SECONDS to make the bot read as an always-on
# security system standing guard over the server.
SECURITY_STATUS_LINES = (
    "for threats",
    "Anti-Nuke armed",
    "Anti-Raid active",
    "for suspicious joins",
    "the audit logs",
    "every channel",
    "for mass-bans",
    "the member gate",
    "24/7 protection online",
    "the server perimeter",
)
STATUS_ROTATION_SECONDS = 30


class MaintainBot(commands.Bot):
    """Server-maintenance Discord bot with SQLite-backed state."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # Required for kick/ban/timeout targets and userinfo.
        intents.message_content = False  # Not needed: this bot is slash-command only.

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.db = Database(Config.DATABASE_PATH)
        self._startup_announced = False
        self._status_cycle = itertools.cycle(SECURITY_STATUS_LINES)
        self.tree.on_error = self._on_app_command_error

    async def _on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Global slash-command error handler.

        On a permission denial, record the attempt so the Security cog can flag
        users who repeatedly probe restricted commands (FEATURES.txt section 8).
        Cog-local handlers (e.g. Moderation.cog_app_command_error) still run for
        their own commands; this catches the rest and the tracking concern.
        """
        if isinstance(error, app_commands.MissingPermissions):
            if interaction.guild is not None and interaction.command is not None:
                security = self.get_cog("Security")
                if security is not None:
                    await security.record_failed_attempt(
                        interaction.guild,
                        interaction.user.id,
                        interaction.command.qualified_name,
                    )
            message = "You don't have permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used in a server."
        else:
            log.exception("Unhandled app command error", exc_info=error)
            message = "Something went wrong while running that command."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("Database connected at %s", Config.DATABASE_PATH)

        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
                log.info("Loaded extension: %s", extension)
            except Exception:
                log.exception("Failed to load extension: %s", extension)
                raise

        if Config.DEV_GUILD_ID is not None:
            guild = discord.Object(id=Config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d command(s) to dev guild %s", len(synced), Config.DEV_GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d command(s) globally (may take up to 1 hour to appear)", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("Connected to %d guild(s)", len(self.guilds))
        if not self._rotate_status.is_running():
            self._rotate_status.start()
        # Announce "systems armed" to each guild's security-log channel once
        # per process start (FEATURES.txt section 6, Strong Visual Identity).
        if not self._startup_announced:
            self._startup_announced = True
            embed = build_security_embed(
                "Legacy Astrel v.1.0 is now online",
                "All systems armed. Anti-nuke and anti-raid protection active.",
                severity="info",
            )
            for guild in self.guilds:
                await send_security_log(self.db, guild, embed)

    @tasks.loop(seconds=STATUS_ROTATION_SECONDS)
    async def _rotate_status(self) -> None:
        """Cycle the bot's 'Watching …' presence through security lines."""
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=next(self._status_cycle),
            )
        )

    @_rotate_status.before_loop
    async def _before_rotate_status(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        self._rotate_status.cancel()
        await self.db.close()
        await super().close()


async def main() -> None:
    bot = MaintainBot()
    async with bot:
        await bot.start(Config.TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutdown requested by user.")
        sys.exit(0)
