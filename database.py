"""PostgreSQL database for CID linking, settings, and rent data per guild."""

import logging
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
log = logging.getLogger("RentManager.database")

_pool = None

async def init_pool():
    global _pool
    if _pool is None:
        if not DB_URL:
            # Optionally default to local for testing if they forgot it, but we should assert instead so they know it failed
            raise ValueError("DATABASE_URL environment variable is not set!")
        _pool = await asyncpg.create_pool(DB_URL)

async def get_db():
    global _pool
    if _pool is None:
        await init_pool()
    return _pool

async def init_db():
    """Initialize schema and run migrations."""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discord_links (
                id              SERIAL PRIMARY KEY,
                guild_id        BIGINT NOT NULL,
                discord_id      BIGINT NOT NULL,
                in_game_cid     BIGINT NOT NULL,
                linked_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                guild_id    BIGINT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );

            CREATE TABLE IF NOT EXISTS rent_data (
                id              SERIAL PRIMARY KEY,
                guild_id        BIGINT NOT NULL,
                status          TEXT NOT NULL,
                address         TEXT NOT NULL,
                interior        TEXT,
                renter_cid      BIGINT,
                renter_name     TEXT,
                income          BIGINT NOT NULL DEFAULT 0,
                cost            BIGINT NOT NULL DEFAULT 0,
                renter_phone    TEXT,
                uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_rent_guild ON rent_data(guild_id);
            CREATE INDEX IF NOT EXISTS idx_rent_cid ON rent_data(guild_id, renter_cid);
            CREATE INDEX IF NOT EXISTS idx_rent_status ON rent_data(guild_id, status);

            CREATE TABLE IF NOT EXISTS trusted_users (
                guild_id    BIGINT NOT NULL,
                discord_id  BIGINT NOT NULL,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, discord_id)
            );
            """
        )

        try:
            # Migration: keep newest (linked_at, then id) per (guild_id, in_game_cid). PostgreSQL syntax
            dupes_removed = await conn.execute(
                """
                DELETE FROM discord_links
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                        ROW_NUMBER() OVER( PARTITION BY guild_id, in_game_cid ORDER BY linked_at DESC, id DESC ) as row_num
                        FROM discord_links
                    ) t
                    WHERE t.row_num > 1
                )
                """
            )
            if dupes_removed and not dupes_removed.startswith('DELETE 0'):
                log.info("Migration: removed duplicate CID link(s) - %s", dupes_removed)

            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_guild_cid "
                "ON discord_links(guild_id, in_game_cid)"
            )

            # Migration: add renter_phone to rent_data if missing
            await conn.execute(
                "ALTER TABLE rent_data ADD COLUMN IF NOT EXISTS renter_phone TEXT"
            )
        except Exception as e:
            log.error("Migration error: %s", e)
            raise


async def link_cid(guild_id: int, discord_id: int, cid: int) -> str:
    """Link a Discord user to a CID in a guild.

    Returns:
    - "linked": fresh link created
    - "already_linked": this user already owns the CID
    - "cid_taken": CID is owned by another user
    """
    pool = await get_db()
    try:
        existing = await pool.fetchrow(
            "SELECT discord_id FROM discord_links WHERE guild_id = $1 AND in_game_cid = $2",
            guild_id, cid
        )

        if existing:
            return "already_linked" if existing["discord_id"] == discord_id else "cid_taken"

        await pool.execute(
            "INSERT INTO discord_links (guild_id, discord_id, in_game_cid) VALUES ($1, $2, $3)",
            guild_id, discord_id, cid
        )
        return "linked"
    except asyncpg.exceptions.UniqueViolationError:
        existing = await pool.fetchrow(
            "SELECT discord_id FROM discord_links WHERE guild_id = $1 AND in_game_cid = $2",
            guild_id, cid
        )
        if existing and existing["discord_id"] == discord_id:
            return "already_linked"
        return "cid_taken"


async def unlink_cid(guild_id: int, discord_id: int, cid: int) -> bool:
    """Unlink a Discord user from a CID in a guild."""
    pool = await get_db()
    status = await pool.execute(
        "DELETE FROM discord_links WHERE guild_id = $1 AND discord_id = $2 AND in_game_cid = $3",
        guild_id, discord_id, cid
    )
    removed = status.startswith('DELETE ') and status != 'DELETE 0'
    return removed


async def get_cids_for_user(guild_id: int, discord_id: int) -> list[int]:
    """Get all CIDs linked to a Discord user in a guild."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT in_game_cid FROM discord_links WHERE guild_id = $1 AND discord_id = $2",
        guild_id, discord_id
    )
    return [r["in_game_cid"] for r in rows]


