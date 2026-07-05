"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


class Config:
    """Runtime configuration for the bot."""

    TOKEN: str = _require_env("DISCORD_TOKEN")

    # Optional: restrict slash-command sync to one guild for instant updates
    # during development. Leave unset for global sync (can take up to 1 hour
    # to propagate on first deploy).
    DEV_GUILD_ID: int | None = (
        int(os.environ["DEV_GUILD_ID"]) if os.getenv("DEV_GUILD_ID") else None
    )

    # Where the SQLite file lives. Defaults to ./data/bot.db for local runs.
    # On a host like Railway, mount a persistent volume and set DATA_DIR to its
    # mount path (e.g. /data) so warnings and settings survive restarts.
    DATABASE_PATH: Path = (
        Path(os.environ["DATA_DIR"]) if os.getenv("DATA_DIR") else BASE_DIR / "data"
    ) / "bot.db"

    # Max number of messages that can be purged in a single /purge invocation.
    MAX_PURGE_LIMIT: int = 100
