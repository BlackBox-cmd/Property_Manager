"""Automated reminder system: /set-rent-channel, /set-deadline + daily task (multi-server)."""

import logging
from datetime import datetime
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from config import FOOTER_TEXT

log = logging.getLogger("RentManager.reminders")

RENT_CHANNEL_KEY = "rent_channel_id"


def classify_status(raw_status: str) -> str:
    """Classify a rent entry's status based on its specific paid date.

    Logic:
    - "Overdue" or "Evictable" -> keep as-is (already delinquent)
    - "Paid MM/DD/YYYY" -> if date < today  -> "Expired"
                        -> if date >= today -> "Paid" (valid)
    """
    status = raw_status.strip()

    if status.lower() in ("overdue", "evictable"):
        return status.capitalize()

    if status.lower().startswith("paid"):
        return "Paid"

    return status




def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


def _chunk_lines(lines: list[str], max_chars: int = 3800) -> list[list[str]]:
    """Split a list of lines into chunks that fit within max_chars.

    If a single line exceeds max_chars, it is truncated with an ellipsis
    to prevent Discord embed failures.
    """
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        # Truncate any single line that alone exceeds the limit
        if len(line) > max_chars:
            line = line[: max_chars - 20] + "… *(truncated)*"
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append(current)
    return chunks


