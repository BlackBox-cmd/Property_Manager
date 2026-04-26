"""Data ingestion cog — /update-data with Pandas cleaning (multi-server)."""

import io
import re
import logging
import discord
import pandas as pd
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT

log = logging.getLogger("RentManager.data")

# Required columns for a valid import (Interior is optional)
REQUIRED_COLUMNS = {"Status", "Address", "Renter CID", "Renter Name", "Phone", "Income", "Cost"}


def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


def clean_currency(val) -> int:
    """Convert '$3,000' or '$1,500' to integer 3000 or 1500."""
    if pd.isna(val):
        return 0
    s = str(val).replace("$", "").replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        return 0


def clean_and_parse(raw_text: str) -> pd.DataFrame:
    """Clean raw text and parse into a DataFrame.

    Handles:
    - Removing  markup
    - Joining split rows (e.g., "Muffin \\n Griffin" -> "Muffin Griffin")
    - Converting currency columns to integers
    - Filtering out Empty / N/A rows
    """
    # 1. Remove  markup
    cleaned = re.sub(r'<[^>]+>', '', raw_text)

    # 2. Fix split rows — join lines that don't start with a valid Status keyword
    #    Valid statuses start a new row: Paid, Overdue, Evictable, Empty
    lines = cleaned.split("\n")
    joined_lines = []
    status_pattern = re.compile(
        r'^\s*(Paid|Overdue|Evictable|Empty|Status)', re.IGNORECASE
    )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if status_pattern.match(stripped) or not joined_lines:
            joined_lines.append(stripped)
        else:
            # This is a continuation of the previous line (split name, etc.)
            joined_lines[-1] = joined_lines[-1].rstrip(",") + " " + stripped

    rejoined = "\n".join(joined_lines)

    # 3. Remove commas from currency values ($1,234,567 → $1234567) before CSV parsing
    #    so they don't get treated as extra column separators.
    rejoined = re.sub(
        r'\$(?:\d{1,3}(?:,\d{3})+|\d+)',
        lambda m: m.group().replace(",", ""),
        rejoined,
    )

    # 4. Parse with Pandas
    df = pd.read_csv(
        io.StringIO(rejoined),
        skipinitialspace=True,
        on_bad_lines="skip",
    )

    # Normalize column names (strip whitespace)
    df.columns = df.columns.str.strip()

    # 4. Convert currency columns to integers
    if "Income" in df.columns:
        df["Income"] = df["Income"].apply(clean_currency)
    if "Cost" in df.columns:
        df["Cost"] = df["Cost"].apply(clean_currency)

    # 5. Filter out Empty status and N/A CIDs
    if "Status" in df.columns:
        df = df[df["Status"].str.strip().str.lower() != "empty"]
    if "Renter CID" in df.columns:
        df = df[df["Renter CID"].astype(str).str.strip().str.upper() != "N/A"]
        df["Renter CID"] = pd.to_numeric(df["Renter CID"], errors="coerce")
        df = df.dropna(subset=["Renter CID"])
        df["Renter CID"] = df["Renter CID"].astype(int)

    # Clean up Renter Name (strip extra whitespace from joined names)
    if "Renter Name" in df.columns:
        df["Renter Name"] = df["Renter Name"].str.strip().str.replace(r'\s+', ' ', regex=True)

    df = df.reset_index(drop=True)
    return df


