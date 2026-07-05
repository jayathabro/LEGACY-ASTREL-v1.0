"""Async SQLite persistence layer for warnings and per-guild configuration.

A single Database instance is created in bot.py, opened in setup_hook, and
closed on shutdown. It is attached to the bot as `bot.db` and shared by all
cogs so there is exactly one connection pool for the process.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
    ON warnings (guild_id, user_id);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    mod_log_channel_id INTEGER,
    mute_role_id INTEGER,
    security_log_channel_id INTEGER,
    alerts_channel_id INTEGER,
    verification_channel_id INTEGER,
    verified_role_id INTEGER,
    antinuke_enabled INTEGER NOT NULL DEFAULT 1,
    antiraid_enabled INTEGER NOT NULL DEFAULT 1,
    verification_enabled INTEGER NOT NULL DEFAULT 0,
    min_account_age_hours INTEGER NOT NULL DEFAULT 168,
    action_threshold_count INTEGER NOT NULL DEFAULT 3,
    action_threshold_seconds INTEGER NOT NULL DEFAULT 10,
    join_rate_count INTEGER NOT NULL DEFAULT 5,
    join_rate_seconds INTEGER NOT NULL DEFAULT 10,
    frozen_until INTEGER,
    frozen_by INTEGER,
    threats_blocked_total INTEGER NOT NULL DEFAULT 0,
    threats_blocked_week INTEGER NOT NULL DEFAULT 0,
    week_reset_at INTEGER
);

CREATE TABLE IF NOT EXISTS trusted_admins (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    added_by INTEGER NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    punished INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_events_guild_user_time
    ON security_events (guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS failed_permission_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    command_name TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failed_perm_guild_user_time
    ON failed_permission_attempts (guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS guild_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_guild_time
    ON guild_snapshots (guild_id, created_at);

CREATE TABLE IF NOT EXISTS verification_pending (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
"""


@dataclass(frozen=True, slots=True)
class Warning:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: int


@dataclass(frozen=True, slots=True)
class GuildConfig:
    guild_id: int
    mod_log_channel_id: int | None
    mute_role_id: int | None
    security_log_channel_id: int | None = None
    alerts_channel_id: int | None = None
    verification_channel_id: int | None = None
    verified_role_id: int | None = None
    antinuke_enabled: bool = True
    antiraid_enabled: bool = True
    verification_enabled: bool = False
    min_account_age_hours: int = 168
    action_threshold_count: int = 3
    action_threshold_seconds: int = 10
    join_rate_count: int = 5
    join_rate_seconds: int = 10
    frozen_until: int | None = None
    frozen_by: int | None = None
    threats_blocked_total: int = 0
    threats_blocked_week: int = 0
    week_reset_at: int | None = None


@dataclass(frozen=True, slots=True)
class SecurityEvent:
    id: int
    guild_id: int
    user_id: int
    action_type: str
    severity: str
    detail: str
    punished: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class GuildSnapshot:
    id: int
    guild_id: int
    trigger: str
    data: str
    created_at: int


