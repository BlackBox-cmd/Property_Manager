"""Admin tools: /rent-summary, /all-links (multi-server)."""

from collections import defaultdict
import platform
import sys
import os
import time
import psutil
import aiohttp

import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT
from cogs.reminders import classify_status


class PaginatorView(discord.ui.View):
    def __init__(self, lines: list, title: str, color: int = 0x3498DB):
        super().__init__(timeout=300)
        self.lines = lines
        self.title = title
        self.color = color
        self.current_page = 0
        
        # Build pages
        self.pages = []
        current_page_lines = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > 3800 and current_page_lines:
                self.pages.append("\n".join(current_page_lines))
                current_page_lines = []
                current_len = 0
            current_page_lines.append(line)
            current_len += line_len
        if current_page_lines:
            self.pages.append("\n".join(current_page_lines))
            
        if not self.pages:
            self.pages = ["No data."]

        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page == len(self.pages) - 1
        self.page_counter.label = f"Page {self.current_page + 1}/{len(self.pages)}"

    def get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{self.title}",
            description=self.pages[self.current_page],
            color=self.color,
        )
        embed.set_footer(text=f"Total: {len(self.lines)} | {FOOTER_TEXT}")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True, custom_id="counter")
    async def page_counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, custom_id="next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


class LinkedStatusOptionsView(discord.ui.View):
    def __init__(self, linked_lines: list, unlinked_lines: list):
        super().__init__(timeout=300)
        self.linked_lines = linked_lines
        self.unlinked_lines = unlinked_lines

    @discord.ui.button(label="View Linked", style=discord.ButtonStyle.success)
    async def btn_linked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.linked_lines:
            return await interaction.response.send_message("No linked CIDs found.", ephemeral=True)
        view = PaginatorView(self.linked_lines, "🔗 Linked CIDs")
        if len(view.pages) <= 1:
            view.clear_items()
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="View Unlinked", style=discord.ButtonStyle.danger)
    async def btn_unlinked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.unlinked_lines:
            return await interaction.response.send_message("No unlinked CIDs found.", ephemeral=True)
        view = PaginatorView(self.unlinked_lines, "🔗 Unlinked CIDs", color=0xE74C3C)
        if len(view.pages) <= 1:
            view.clear_items()
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)