class RemindersCog(commands.Cog):
    """Automated rent reminder system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.daily_reminder.start()

    async def cog_unload(self):
        self.daily_reminder.cancel()

    # ── /set-rent-channel ─────────────────────────────────────
    @app_commands.command(
        name="set-rent-channel",
        description="Set the channel where rent reminders will be posted",
    )
    @app_commands.describe(channel="The channel for rent reminders")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def set_rent_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
    ):
        guild_id = interaction.guild_id
        await db.set_setting(guild_id, RENT_CHANNEL_KEY, str(channel.id))
        await interaction.response.send_message(
            embed=_embed(
                "✅ Rent Channel Set",
                f"Rent reminders will now be posted in {channel.mention}.\n\n"
                "The bot will check **every 24 hours** and mention anyone "
                "with overdue, evictable, or expired payments.",
                0x2ECC71,
            )
        )

    # ── /send-reminders (manual trigger) ──────────────────────
    @app_commands.command(
        name="send-reminders",
        description="Manually trigger rent reminders right now",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def send_reminders_now(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        guild_id = interaction.guild_id
        try:
            result = await self._send_reminders_for_guild(guild_id)
        except Exception as e:
            log.error(f"[Guild {guild_id}] Manual reminder error: {e}", exc_info=True)
            result = None
            await interaction.followup.send(
                embed=_embed(
                    "❌ Reminder Failed",
                    f"An error occurred while sending reminders:\n```\n{e}\n```",
                    0xE74C3C,
                )
            )
            return

        await interaction.followup.send(
            embed=_embed(
                "📨 Reminders Sent" if result else "ℹ️ No Reminders Needed",
                result or "No overdue properties found, or no rent channel is set.",
                0x2ECC71 if result else 0x95A5A6,
            )
        )

    # ── Daily Background Task ─────────────────────────────────
    @tasks.loop(hours=24)
    async def daily_reminder(self):
        """Run the reminder check every 24 hours for ALL guilds."""
        try:
            # Get all guilds that have a rent channel configured
            guild_settings = await db.get_all_guild_settings(RENT_CHANNEL_KEY)
            if not guild_settings:
                log.info("No guilds have a rent channel set. Skipping daily reminders.")
                return

            for entry in guild_settings:
                guild_id = entry["guild_id"]
                try:
                    await self._send_reminders_for_guild(guild_id)
                except Exception as e:
                    log.error(f"[Guild {guild_id}] Daily reminder error: {e}", exc_info=True)

        except Exception as e:
            log.error(f"Daily reminder global error: {e}", exc_info=True)

    @daily_reminder.before_loop
    async def before_daily(self):
        await self.bot.wait_until_ready()

    # ── Core Reminder Logic (per guild) ───────────────────────
    async def _send_reminders_for_guild(self, guild_id: int) -> str | None:
        """Process rent data for a single guild, group by user, send reminders."""
        # Get the designated channel for this guild
        channel_id_str = await db.get_setting(guild_id, RENT_CHANNEL_KEY)
        if not channel_id_str:
            log.warning(f"[Guild {guild_id}] No rent channel set. Skipping reminders.")
            return None

        # Cache-miss fallback: try fetch_channel if not cached
        channel = self.bot.get_channel(int(channel_id_str))
        if not channel:
            try:
                channel = await self.bot.fetch_channel(int(channel_id_str))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if not channel:
            log.warning(f"[Guild {guild_id}] Rent channel {channel_id_str} not found.")
            return None

        # Get all rent data for this guild
        all_rent = await db.get_all_rent_data(guild_id)
        if not all_rent:
            return None

        # Classify each entry and find delinquent ones
        delinquent = []
        for entry in all_rent:
            classified = classify_status(entry["status"])
            if classified in ("Overdue", "Evictable", "Expired"):
                entry["classified_status"] = classified
                delinquent.append(entry)

        if not delinquent:
            return None

        # Group delinquent entries by Discord user ID
        # discord_id -> list of entries
        user_debts = defaultdict(list)
        unlinked = []

        for entry in delinquent:
            cid = entry["renter_cid"]
            discord_id = await db.get_discord_id_for_cid(guild_id, cid)
            if discord_id:
                user_debts[discord_id].append(entry)
            else:
                unlinked.append(entry)

        messages_sent = 0
        send_failures = 0
        dm_sent = 0
        dm_failures = 0

        # Send per-user reminder messages (with chunking for large debt lists)
        for discord_id, entries in user_debts.items():
            debt_lines = []
            for e in entries:
                status_emoji = {
                    "Overdue": "🟡",
                    "Evictable": "🔴",
                    "Expired": "🟠",
                }.get(e["classified_status"], "⚫")

                debt_lines.append(
                    f"{status_emoji} **{e['address']}** ({e['classified_status']})"
                )

            cid_info = f"Renter CID(s): {', '.join(str(e['renter_cid']) for e in entries)}"

            # Chunk debt lines to stay within embed description limits
            chunks = _chunk_lines(debt_lines, max_chars=3500)

            # ── Post to the rent channel ──────────────────────
            channel_ok = True
            for i, chunk in enumerate(chunks):
                header = f"<@{discord_id}>, you have outstanding rent for:\n\n"
                footer_text = "\n\nPlease pay immediately to avoid eviction."
                if len(chunks) > 1:
                    header = f"<@{discord_id}>, outstanding rent ({i + 1}/{len(chunks)}):\n\n"

                embed = discord.Embed(
                    title="🔔 Rent Payment Reminder",
                    description=header + "\n".join(chunk) + footer_text,
                    color=0xE74C3C,
                )
                if i == len(chunks) - 1:
                    embed.set_footer(text=f"{cid_info} | {FOOTER_TEXT}")
                else:
                    embed.set_footer(text=FOOTER_TEXT)

                try:
                    await channel.send(
                        content=f"<@{discord_id}>" if i == 0 else None,
                        embed=embed,
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.warning(f"[Guild {guild_id}] Failed to send reminder to {discord_id}: {e}")
                    send_failures += 1
                    channel_ok = False
                    break  # Skip remaining chunks for this user

            if channel_ok:
                messages_sent += 1

            # ── Also DM the user directly ─────────────────────
            try:
                user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
                if user:
                    guild = self.bot.get_guild(guild_id)
                    guild_name = guild.name if guild else f"Server {guild_id}"

                    dm_header = f"You have outstanding rent in **{guild_name}**:\n\n"
                    dm_footer_text = "\n\nPlease pay immediately to avoid eviction."

                    dm_chunks = _chunk_lines(debt_lines, max_chars=3500)
                    for dm_chunk in dm_chunks:
                        dm_embed = discord.Embed(
                            title="🔔 Rent Payment Reminder",
                            description=dm_header + "\n".join(dm_chunk) + dm_footer_text,
                            color=0xE74C3C,
                        )
                        dm_embed.set_footer(text=f"{cid_info} | {FOOTER_TEXT}")
                        await user.send(embed=dm_embed)
                    dm_sent += 1
            except (discord.Forbidden, discord.HTTPException):
                # User has DMs disabled or bot is blocked — not critical
                dm_failures += 1
                log.debug(f"[Guild {guild_id}] Could not DM user {discord_id} (DMs likely disabled)")
            except Exception as exc:
                dm_failures += 1
                log.warning(f"[Guild {guild_id}] Unexpected DM error for {discord_id}: {exc}")

        # Post unlinked renters (no Discord account linked)
        if unlinked:
            lines = []
            for e in unlinked:
                status_emoji = {
                    "Overdue": "🟡",
                    "Evictable": "🔴",
                    "Expired": "🟠",
                }.get(e["classified_status"], "⚫")
                lines.append(
                    f"{status_emoji} **{e['address']}** — {e['renter_name']} "
                    f"(CID: {e['renter_cid']}) ({e['classified_status']})"
                )

            # Safe chunking based on description length
            chunks = _chunk_lines(lines, max_chars=3500)
            for chunk in chunks:
                embed = discord.Embed(
                    title="⚠️ Unlinked Renters with Outstanding Rent",
                    description=(
                        "\n".join(chunk)
                        + "\n\n*These renters have not linked their Discord account.*\n"
                        "Ask them to use `/link-cid` with their CID."
                    ),
                    color=0xF39C12,
                )
                embed.set_footer(text=FOOTER_TEXT)
                try:
                    await channel.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.warning(f"[Guild {guild_id}] Failed to send unlinked reminder: {e}")
                    send_failures += 1

        total_delinquent = len(delinquent)
        log.info(
            f"[Guild {guild_id}] Sent reminders: {messages_sent} users pinged, "
            f"{dm_sent} DM(s) delivered, {dm_failures} DM(s) failed, "
            f"{len(unlinked)} unlinked, {total_delinquent} total delinquent"
            + (f", {send_failures} channel send failure(s)" if send_failures else "")
            + "."
        )

        summary = (
            f"Sent **{messages_sent}** user reminder(s) to channel.\n"
            f"📬 **{dm_sent}** DM(s) delivered successfully.\n"
            f"**{len(unlinked)}** unlinked renter(s) flagged.\n"
            f"**{total_delinquent}** total delinquent properties."
        )
        if send_failures:
            summary += f"\n⚠️ **{send_failures}** channel message(s) failed to send."
        if dm_failures:
            summary += f"\n📭 **{dm_failures}** DM(s) could not be delivered (DMs disabled)."
        return summary


async def setup(bot: commands.Bot):
    await bot.add_cog(RemindersCog(bot))