class Database:
    """Thin async wrapper around a single aiosqlite connection."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() must be called before use.")
        return self._conn

    # ---------------------------------------------------------------- #
    # Warnings
    # ---------------------------------------------------------------- #

    async def add_warning(
        self, guild_id: int, user_id: int, moderator_id: int, reason: str
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason, int(time.time())),
        )
        await self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_warnings(self, guild_id: int, user_id: int) -> list[Warning]:
        cursor = await self.conn.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, reason, created_at
            FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id, user_id),
        )
        rows = await cursor.fetchall()
        return [Warning(**dict(row)) for row in rows]

    async def remove_warning(self, guild_id: int, warning_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND id = ?",
            (guild_id, warning_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount

    # ---------------------------------------------------------------- #
    # Guild configuration
    # ---------------------------------------------------------------- #

    _GUILD_CONFIG_COLUMNS = (
        "guild_id, mod_log_channel_id, mute_role_id, security_log_channel_id, "
        "alerts_channel_id, verification_channel_id, verified_role_id, "
        "antinuke_enabled, antiraid_enabled, verification_enabled, "
        "min_account_age_hours, action_threshold_count, action_threshold_seconds, "
        "join_rate_count, join_rate_seconds, frozen_until, frozen_by, "
        "threats_blocked_total, threats_blocked_week, week_reset_at"
    )

    @staticmethod
    def _row_to_guild_config(row: aiosqlite.Row) -> GuildConfig:
        data = dict(row)
        data["antinuke_enabled"] = bool(data["antinuke_enabled"])
        data["antiraid_enabled"] = bool(data["antiraid_enabled"])
        data["verification_enabled"] = bool(data["verification_enabled"])
        return GuildConfig(**data)

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        cursor = await self.conn.execute(
            f"SELECT {self._GUILD_CONFIG_COLUMNS} FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return GuildConfig(guild_id=guild_id, mod_log_channel_id=None, mute_role_id=None)
        return self._row_to_guild_config(row)

    async def _upsert_guild_config_column(self, guild_id: int, column: str, value: object) -> None:
        # `column` is always a hardcoded literal from a caller below, never
        # user input, so building the SQL string is safe here.
        await self.conn.execute(
            f"""
            INSERT INTO guild_config (guild_id, {column})
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {column} = excluded.{column}
            """,
            (guild_id, value),
        )
        await self.conn.commit()

    async def set_mod_log_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "mod_log_channel_id", channel_id)

    async def set_mute_role(self, guild_id: int, role_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "mute_role_id", role_id)

    async def set_security_log_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "security_log_channel_id", channel_id)

    async def set_alerts_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "alerts_channel_id", channel_id)

    async def set_verification_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "verification_channel_id", channel_id)

    async def set_verified_role(self, guild_id: int, role_id: int | None) -> None:
        await self._upsert_guild_config_column(guild_id, "verified_role_id", role_id)

    async def set_antinuke_enabled(self, guild_id: int, enabled: bool) -> None:
        await self._upsert_guild_config_column(guild_id, "antinuke_enabled", int(enabled))

    async def set_antiraid_enabled(self, guild_id: int, enabled: bool) -> None:
        await self._upsert_guild_config_column(guild_id, "antiraid_enabled", int(enabled))

    async def set_verification_enabled(self, guild_id: int, enabled: bool) -> None:
        await self._upsert_guild_config_column(guild_id, "verification_enabled", int(enabled))

    async def set_min_account_age_hours(self, guild_id: int, hours: int) -> None:
        await self._upsert_guild_config_column(guild_id, "min_account_age_hours", hours)

    async def set_freeze(self, guild_id: int, until_ts: int | None, by_user_id: int | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO guild_config (guild_id, frozen_until, frozen_by)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                frozen_until = excluded.frozen_until,
                frozen_by = excluded.frozen_by
            """,
            (guild_id, until_ts, by_user_id),
        )
        await self.conn.commit()

    async def increment_threats_blocked(self, guild_id: int) -> None:
        now = int(time.time())
        week_seconds = 7 * 24 * 3600
        config = await self.get_guild_config(guild_id)
        week_reset_at = config.week_reset_at
        week_count = config.threats_blocked_week
        if week_reset_at is None or now - week_reset_at >= week_seconds:
            week_reset_at = now
            week_count = 0
        week_count += 1
        await self.conn.execute(
            """
            INSERT INTO guild_config (guild_id, threats_blocked_total, threats_blocked_week, week_reset_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                threats_blocked_total = threats_blocked_total + 1,
                threats_blocked_week = ?,
                week_reset_at = ?
            """,
            (guild_id, week_count, week_reset_at, week_count, week_reset_at),
        )
        await self.conn.commit()

    # ---------------------------------------------------------------- #
    # Trusted admins (bot-level trust list, independent of Discord roles)
    # ---------------------------------------------------------------- #

    async def add_trusted_admin(self, guild_id: int, user_id: int, added_by: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO trusted_admins (guild_id, user_id, added_by, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO NOTHING
            """,
            (guild_id, user_id, added_by, int(time.time())),
        )
        await self.conn.commit()

    async def remove_trusted_admin(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM trusted_admins WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def is_trusted_admin(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM trusted_admins WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return row is not None

    async def list_trusted_admins(self, guild_id: int) -> list[int]:
        cursor = await self.conn.execute(
            "SELECT user_id FROM trusted_admins WHERE guild_id = ? ORDER BY added_at ASC",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [row["user_id"] for row in rows]

    # ---------------------------------------------------------------- #
    # Security events (anti-nuke / anti-raid incident trail)
    # ---------------------------------------------------------------- #

    async def log_security_event(
        self,
        guild_id: int,
        user_id: int,
        action_type: str,
        severity: str,
        detail: str,
        punished: bool,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO security_events (guild_id, user_id, action_type, severity, detail, punished, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, action_type, severity, detail, int(punished), int(time.time())),
        )
        await self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def count_recent_actions(
        self, guild_id: int, user_id: int, action_type: str, since_ts: int
    ) -> int:
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM security_events
            WHERE guild_id = ? AND user_id = ? AND action_type = ? AND created_at >= ?
            """,
            (guild_id, user_id, action_type, since_ts),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_recent_security_events(
        self, guild_id: int, limit: int = 25
    ) -> list[SecurityEvent]:
        cursor = await self.conn.execute(
            """
            SELECT id, guild_id, user_id, action_type, severity, detail, punished, created_at
            FROM security_events
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        rows = await cursor.fetchall()
        events = []
        for row in rows:
            data = dict(row)
            data["punished"] = bool(data["punished"])
            events.append(SecurityEvent(**data))
        return events

    # ---------------------------------------------------------------- #
    # Failed-permission attempts (flag repeated restricted-command tries)
    # ---------------------------------------------------------------- #

    async def log_failed_permission_attempt(
        self, guild_id: int, user_id: int, command_name: str
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO failed_permission_attempts (guild_id, user_id, command_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, command_name, int(time.time())),
        )
        await self.conn.commit()

    async def count_recent_failed_attempts(
        self, guild_id: int, user_id: int, since_ts: int
    ) -> int:
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM failed_permission_attempts
            WHERE guild_id = ? AND user_id = ? AND created_at >= ?
            """,
            (guild_id, user_id, since_ts),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    # ---------------------------------------------------------------- #
    # Guild snapshots (backup & recovery, incl. snapshot-before-destroy)
    # ---------------------------------------------------------------- #

    async def save_snapshot(self, guild_id: int, trigger: str, data: str) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO guild_snapshots (guild_id, trigger, data, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, trigger, data, int(time.time())),
        )
        await self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_latest_snapshot(self, guild_id: int) -> GuildSnapshot | None:
        cursor = await self.conn.execute(
            """
            SELECT id, guild_id, trigger, data, created_at
            FROM guild_snapshots
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (guild_id,),
        )
        row = await cursor.fetchone()
        return GuildSnapshot(**dict(row)) if row else None

    async def prune_old_snapshots(self, guild_id: int, keep: int = 10) -> None:
        await self.conn.execute(
            """
            DELETE FROM guild_snapshots
            WHERE guild_id = ? AND id NOT IN (
                SELECT id FROM guild_snapshots
                WHERE guild_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            """,
            (guild_id, guild_id, keep),
        )
        await self.conn.commit()

    # ---------------------------------------------------------------- #
    # Verification gate (pending members awaiting captcha/button click)
    # ---------------------------------------------------------------- #

    async def add_pending_verification(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO verification_pending (guild_id, user_id, joined_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO NOTHING
            """,
            (guild_id, user_id, int(time.time())),
        )
        await self.conn.commit()

    async def remove_pending_verification(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM verification_pending WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def is_pending_verification(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM verification_pending WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return row is not None
