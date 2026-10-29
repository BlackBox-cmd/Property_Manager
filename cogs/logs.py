"""Logs channel configuration — /logs and /remove-logs commands."""

import logging
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT

log = logging.getLogger("RentManager.logs")


def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


class LogsCog(commands.Cog):
    """Logs channel configuration commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="logs",
        description="Set the channel for role assignment/removal logs",
    )
    @app_commands.describe(
        channel="The Discord channel for role sync logs",
    )
    @app_commands.default_permissions(administrator=True)
    async def logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Configure the logs channel for role operations."""
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id

        # Store in settings
        await db.set_setting(guild_id, "role_logs_channel_id", str(channel.id))

        log.info(f"Role logs channel configured: #{channel.name} (ID: {channel.id}) in guild {guild_id}")

        embed = _embed(
            "✅ Logs Channel Set",
            f"Role assignment and removal events will be logged to #{channel.name}.",
            0x57F287,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="remove-logs",
        description="Remove the configured logs channel",
    )
    @app_commands.default_permissions(administrator=True)
    async def remove_logs(self, interaction: discord.Interaction):
        """Remove the logs channel configuration."""
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id

        # Check if there's a logs channel configured
        current_channel_id = await db.get_setting(guild_id, "role_logs_channel_id")
        if not current_channel_id:
            return await interaction.followup.send(
                embed=_embed(
                    "ℹ️ No Logs Channel",
                    "No logs channel is currently configured.",
                    0x3498DB,
                ),
            )

        # Get channel name for display
        channel_name = "Unknown"
        try:
            channel = interaction.guild.get_channel(int(current_channel_id))
            if channel:
                channel_name = f"#{channel.name}"
        except (ValueError, TypeError):
            pass

        # Remove the setting
        await db.set_setting(guild_id, "role_logs_channel_id", "")

        await interaction.followup.send(
            embed=_embed(
                "✅ Logs Channel Removed",
                f"The logs channel (**{channel_name}**) has been removed.",
                0x57F287,
            ),
        )
        log.info(f"Role logs channel removed in guild {guild_id}")


async def setup(bot: commands.Bot):
    """Add the logs cog to the bot."""
    await bot.add_cog(LogsCog(bot))