class AdminCog(commands.Cog):
    """Admin reporting tools."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="rent-summary",
        description="Display a summary of rent collections (Overdue vs Paid)",
    )
    @app_commands.guild_only()
    async def rent_summary(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        await interaction.response.defer()
        
        guild_id = interaction.guild_id
        all_data = await db.get_all_rent_data(guild_id)
        if not all_data:
            embed = discord.Embed(
                title="ℹ️ No Data",
                description="No rent data loaded yet. Use `/update-data` to upload.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.followup.send(embed=embed, ephemeral=True)
            
        links = await db.get_all_links(guild_id)
        cid_to_discord = {str(link["in_game_cid"]): link["discord_id"] for link in links}

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
        }
        for status in ["Paid", "Overdue", "Evictable"]:
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
        delinquent = df[df["classified"].isin(["Overdue", "Evictable"])]
        if not delinquent.empty:
            top_debtors = (
                delinquent.groupby(["renter_cid", "renter_name"])["cost"]
                .sum()
                .sort_values(ascending=False)
                .head(10)
            )

            debtor_lines = []
            for (cid, name), total in top_debtors.items():
                discord_id = cid_to_discord.get(str(cid))
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
            if cid_to_discord.get(str(c)) is not None:
                linked_count += 1
        embed.add_field(
            name="🔗 CID Links",
            value=f"**{linked_count}/{len(all_cids)}** unique renters linked to Discord",
            inline=True,
        )

        embed.set_footer(text=f"Data from last /update-data upload | {FOOTER_TEXT}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="all-links",
        description="View all Discord-CID links (admin view)",
    )
    @app_commands.guild_only()
    async def all_links(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        await interaction.response.defer()
        
        guild_id = interaction.guild_id
        links = await db.get_all_links(guild_id)
        if not links:
            embed = discord.Embed(
                title="🔗 No Links",
                description="No Discord users have linked their CIDs yet.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.followup.send(embed=embed, ephemeral=True)

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
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="renter-phones",
        description="Get a list of all renters and their phone numbers",
    )
    @app_commands.describe(
        cid="Search for a specific renter's phone by CID",
        renter_name="Search for a specific renter's phone by Name"
    )
    @app_commands.guild_only()
    async def renter_phones(self, interaction: discord.Interaction, cid: str = None, renter_name: str = None):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied. You must be an Admin or Trusted User.", ephemeral=True)
            
        await interaction.response.defer()
        
        guild_id = interaction.guild_id
        all_data = await db.get_all_rent_data(guild_id)

        if not all_data:
            embed = discord.Embed(
                title="ℹ️ No Data",
                description="No rent data loaded yet. Use `/update-data` to upload.",
                color=0x95A5A6,
            )
            embed.set_footer(text=FOOTER_TEXT)
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # Filter out entries without a renter
        valid_entries = [e for e in all_data if e.get("renter_cid") and e.get("renter_name")]

        if not valid_entries:
            return await interaction.followup.send("No occupied properties found to list phone numbers for.", ephemeral=True)

        if cid:
            valid_entries = [e for e in valid_entries if str(e.get("renter_cid")) == str(cid)]
            
        if renter_name:
            valid_entries = [e for e in valid_entries if e.get("renter_name").lower() == renter_name.lower()]

        if not valid_entries:
            return await interaction.followup.send("No renters found matching your search criteria.", ephemeral=True)

        # Format lines: Address — Renter Name (CID) — Phone
        lines = []
        for e in valid_entries:
            phone = e.get("renter_phone", "No Phone Found")
            lines.append(f"🏠 **{e['address']}**\n👤 {e['renter_name']} (CID: {e['renter_cid']})\n📞 `{phone}`\n")

        view = PaginatorView(lines, "📞 Renter Phone Directory")
        if len(view.pages) <= 1:
            view.clear_items()
            
        embed = view.get_embed()
        
        await interaction.followup.send(embed=embed, view=view)

    @renter_phones.autocomplete("renter_name")
    async def renter_phones_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        guild_id = interaction.guild_id
        if not guild_id:
            return []
            
        pool = await db.get_db()
        rows = await pool.fetch(
            "SELECT DISTINCT renter_name FROM rent_data WHERE guild_id = $1 AND renter_name IS NOT NULL AND renter_name ILIKE $2 LIMIT 25",
            guild_id, f"%{current}%"
        )
        
        return [app_commands.Choice(name=r["renter_name"], value=r["renter_name"]) for r in rows]


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

    @commands.command(name="clearsync")
    @commands.has_permissions(administrator=True)
    async def clearsync(self, ctx: commands.Context):
        """Wipe out stuck guild-specific slash commands."""
        # This clears any commands specifically registered to this guild ID
        self.bot.tree.clear_commands(guild=ctx.guild)
        await self.bot.tree.sync(guild=ctx.guild)
        
        # Then sync global commands as usual
        synced = await self.bot.tree.sync()
        await ctx.send(f"🧹 Cleared stuck guild-specific commands and re-synced **{len(synced)}** global commands!")

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

    @app_commands.command(
        name="linkedstatus",
        description="Show how many in-game CIDs are linked to Discord accounts.",
    )
    @app_commands.guild_only()
    async def linked_status(self, interaction: discord.Interaction):
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            
        await interaction.response.defer()
        
        guild_id = interaction.guild_id
        all_data = await db.get_all_rent_data(guild_id)
        if not all_data:
            return await interaction.followup.send("No rent data found to compare links against.", ephemeral=True)
            
        links = await db.get_all_links(guild_id)
        cid_to_discord = {str(link["in_game_cid"]): link["discord_id"] for link in links}
            
        cid_to_name = {}
        for e in all_data:
            c = e.get("renter_cid")
            if c:
                c_str = str(c)
                name = e.get("renter_name")
                if c_str not in cid_to_name or not cid_to_name[c_str] or cid_to_name[c_str] == "Unknown Renter":
                    cid_to_name[c_str] = name if name else "Unknown Renter"
                    
        all_cids = list(cid_to_name.keys())
        
        linked_lines = []
        unlinked_lines = []
        
        # Sort CIDs properly if they are numeric
        sorted_cids = sorted(all_cids, key=lambda x: int(x) if str(x).isdigit() else str(x))
        
        for c in sorted_cids:
            discord_id = cid_to_discord.get(c)
            name = cid_to_name.get(c, "Unknown Renter")
            if discord_id is not None:
                linked_lines.append(f"👤 **{name}** (CID: {c}) → <@{discord_id}>")
            else:
                unlinked_lines.append(f"👤 **{name}** (CID: {c})")
                
        embed = discord.Embed(
            title="🔗 CID Link Status",
            color=0x3498DB
        )
        embed.add_field(name="Linked CIDs", value=f"**{len(linked_lines)}**", inline=True)
        embed.add_field(name="Unlinked CIDs", value=f"**{len(unlinked_lines)}**", inline=True)
        embed.add_field(name="Total Unique CIDs", value=f"**{len(all_cids)}**", inline=True)
        
        embed.set_footer(text=FOOTER_TEXT)
        view = LinkedStatusOptionsView(linked_lines, unlinked_lines)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="sys",
        description="Display System Information",
    )
    @app_commands.guild_only()
    async def sys_info(self, interaction: discord.Interaction):
        owner_id = os.getenv("OwnerId")
        if not owner_id or str(interaction.user.id) != owner_id.strip():
            return await interaction.response.send_message("❌ Permission Denied. Only the bot owner can use this command.", ephemeral=True)
            
        await interaction.response.defer()
        
        # Bot Statistics
        servers = len(self.bot.guilds)
        users = sum(g.member_count for g in self.bot.guilds if g.member_count)
        
        process = psutil.Process(os.getpid())
        bot_uptime_seconds = int(time.time() - process.create_time())
        bot_uptime = f"{bot_uptime_seconds // 3600} hours"
        
        # Network Info
        ip = "Unknown"
        location = "Unknown"
        isp = "Unknown"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://ip-api.com/json/") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ip = data.get("query", "Unknown")
                        location = f'{data.get("city", "Unknown")}, {data.get("countryCode", "Unknown")}'
                        isp = data.get("isp", "Unknown")
        except Exception:
            pass
            
        # System Info
        sys_os = platform.system()
        version = platform.release()
        plat = sys.platform
        arch = platform.machine()
        
        mem = psutil.virtual_memory()
        mem_used = f"{mem.used / (1024**3):.2f}GB"
        mem_total = f"{mem.total / (1024**3):.2f}GB"
        
        cpu_name = platform.processor()
        if (not cpu_name or cpu_name == "x86_64") and platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if line.startswith("model name"):
                            cpu_name = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass
        cpu_name = cpu_name or "Unknown"
        
        cpu_cores = psutil.cpu_count(logical=True) or "Unknown"
        
        try:
            load_avg = ", ".join(str(round(x, 2)) for x in psutil.getloadavg())
        except AttributeError:
            load_avg = "N/A"
            
        sys_uptime_seconds = int(time.time() - psutil.boot_time())
        sys_uptime = f"{sys_uptime_seconds // 3600} hours"
        
        # Process Info
        python_version = sys.version.split(" ")[0]
        process_mem = f"{process.memory_info().rss / (1024**2):.2f} MB"
        process_uptime_mins = f"{bot_uptime_seconds // 60} minutes"
        pid = os.getpid()
        
        embed = discord.Embed(title="🤖 System Information", color=0x2b2d31)
        
        bot_stats = f"Servers: {servers}\nUsers: {users}\nUptime: {bot_uptime}"
        net_info = f"IP: {ip}\nLocation: {location}\nISP: {isp}"
        
        sys_info_str = (
            f"OS: {sys_os}\n"
            f"Version: {version}\n"
            f"Platform: {plat}\n"
            f"Architecture: {arch}\n"
            f"Memory: {mem_used} / {mem_total}\n"
            f"CPU: {cpu_name}\n"
            f"CPU Cores: {cpu_cores}\n"
            f"Load Avg: {load_avg}\n"
            f"Uptime: {sys_uptime}"
        )
        
        proc_info_str = (
            f"Python: v{python_version}\n"
            f"Memory Usage: {process_mem}\n"
            f"Process Uptime: {process_uptime_mins}\n"
            f"Platform: {plat}\n"
            f"PID: {pid}"
        )
        
        embed.add_field(name="🤖 Bot Statistics", value=bot_stats, inline=True)
        embed.add_field(name="🌐 Network Info", value=net_info, inline=True)
        embed.add_field(name="💻 System Info", value=sys_info_str, inline=False)
        embed.add_field(name="⚙️ Process Info", value=proc_info_str, inline=False)
        embed.set_footer(text=FOOTER_TEXT)
        
        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