class DataCog(commands.Cog):
    """Data ingestion: /update-data command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="update-data",
        description="Update rent data — upload a file, paste CSV text, or link a message",
    )
    @app_commands.describe(
        file="A .txt or .csv rent data file to upload",
        csv_data="Paste CSV rent data directly (use instead of a file)",
        message_link="Discord link to a message that has the .txt/.csv file attached",
    )
    @app_commands.guild_only()
    async def update_data(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment = None,
        csv_data: str = None,
        message_link: str = None,
    ):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        guild_id = interaction.guild_id

        # Count how many sources were provided
        provided = sum(x is not None for x in (file, csv_data, message_link))
        if provided == 0:
            return await interaction.response.send_message(
                embed=_embed(
                    "❌ No Data Provided",
                    "Please use **one** of:\n"
                    "• `file` — attach a `.txt` / `.csv` file\n"
                    "• `csv_data` — paste CSV text directly\n"
                    "• `message_link` — paste a Discord message link to a file",
                    0xE74C3C,
                ),
                ephemeral=True,
            )
        if provided > 1:
            return await interaction.response.send_message(
                embed=_embed(
                    "❌ Too Many Inputs",
                    "Please provide **only one** of `file`, `csv_data`, or `message_link`.",
                    0xE74C3C,
                ),
                ephemeral=True,
            )

        # Validate file type when a file is given (case-insensitive)
        if file is not None and not file.filename.lower().endswith((".txt", ".csv")):
            return await interaction.response.send_message(
                embed=_embed("❌ Invalid File", "Please upload a `.txt` or `.csv` file.", 0xE74C3C),
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)

        try:
            # Resolve raw text from whichever source was given
            if file is not None:
                raw = (await file.read()).decode("utf-8")
                source_label = file.filename

            elif csv_data is not None:
                raw = csv_data
                source_label = "pasted text"

            else:
                # message_link path — parse Discord URL
                # Format: https://discord.com/channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
                link_match = re.search(
                    r'discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)',
                    message_link,
                )
                if not link_match:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Invalid Message Link",
                            "Could not parse that Discord link.\n"
                            "Right-click the message → **Copy Message Link** and paste it here.",
                            0xE74C3C,
                        )
                    )

                link_guild_id, channel_id, message_id = (int(x) for x in link_match.groups())

                # Reject cross-guild message links
                if link_guild_id != guild_id:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Wrong Server",
                            "That message link is from a different server. "
                            "Please use a message link from **this** server.",
                            0xE74C3C,
                        )
                    )

                # Try cache first, then API fetch as fallback
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

                if channel is None:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Channel Not Found",
                            "The bot can't see that channel. Make sure it has access.",
                            0xE74C3C,
                        )
                    )

                if not hasattr(channel, "fetch_message"):
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Unsupported Channel",
                            "That message link points to a channel type that cannot be read.",
                            0xE74C3C,
                        )
                    )

                try:
                    linked_msg = await channel.fetch_message(message_id)
                except discord.NotFound:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Message Not Found",
                            "That message doesn't exist or was deleted.",
                            0xE74C3C,
                        )
                    )
                except discord.Forbidden:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ Missing Access",
                            "The bot doesn't have **Read Message History** permission in that channel.\n"
                            "Grant the bot access to that channel, or upload the file directly using the `file` option.",
                            0xE74C3C,
                        )
                    )

                # Find a valid attachment on that message (case-insensitive)
                attachment = next(
                    (a for a in linked_msg.attachments if a.filename.lower().endswith((".txt", ".csv"))),
                    None,
                )
                if attachment is None:
                    return await interaction.followup.send(
                        embed=_embed(
                            "❌ No File Found",
                            "That message has no `.txt` or `.csv` attachment.",
                            0xE74C3C,
                        )
                    )

                raw = (await attachment.read()).decode("utf-8")
                source_label = f"{attachment.filename} (via message link)"

            df = clean_and_parse(raw)

            if df.empty:
                return await interaction.followup.send(
                    embed=_embed(
                        "⚠️ No Valid Data",
                        "The input contained no valid rent entries after cleaning.\n"
                        "Make sure the data has the expected columns:\n"
                        "`Status, Address, Interior, Renter CID, Renter Name, Phone, Income, Cost`",
                        0xF39C12,
                    )
                )

            # Validate required columns BEFORE writing to the database
            missing = REQUIRED_COLUMNS - set(df.columns)
            if missing:
                return await interaction.followup.send(
                    embed=_embed(
                        "❌ Missing Required Columns",
                        f"The data is missing these required columns:\n"
                        f"**{', '.join(sorted(missing))}**\n\n"
                        "Expected columns:\n"
                        "`Status, Address, Interior, Renter CID, Renter Name, Phone, Income, Cost`",
                        0xE74C3C,
                    )
                )

            # Convert DataFrame to list of dicts for database
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "status": str(row.get("Status", "")).strip(),
                    "address": str(row.get("Address", "")).strip(),
                    "interior": str(row.get("Interior", "")).strip() if "Interior" in df.columns else "",
                    "renter_cid": int(row.get("Renter CID", 0)),
                    "renter_name": str(row.get("Renter Name", "")).strip() if "Renter Name" in df.columns else "",
                    "renter_phone": str(row.get("Phone", "")).strip() if "Phone" in df.columns else "",
                    "income": int(row.get("Income", 0)),
                    "cost": int(row.get("Cost", 0)),
                })

            # Build summary BEFORE DB write — ensures failed summary doesn't leave partial data
            status_counts = df["Status"].apply(
                lambda x: "Paid" if str(x).startswith("Paid") else str(x).strip()
            ).value_counts()

            total_income = df["Income"].sum()
            total_cost = df["Cost"].sum()

            summary_lines = []
            for status, count in status_counts.items():
                emoji = {"Paid": "🟢", "Overdue": "🟡", "Evictable": "🔴"}.get(status, "⚫")
                summary_lines.append(f"{emoji} **{status}**: {count}")

            unique_renters = df["Renter CID"].nunique()

            # Everything validated — now atomically replace guild data
            await db.replace_rent_data(guild_id, rows)

            embed = discord.Embed(
                title="✅ Data Updated Successfully",
                description=f"Loaded **{len(rows)}** rent entries from `{source_label}`",
                color=0x2ECC71,
            )
            embed.add_field(
                name="Status Breakdown",
                value="\n".join(summary_lines) if summary_lines else "No data",
                inline=True,
            )
            embed.add_field(
                name="Financials",
                value=f"Expected Income: **${total_income:,}**\nCollected: **${total_cost:,}**",
                inline=True,
            )
            embed.set_footer(text=f"{unique_renters} unique renters | {FOOTER_TEXT}")

            await interaction.followup.send(embed=embed)
            log.info(f"[Guild {guild_id}] Data updated: {len(rows)} entries from {source_label}")

        except Exception as e:
            log.error(f"[Guild {guild_id}] Data import error: {e}", exc_info=True)
            await interaction.followup.send(
                embed=_embed("❌ Import Failed", f"```\n{str(e)}\n```", 0xE74C3C)
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DataCog(bot))