async def get_discord_id_for_cid(guild_id: int, cid: int) -> int | None:
    """Get the Discord user ID that owns a CID in a guild."""
    pool = await get_db()
    row = await pool.fetchrow(
        "SELECT discord_id FROM discord_links WHERE guild_id = $1 AND in_game_cid = $2",
        guild_id, cid
    )
    return row["discord_id"] if row else None


async def get_all_links(guild_id: int) -> list[dict]:
    """Get all Discord <-> CID links for a guild."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT discord_id, in_game_cid FROM discord_links WHERE guild_id = $1 ORDER BY discord_id",
        guild_id
    )
    return [dict(r) for r in rows]


async def set_setting(guild_id: int, key: str, value: str):
    """Set a bot setting for a guild (upsert)."""
    pool = await get_db()
    await pool.execute(
        "INSERT INTO settings (guild_id, key, value) VALUES ($1, $2, $3) "
        "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
        guild_id, key, value
    )


async def get_setting(guild_id: int, key: str) -> str | None:
    """Get a bot setting by key for a guild."""
    pool = await get_db()
    row = await pool.fetchrow(
        "SELECT value FROM settings WHERE guild_id = $1 AND key = $2",
        guild_id, key
    )
    return row["value"] if row else None


async def get_all_guild_settings(key: str) -> list[dict]:
    """Get a specific setting across all guilds."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT guild_id, value FROM settings WHERE key = $1",
        key
    )
    return [dict(r) for r in rows]


async def replace_rent_data(guild_id: int, rows: list[dict]):
    """Replace all rent data for a guild with a fresh upload atomically."""
    pool = await get_db()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM rent_data WHERE guild_id = $1", guild_id)
            
            values = [
                (
                    guild_id,
                    r["status"],
                    r["address"],
                    r["interior"],
                    r["renter_cid"] if r["renter_cid"] is not None else 0, # Catch None
                    r["renter_name"] if r["renter_name"] is not None else "",
                    r["income"],
                    r["cost"],
                    r["renter_phone"] if r.get("renter_phone") else "",
                )
                for r in rows
            ]
            
            if values:
                await conn.executemany(
                    "INSERT INTO rent_data (guild_id, status, address, interior, renter_cid, renter_name, income, cost, renter_phone) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    values
                )


async def get_all_rent_data(guild_id: int) -> list[dict]:
    """Get all current rent data for a guild."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT * FROM rent_data WHERE guild_id = $1 ORDER BY address",
        guild_id
    )
    return [dict(r) for r in rows]


async def add_trusted_user(guild_id: int, discord_id: int) -> bool:
    """Add a user to the trusted list. Returns True if added, False if already exists."""
    pool = await get_db()
    try:
        await pool.execute(
            "INSERT INTO trusted_users (guild_id, discord_id) VALUES ($1, $2)",
            guild_id, discord_id
        )
        return True
    except asyncpg.exceptions.UniqueViolationError:
        return False

async def remove_trusted_user(guild_id: int, discord_id: int) -> bool:
    """Remove a user from the trusted list. Returns True if removed."""
    pool = await get_db()
    status = await pool.execute(
        "DELETE FROM trusted_users WHERE guild_id = $1 AND discord_id = $2",
        guild_id, discord_id
    )
    return status.startswith('DELETE ') and status != 'DELETE 0'

async def is_trusted_user(guild_id: int, discord_id: int) -> bool:
    """Check if a user is trusted in a guild."""
    pool = await get_db()
    row = await pool.fetchrow(
        "SELECT 1 FROM trusted_users WHERE guild_id = $1 AND discord_id = $2",
        guild_id, discord_id
    )
    return row is not None

async def get_trusted_users(guild_id: int) -> list[int]:
    """Get all trusted users for a guild."""
    pool = await get_db()
    rows = await pool.fetch(
        "SELECT discord_id FROM trusted_users WHERE guild_id = $1",
        guild_id
    )
    return [r["discord_id"] for r in rows]
