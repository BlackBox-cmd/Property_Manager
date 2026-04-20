"""Admin tools: /rent-summary, /all-links (multi-server)."""

from collections import defaultdict

import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT
from cogs.reminders import classify_status


class AdminCog(commands.Cog):
    """Admin reporting tools."""

    pass

    @app_commands.command(
        name="rent-summary",
        description="Display a summary of rent collections (Overdue vs Paid)",
    )
    @app_commands.guild_only()
    async def rent_summary(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        guild_id = interaction.guild_id
        all_data = await db.get_all_rent_data(guild_id)
        if not all_data:
            embed = discord.Embed(
                title="ℹ️ No Data",
                description="No rent data loaded yet. Use `/update-data` to upload.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Build DataFrame for Pandas analysis
        df = pd.DataFrame(all_data)

        # Classify statuses
        df["classified"] = df["status"].apply(classify_status)

        # Aggregate
        total_entries = len(df)
        total_income = df["income"].sum()
        total_cost = df["cost"].sum()

        # Status breakdown
        status_counts = df["classified"].value_counts()
        status_costs = df.groupby("classified")["cost"].sum()

        # Build the embed
        embed = discord.Embed(
            title="📊 Rent Collection Summary",
            color=0x3498DB,
        )

        # Overview
        collection_rate = (total_cost / total_income * 100) if total_income > 0 else 0
        embed.add_field(
            name="💰 Overview",
            value=(
                f"Total Properties: **{total_entries}**\n"
                f"Expected Income: **${total_income:,}**\n"
                f"Collected: **${total_cost:,}**\n"
                f"Collection Rate: **{collection_rate:.1f}%**"
            ),
            inline=False,
        )

        # Status breakdown
        status_lines = []
        status_emojis = {
            "Paid": "🟢",
            "Overdue": "🟡",
            "Evictable": "🔴",
            "Expired": "🟠",
        }
        for status in ["Paid", "Overdue", "Evictable", "Expired"]:
            count = status_counts.get(status, 0)
            cost = status_costs.get(status, 0)
            emoji = status_emojis.get(status, "⚫")
            if count > 0:
                status_lines.append(
                    f"{emoji} **{status}**: {count} properties — ${cost:,}"
                )

        # Handle any other statuses
        for status in status_counts.index:
            if status not in ["Paid", "Overdue", "Evictable", "Expired"]:
                count = status_counts[status]
                cost = status_costs.get(status, 0)
                status_lines.append(f"⚫ **{status}**: {count} — ${cost:,}")

        embed.add_field(
            name="📋 Status Breakdown",
            value="\n".join(status_lines) if status_lines else "No data",
            inline=False,
        )

        # Top debtors
        delinquent = df[df["classified"].isin(["Overdue", "Evictable", "Expired"])]
        if not delinquent.empty:
            top_debtors = (
                delinquent.groupby(["renter_cid", "renter_name"])["cost"]
                .sum()
                .sort_values(ascending=False)
                .head(10)
            )

            debtor_lines = []
            for (cid, name), total in top_debtors.items():
                discord_id = await db.get_discord_id_for_cid(guild_id, cid)
                mention = f"<@{discord_id}>" if discord_id else f"*{name}*"
                debtor_lines.append(f"• {mention} (CID: {cid}) — **${total:,}**")

            embed.add_field(
                name="🔝 Top Debtors",
                value="\n".join(debtor_lines),
                inline=False,
            )

            total_owed = delinquent["cost"].sum()
            embed.add_field(
                name="⚠️ Total Outstanding",
                value=f"**${total_owed:,}** across **{len(delinquent)}** properties",
                inline=True,
            )

        # Linked vs unlinked
        all_cids = set(df["renter_cid"].unique())
        linked_count = 0
        for c in all_cids:
            if await db.get_discord_id_for_cid(guild_id, c) is not None:
                linked_count += 1
        embed.add_field(
            name="🔗 CID Links",
            value=f"**{linked_count}/{len(all_cids)}** unique renters linked to Discord",
            inline=True,
        )

        embed.set_footer(text=f"Data from last /update-data upload | {FOOTER_TEXT}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="all-links",
        description="View all Discord-CID links (admin view)",
    )
    @app_commands.guild_only()
    async def all_links(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        guild_id = interaction.guild_id
        links = await db.get_all_links(guild_id)
        if not links:
            embed = discord.Embed(
                title="🔗 No Links",
                description="No Discord users have linked their CIDs yet.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Group by discord_id
        grouped = defaultdict(list)
        for link in links:
            grouped[link["discord_id"]].append(link["in_game_cid"])

        all_lines = []
        for discord_id, cids in grouped.items():
            cid_str = ", ".join(str(c) for c in cids)
            all_lines.append(f"<@{discord_id}> → CIDs: **{cid_str}**")

        stats_text = f"{len(links)} total links, {len(grouped)} users"

        # Paginate to stay under Discord's 4096-char embed description limit
        pages = []
        current_page = []
        current_len = 0
        for line in all_lines:
            line_len = len(line) + 1  # +1 for newline
            if current_len + line_len > 3800 and current_page:  # Leave headroom
                pages.append(current_page)
                current_page = []
                current_len = 0
            current_page.append(line)
            current_len += line_len
        if current_page:
            pages.append(current_page)

        for i, page in enumerate(pages):
            title = "🔗 All Discord — CID Links"
            if len(pages) > 1:
                title += f" (Page {i + 1}/{len(pages)})"
            embed = discord.Embed(
                title=title,
                description="\n".join(page),
                color=0x3498DB,
            )
            if i == len(pages) - 1:
                embed.set_footer(text=f"{stats_text} | {FOOTER_TEXT}")
            else:
                embed.set_footer(text=FOOTER_TEXT)

            if i == 0:
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="renter-phones",
        description="Get a list of all renters and their phone numbers",
    )
    @app_commands.guild_only()
    async def renter_phones(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        guild_id = interaction.guild_id
        all_data = await db.get_all_rent_data(guild_id)

        if not all_data:
            embed = discord.Embed(
                title="ℹ️ No Data",
                description="No rent data loaded yet. Use `/update-data` to upload.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Filter out entries without a renter
        valid_entries = [e for e in all_data if e.get("renter_cid") and e.get("renter_name")]

        if not valid_entries:
            return await interaction.response.send_message("No occupied properties found to list phone numbers for.", ephemeral=True)

        # Format lines: Address — Renter Name (CID) — Phone
        lines = []
        for e in valid_entries:
            phone = e.get("renter_phone", "No Phone Found")
            lines.append(f"🏠 **{e['address']}**\n👤 {e['renter_name']} (CID: {e['renter_cid']})\n📞 `{phone}`")

        # Paginate to stay under limits
        pages = []
        current_page = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 2  # +2 for extra spacing
            if current_len + line_len > 3500 and current_page:
                pages.append(current_page)
                current_page = []
                current_len = 0
            current_page.append(line)
            current_len += line_len
        if current_page:
            pages.append(current_page)

        for i, page_lines in enumerate(pages):
            title = "📞 Renter Phone Directory"
            if len(pages) > 1:
                title += f" ({i + 1}/{len(pages)})"

            embed = discord.Embed(
                title=title,
                description="\n\n".join(page_lines),
                color=0x3498DB,
            )
            embed.set_footer(text=f"Total Properties: {len(valid_entries)} | {FOOTER_TEXT}")

            if i == 0:
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.followup.send(embed=embed)


    @app_commands.command(
        name="trust-user",
        description="Add a Discord user to the trusted list for admin commands",
    )
    @app_commands.describe(user="The Discord user to trust")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def trust_user(self, interaction: discord.Interaction, user: discord.Member):
        added = await db.add_trusted_user(interaction.guild_id, user.id)
        if added:
            await interaction.response.send_message(f"✅ {user.mention} has been added to the trusted user list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {user.mention} is already a trusted user.", ephemeral=True)

    @app_commands.command(
        name="untrust-user",
        description="Remove a Discord user from the trusted list",
    )
    @app_commands.describe(user="The Discord user to untrust")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def untrust_user(self, interaction: discord.Interaction, user: discord.Member):
        removed = await db.remove_trusted_user(interaction.guild_id, user.id)
        if removed:
            await interaction.response.send_message(f"✅ {user.mention} has been removed from the trusted user list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {user.mention} wasn't in the trusted user list.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
