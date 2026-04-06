"""Property Manager Bot — Entry Point (multi-server)."""

import asyncio
import logging
import os
import random
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from database import init_db
from config import BOT_ACTIVITIES

load_dotenv()

# ── Config ────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("RentManager")

# ── Bot Setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for member lookup

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

COGS = [
    "cogs.data",
    "cogs.linking",
    "cogs.reminders",
    "cogs.admin",
    "cogs.help",
]


# ── Rotating Activity ────────────────────────────────────────
@tasks.loop(minutes=5)
async def rotate_activity():
    activity_data = random.choice(BOT_ACTIVITIES)
    activity_type = {
        "playing": discord.ActivityType.playing,
        "listening": discord.ActivityType.listening,
        "watching": discord.ActivityType.watching,
        "competing": discord.ActivityType.competing,
    }.get(activity_data["type"], discord.ActivityType.playing)

    await bot.change_presence(
        activity=discord.Activity(type=activity_type, name=activity_data["text"])
    )


@rotate_activity.before_loop
async def before_rotate():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Connected to {len(bot.guilds)} guild(s)")

    if not rotate_activity.is_running():
        rotate_activity.start()

    log.info("Rent Manager Bot is ready!")


async def main():
    init_db()
    log.info("Database initialized.")

    for cog in COGS:
        try:
            await bot.load_extension(cog)
            log.info(f"Loaded cog: {cog}")
        except Exception as e:
            log.error(f"Failed to load cog {cog}: {e}")

    if not TOKEN or TOKEN == "your_token_here":
        log.error("DISCORD_TOKEN not set! Update your .env file.")
        return

    # Sync commands once via setup_hook (runs exactly once before on_ready)
    @bot.event
    async def setup_hook():
        try:
            synced = await bot.tree.sync()
            log.info(f"Synced {len(synced)} slash commands globally")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}")

    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
