"""SQLite database for CID linking, settings, and rent data per guild."""

import logging
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "rent_manager.db"
log = logging.getLogger("RentManager.database")


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize schema and run idempotent migrations."""
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS discord_links (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                discord_id      INTEGER NOT NULL,
                in_game_cid     INTEGER NOT NULL,
                linked_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                guild_id    INTEGER NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );

            CREATE TABLE IF NOT EXISTS rent_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                status          TEXT NOT NULL,
                address         TEXT NOT NULL,
                interior        TEXT,
                renter_cid      INTEGER,
                renter_name     TEXT,
                income          INTEGER NOT NULL DEFAULT 0,
                cost            INTEGER NOT NULL DEFAULT 0,
                uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_links_guild ON discord_links(guild_id);
            CREATE INDEX IF NOT EXISTS idx_links_discord ON discord_links(guild_id, discord_id);
            CREATE INDEX IF NOT EXISTS idx_links_cid ON discord_links(guild_id, in_game_cid);
            CREATE INDEX IF NOT EXISTS idx_rent_guild ON rent_data(guild_id);
            CREATE INDEX IF NOT EXISTS idx_rent_cid ON rent_data(guild_id, renter_cid);
            CREATE INDEX IF NOT EXISTS idx_rent_status ON rent_data(guild_id, status);
            """
        )

        # Migration: keep newest (linked_at, then id) per (guild_id, in_game_cid).
        dupes_removed = conn.execute(
            """
            DELETE FROM discord_links AS old
            WHERE EXISTS (
                SELECT 1
                FROM discord_links AS newer
                WHERE newer.guild_id = old.guild_id
                  AND newer.in_game_cid = old.in_game_cid
                  AND (
                      COALESCE(newer.linked_at, '') > COALESCE(old.linked_at, '')
                      OR (
                          COALESCE(newer.linked_at, '') = COALESCE(old.linked_at, '')
                          AND newer.id > old.id
                      )
                  )
            )
            """
        ).rowcount
        if dupes_removed:
            log.info("Migration: removed %s duplicate CID link(s).", dupes_removed)

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_guild_cid "
            "ON discord_links(guild_id, in_game_cid)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def link_cid(guild_id: int, discord_id: int, cid: int) -> str:
    """Link a Discord user to a CID in a guild.

    Returns:
    - "linked": fresh link created
    - "already_linked": this user already owns the CID
    - "cid_taken": CID is owned by another user
    """
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT discord_id FROM discord_links WHERE guild_id = ? AND in_game_cid = ?",
            (guild_id, cid),
        ).fetchone()

        if existing:
            return "already_linked" if existing["discord_id"] == discord_id else "cid_taken"

        conn.execute(
            "INSERT INTO discord_links (guild_id, discord_id, in_game_cid) VALUES (?, ?, ?)",
            (guild_id, discord_id, cid),
        )
        conn.commit()
        return "linked"
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT discord_id FROM discord_links WHERE guild_id = ? AND in_game_cid = ?",
            (guild_id, cid),
        ).fetchone()
        if existing and existing["discord_id"] == discord_id:
            return "already_linked"
        return "cid_taken"
    finally:
        conn.close()


def unlink_cid(guild_id: int, discord_id: int, cid: int) -> bool:
    """Unlink a Discord user from a CID in a guild."""
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM discord_links WHERE guild_id = ? AND discord_id = ? AND in_game_cid = ?",
        (guild_id, discord_id, cid),
    )
    conn.commit()
    removed = cursor.rowcount > 0
    conn.close()
    return removed


def get_cids_for_user(guild_id: int, discord_id: int) -> list[int]:
    """Get all CIDs linked to a Discord user in a guild."""
    conn = get_db()
    rows = conn.execute(
        "SELECT in_game_cid FROM discord_links WHERE guild_id = ? AND discord_id = ?",
        (guild_id, discord_id),
    ).fetchall()
    conn.close()
    return [r["in_game_cid"] for r in rows]


def get_discord_id_for_cid(guild_id: int, cid: int) -> int | None:
    """Get the Discord user ID that owns a CID in a guild."""
    conn = get_db()
    row = conn.execute(
        "SELECT discord_id FROM discord_links WHERE guild_id = ? AND in_game_cid = ?",
        (guild_id, cid),
    ).fetchone()
    conn.close()
    return row["discord_id"] if row else None


def get_all_links(guild_id: int) -> list[dict]:
    """Get all Discord <-> CID links for a guild."""
    conn = get_db()
    rows = conn.execute(
        "SELECT discord_id, in_game_cid FROM discord_links WHERE guild_id = ? ORDER BY discord_id",
        (guild_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_setting(guild_id: int, key: str, value: str):
    """Set a bot setting for a guild (upsert)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (guild_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
        (guild_id, key, value),
    )
    conn.commit()
    conn.close()


def get_setting(guild_id: int, key: str) -> str | None:
    """Get a bot setting by key for a guild."""
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def get_all_guild_settings(key: str) -> list[dict]:
    """Get a specific setting across all guilds."""
    conn = get_db()
    rows = conn.execute(
        "SELECT guild_id, value FROM settings WHERE key = ?",
        (key,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def replace_rent_data(guild_id: int, rows: list[dict]):
    """Replace all rent data for a guild with a fresh upload atomically."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM rent_data WHERE guild_id = ?", (guild_id,))
        for r in rows:
            conn.execute(
                "INSERT INTO rent_data (guild_id, status, address, interior, renter_cid, renter_name, income, cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    r["status"],
                    r["address"],
                    r["interior"],
                    r["renter_cid"],
                    r["renter_name"],
                    r["income"],
                    r["cost"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_rent_data(guild_id: int) -> list[dict]:
    """Get all current rent data for a guild."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM rent_data WHERE guild_id = ? ORDER BY address",
        (guild_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rent_data_for_cid(guild_id: int, cid: int) -> list[dict]:
    """Get all rent entries for a specific CID in a guild."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM rent_data WHERE guild_id = ? AND renter_cid = ? ORDER BY address",
        (guild_id, cid),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
