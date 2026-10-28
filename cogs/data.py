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


def _extract_block_from_address(address: str) -> str:
    """Extract block name from address.

    Examples:
        "Forum Drive 2 / Apt 1" -> "Forum Drive"
        "Clinton Ave / Apt1" -> "Clinton Ave"
        "Niland Avenue 1" -> "Niland Avenue"
        "Steele Way 2" -> "Steele Way"
    """
    import re
    # Handle "/" separator first
    if "/" in address:
        address = address.split("/")[0].strip()

    # Strip trailing numbers (e.g., " 1", " 2", " 3") at the end
    # "Forum Drive 2" -> "Forum Drive"
    # "Niland Avenue 1" -> "Niland Avenue"
    address = re.sub(r'\s+\d+$', '', address).strip()

    return address


async def _sync_roles_for_data_update(guild: discord.Guild, rows: list[dict]) -> dict:
    """Synchronize Discord roles based on the updated rent data.

    Returns a summary dict with counts of assigned, removed, moved, and errors.
    """
    guild_id = guild.id

    # Get configuration
    logs_channel_id = await db.get_setting(guild_id, "role_logs_channel_id")
    logs_channel = None
    if logs_channel_id:
        try:
            logs_channel = guild.get_channel(int(logs_channel_id))
        except (ValueError, TypeError):
            pass

    # Get block role mappings
    block_mappings = await db.list_all_role_mappings(guild_id)

    # Get old renter blocks
    old_renter_blocks = await db.get_all_renter_blocks(guild_id)

    # Build new renter blocks from the imported data
    new_renter_blocks = {}  # {cid: block_name}
    for row in rows:
        cid = row.get("renter_cid")
        if cid:
            block = _extract_block_from_address(row.get("address", ""))
            new_renter_blocks[cid] = block

    # Track operations
    assigned = []
    removed = []
    moved = []
    errors = []
    skipped_no_link = []
    skipped_no_role = []

    # Process each renter in the new data
    for cid, block in new_renter_blocks.items():
        # Get Discord ID for this CID
        discord_id = await db.get_discord_id_for_cid(guild_id, cid)
        if not discord_id:
            continue  # CID not linked to any Discord user

        # Get the member
        member = guild.get_member(discord_id)
        if not member:
            errors.append(f"CID {cid}: Member not found in server")
            continue

        # Get old block for this CID
        old_block = old_renter_blocks.get(cid)

        # Get the block role for this block
        role_id = block_mappings.get(block)
        if not role_id:
            skipped_no_role.append(f"CID {cid}: No role configured for block '{block}'")
            continue

        role = guild.get_role(role_id)
        if not role:
            errors.append(f"Block '{block}': Role not found (ID: {role_id})")
            continue

        # Check if role already assigned
        if role in member.roles:
            # Check if block changed
            if old_block and old_block != block:
                # Block changed - need to swap roles
                old_role_id = block_mappings.get(old_block)
                if old_role_id:
                    old_role = guild.get_role(old_role_id)
                    if old_role and old_role in member.roles:
                        try:
                            await member.remove_roles(old_role, reason=f"Block changed from '{old_block}' to '{block}'")
                            moved.append(f"{member.display_name}: {old_role.name} → {role.name}")
                        except discord.Forbidden:
                            errors.append(f"{member.display_name}: Permission denied to remove {old_role.name}")
                        except discord.HTTPException as e:
                            errors.append(f"{member.display_name}: API error removing {old_role.name}: {str(e)}")
            # Role already assigned, just update history
            await db.set_renter_block(guild_id, cid, block)
            continue

        # Assign the role
        try:
            await member.add_roles(role, reason=f"Block assignment from '{block}'")
            assigned.append(f"{member.display_name}: {role.name}")
        except discord.Forbidden:
            errors.append(f"{member.display_name}: Permission denied to assign {role.name}")
        except discord.HTTPException as e:
            errors.append(f"{member.display_name}: API error assigning {role.name}: {str(e)}")

        # Update history
        await db.set_renter_block(guild_id, cid, block)

    # Process removals: renters in old data but not in new data
    for cid, old_block in old_renter_blocks.items():
        if cid not in new_renter_blocks:
            # Renter no longer in dataset - remove their block role
            discord_id = await db.get_discord_id_for_cid(guild_id, cid)
            if not discord_id:
                continue

            member = guild.get_member(discord_id)
            if not member:
                continue

            # Get the old block role
            old_role_id = block_mappings.get(old_block)
            if not old_role_id:
                continue

            old_role = guild.get_role(old_role_id)
            if not old_role or old_role not in member.roles:
                continue

            try:
                await member.remove_roles(old_role, reason=f"Renter removed from dataset")
                removed.append(f"{member.display_name}: {old_role.name}")
            except discord.Forbidden:
                errors.append(f"{member.display_name}: Permission denied to remove {old_role.name}")
            except discord.HTTPException as e:
                errors.append(f"{member.display_name}: API error removing {old_role.name}: {str(e)}")

            # Update history to mark as removed
            await db.set_renter_block(guild_id, cid, None)

    # Check if linked user role needs to be assigned (for existing linked users in new data)
    linked_role_id = await db.get_setting(guild_id, "linked_user_role_id")
    if linked_role_id:
        try:
            linked_role_id = int(linked_role_id)
            linked_role = guild.get_role(linked_role_id)
            if linked_role:
                for cid in new_renter_blocks.keys():
                    discord_id = await db.get_discord_id_for_cid(guild_id, cid)
                    if not discord_id:
                        continue
                    member = guild.get_member(discord_id)
                    if member and linked_role not in member.roles:
                        try:
                            await member.add_roles(linked_role, reason="Linked user role (data update)")
                        except (discord.Forbidden, discord.HTTPException):
                            pass  # Silently skip if already has role or can't assign
        except (ValueError, TypeError):
            pass

    # Log to channel if configured
    if logs_channel:
        sync_embed = _embed(
            "🔄 Role Sync Completed",
            f"Data update triggered automatic role synchronization",
            0x5865F2,
        )

        if assigned:
            sync_embed.add_field(
                name="✅ Assigned",
                value="\n".join(assigned[:10]) + ("..." if len(assigned) > 10 else ""),
                inline=False,
            )
        if moved:
            sync_embed.add_field(
                name="🔄 Moved Blocks",
                value="\n".join(moved[:10]) + ("..." if len(moved) > 10 else ""),
                inline=False,
            )
        if removed:
            sync_embed.add_field(
                name="❌ Removed (Renter Removed)",
                value="\n".join(removed[:10]) + ("..." if len(removed) > 10 else ""),
                inline=False,
            )
        if errors:
            sync_embed.add_field(
                name="⚠️ Errors",
                value="\n".join(errors[:5]) + ("..." if len(errors) > 5 else ""),
                inline=False,
            )
        if skipped_no_role:
            sync_embed.add_field(
                name="⏭️ Skipped (No Role Configured)",
                value="\n".join(skipped_no_role[:5]) + ("..." if len(skipped_no_role) > 5 else ""),
                inline=False,
            )

        # Summary
        summary = f"Assigned: **{len(assigned)}** | Moved: **{len(moved)}** | Removed: **{len(removed)}** | Errors: **{len(errors)}**"
        sync_embed.add_field(name="Summary", value=summary, inline=False)

        try:
            await logs_channel.send(embed=sync_embed)
        except discord.Forbidden:
            log.warning(f"Cannot send to logs channel {logs_channel_id} in guild {guild_id}")
        except discord.HTTPException as e:
            log.warning(f"Error sending to logs channel: {e}")

    return {
        "assigned": assigned,
        "removed": removed,
        "moved": moved,
        "errors": errors,
        "skipped_no_role": skipped_no_role,
    }


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

    # 5. Handle Renter CID — allow N/A for Empty status, convert others to numeric
    if "Renter CID" in df.columns and "Status" in df.columns:
        # For non-Empty properties, N/A is invalid — filter them out
        is_empty = df["Status"].astype(str).str.strip().str.lower() == "empty"
        is_na = df["Renter CID"].astype(str).str.strip().str.upper() == "N/A"

        # Keep: Empty properties (regardless of CID), or non-Empty with valid CID
        df = df[is_empty | ~is_na]

        # Convert Renter CID to numeric (N/A becomes NaN, valid numbers stay)
        df["Renter CID"] = pd.to_numeric(df["Renter CID"], errors="coerce")

        # Convert to int where not NaN (use Int64 dtype to allow NaN)
        df["Renter CID"] = df["Renter CID"].astype("Int64")

    # Clean up Renter Name (strip extra whitespace from joined names)
    if "Renter Name" in df.columns:
        df["Renter Name"] = df["Renter Name"].astype(str).str.strip().str.replace(r'\s+', ' ', regex=True)

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
                    "renter_cid": int(row.get("Renter CID", 0)) if pd.notna(row.get("Renter CID")) else 0,
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

            # Synchronize roles based on the new data
            role_sync_result = await _sync_roles_for_data_update(interaction.guild, rows)

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

            # Add role sync summary
            if role_sync_result["assigned"] or role_sync_result["removed"] or role_sync_result["moved"] or role_sync_result["errors"]:
                sync_summary = (
                    f"✅ Assigned: {len(role_sync_result['assigned'])}\n"
                    f"🔄 Moved: {len(role_sync_result['moved'])}\n"
                    f"❌ Removed: {len(role_sync_result['removed'])}\n"
                    f"⚠️ Errors: {len(role_sync_result['errors'])}"
                )
                embed.add_field(name="Role Sync", value=sync_summary, inline=True)

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
