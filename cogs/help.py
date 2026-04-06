"""Interactive /help command with category navigation."""

import discord
from discord import app_commands
from discord.ext import commands

from config import FOOTER_TEXT


# ── Command Registry ──────────────────────────────────────────
# Each category maps to a list of (name, description, admin_only) tuples.
COMMAND_CATEGORIES = {
    "🔗 Linking": {
        "emoji": "🔗",
        "description": "Link your Discord account to your in-game CID.",
        "commands": [
            ("/link-cid", "Link a CID to your Discord account (admins can link others)", False),
            ("/unlink-cid", "Remove a CID link from your account (admins can unlink others)", False),
            ("/my-cids", "View all CIDs linked to your Discord account", False),
        ],
    },
    "📊 Admin": {
        "emoji": "📊",
        "description": "Server management and reporting tools.",
        "commands": [
            ("/update-data", "Upload rent data via file, pasted CSV, or message link", True),
            ("/rent-summary", "View a full financial summary with status breakdown", True),
            ("/all-links", "View every Discord ↔ CID link in the server", True),
        ],
    },
    "🔔 Reminders": {
        "emoji": "🔔",
        "description": "Automated rent payment reminders.",
        "commands": [
            ("/set-rent-channel", "Set the channel for posting rent reminders", True),
            ("/set-deadline", "Set the rent payment deadline date (MM/DD/YYYY)", True),
            ("/send-reminders", "Manually trigger rent reminders right now", True),
        ],
    },
    "ℹ️ Info": {
        "emoji": "ℹ️",
        "description": "General bot information.",
        "commands": [
            ("/help", "Show this help menu", False),
        ],
    },
}


class CategorySelect(discord.ui.Select):
    """Dropdown to pick a help category."""

    def __init__(self):
        options = []
        for label, data in COMMAND_CATEGORIES.items():
            options.append(
                discord.SelectOption(
                    label=label,
                    emoji=data["emoji"],
                    description=data["description"][:100],
                )
            )
        super().__init__(
            placeholder="Choose a category…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        cat = COMMAND_CATEGORIES[selected]

        lines = []
        for name, desc, admin in cat["commands"]:
            admin_badge = " 🔒" if admin else ""
            lines.append(f"**`{name}`**{admin_badge}\n╰ {desc}")

        embed = discord.Embed(
            title=f"{cat['emoji']}  {selected}",
            description="\n\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text=f"🔒 = Admin only | {FOOTER_TEXT}")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    """Persistent view containing the category dropdown."""

    def __init__(self, *, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.add_item(CategorySelect())
        self.message: discord.Message | None = None

    async def on_timeout(self):
        # Disable the dropdown and push the edit to Discord
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def _overview_embed() -> discord.Embed:
    """Build the main overview embed shown when /help is first invoked."""
    total_cmds = sum(len(c["commands"]) for c in COMMAND_CATEGORIES.values())

    description = (
        "Welcome to **Property Manager Bot** — your all-in-one tool for "
        "managing rental properties, tracking payments, and sending "
        "automated reminders.\n\n"
        "Use the **dropdown menu** below to explore commands by category.\n\n"
    )

    category_lines = []
    for label, data in COMMAND_CATEGORIES.items():
        cmd_count = len(data["commands"])
        category_lines.append(f"{data['emoji']} **{label}** — {cmd_count} command{'s' if cmd_count != 1 else ''}")

    description += "\n".join(category_lines)
    description += f"\n\n📋 **{total_cmds}** commands available in total."

    embed = discord.Embed(
        title="📖  Property Manager — Help",
        description=description,
        color=0x5865F2,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


class HelpCog(commands.Cog):
    """Interactive help command."""

    @app_commands.command(
        name="help",
        description="Show all available commands and how to use them",
    )
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction):
        view = HelpView()
        await interaction.response.send_message(
            embed=_overview_embed(),
            view=view,
            ephemeral=True,
        )
        # Store the message reference so on_timeout can edit it
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
