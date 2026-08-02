"""Honeytrap anti-spam/anti-raid system with 24-hour timeout."""

import logging
import discord
from datetime import timedelta
from discord import app_commands
from discord.ext import commands

import database as db

log = logging.getLogger("RentManager.honeytrap")

# Valid action types
VALID_ACTIONS = ["timeout", "ban", "warn", "disable"]
DEFAULT_TIMEOUT_DURATION = 86400  # 24 hours in seconds


class HoneytrapCog(commands.Cog):
    """Honeytrap system to catch spammers/raiders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    honeytrap_group = app_commands.Group(
        name="honeytrap",
        description="Configure the honeytrap anti-spam system"
    )

    @honeytrap_group.command(name="configure", description="Set up the honeytrap channel, log channel, and action")
    @app_commands.describe(
        channel="The channel to monitor as honeytrap (defaults to #honeytrap if exists)",
        log_channel="Channel to log honeytrap actions",
        action="Action to take when triggered: timeout (24h), ban, warn, or disable",
        dm_message="Custom DM message sent to caught users (optional)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Timeout (24 hours)", value="timeout"),
        app_commands.Choice(name="Ban", value="ban"),
        app_commands.Choice(name="Warn", value="warn"),
        app_commands.Choice(name="Disable", value="disable"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def configure(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        log_channel: discord.TextChannel = None,
        action: str = "timeout",
        dm_message: str = None
    ):
        """Configure the honeytrap system for this server."""
        guild = interaction.guild
        
        # Auto-detect #honeytrap channel if not provided
        if channel is None:
            channel = discord.utils.get(guild.text_channels, name="honeytrap")
            if channel is None:
                await interaction.response.send_message(
                    "❌ No honeytrap channel specified and no `#honeytrap` channel found. "
                    "Please specify a channel or create a `#honeytrap` channel first.",
                    ephemeral=True
                )
                return

        # Validate action
        if action not in VALID_ACTIONS:
            await interaction.response.send_message(
                f"❌ Invalid action. Choose from: {', '.join(VALID_ACTIONS)}",
                ephemeral=True
            )
            return

        # Check bot permissions
        bot_member = guild.me
        if not bot_member.guild_permissions.ban_members:
            await interaction.response.send_message(
                "⚠️ Warning: Bot lacks **Ban Members** permission. Timeout/ban actions may fail.",
                ephemeral=True
            )

        if not bot_member.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "⚠️ Warning: Bot lacks **Moderate Members** permission. Timeout action will fail.",
                ephemeral=True
            )

        # Save settings
        await db.update_honeytrap_settings(
            guild.id,
            honeytrap_channel_id=channel.id,
            log_channel_id=log_channel.id if log_channel else None,
            action_type=action,
            enabled=True,
            dm_message=dm_message
        )

        # Build response
        embed = discord.Embed(
            title="✅ Honeytrap Configured",
            color=discord.Color.green()
        )
        embed.add_field(name="Honeytrap Channel", value=channel.mention, inline=True)
        embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="Action", value=action.capitalize(), inline=True)
        embed.add_field(name="Status", value="Enabled", inline=True)
        if dm_message:
            embed.add_field(name="Custom DM", value=dm_message[:100] + ("..." if len(dm_message) > 100 else ""), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @honeytrap_group.command(name="toggle", description="Enable or disable the honeytrap system")
    @app_commands.describe(enabled="Enable or disable the honeytrap")
    @app_commands.default_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, enabled: bool):
        """Enable or disable the honeytrap system."""
        guild = interaction.guild
        settings = await db.get_honeytrap_settings(guild.id)
        
        if not settings:
            await interaction.response.send_message(
                "❌ Honeytrap not configured yet. Use `/honeytrap configure` first.",
                ephemeral=True
            )
            return

        await db.set_honeytrap_setting(guild.id, "enabled", enabled)
        
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"✅ Honeytrap has been **{status}**.",
            ephemeral=True
        )

    @honeytrap_group.command(name="info", description="Show current honeytrap configuration")
    @app_commands.default_permissions(manage_guild=True)
    async def info(self, interaction: discord.Interaction):
        """Display current honeytrap settings."""
        guild = interaction.guild
        settings = await db.get_honeytrap_settings(guild.id)
        
        if not settings:
            await interaction.response.send_message(
                "❌ Honeytrap not configured yet. Use `/honeytrap configure` first.",
                ephemeral=True
            )
            return

        honeytrap_channel = guild.get_channel(settings["honeytrap_channel_id"])
        log_channel = guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None

        embed = discord.Embed(
            title="🛡️ Honeytrap Configuration",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Honeytrap Channel",
            value=honeytrap_channel.mention if honeytrap_channel else f"Deleted channel (ID: {settings['honeytrap_channel_id']})",
            inline=True
        )
        embed.add_field(
            name="Log Channel",
            value=log_channel.mention if log_channel else "Not set",
            inline=True
        )
        embed.add_field(name="Action", value=settings["action_type"].capitalize(), inline=True)
        embed.add_field(name="Status", value="Enabled" if settings["enabled"] else "Disabled", inline=True)
        if settings["dm_message"]:
            embed.add_field(name="Custom DM", value=settings["dm_message"][:200], inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages in the honeytrap channel and take action."""
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Get honeytrap settings for this guild
        settings = await db.get_honeytrap_settings(message.guild.id)
        if not settings or not settings["enabled"]:
            return

        # Check if message is in the honeytrap channel
        if message.channel.id != settings["honeytrap_channel_id"]:
            return

        # Don't act on messages from users with manage_guild permission (admins)
        if message.author.guild_permissions.manage_guild:
            return

        action = settings["action_type"]
        user = message.author
        guild = message.guild

        try:
            if action == "timeout":
                await self._apply_timeout(guild, user, message)
            elif action == "ban":
                await self._apply_ban(guild, user, message)
            elif action == "warn":
                await self._apply_warn(guild, user, message)
            elif action == "disable":
                await self._disable_honeytrap(guild, message)
        except discord.Forbidden:
            log.error(f"Missing permissions to apply {action} in guild {guild.id}")
            # Notify in log channel if possible
            await self._log_action(guild, settings, user, action, message, success=False, error="Missing permissions")
        except Exception as e:
            log.error(f"Error applying honeytrap action: {e}")
            await self._log_action(guild, settings, user, action, message, success=False, error=str(e))

    async def _apply_timeout(self, guild: discord.Guild, user: discord.Member, message: discord.Message):
        """Apply a 24-hour timeout to the user."""
        # Check if user is already timed out
        if user.is_timed_out():
            await self._log_action(guild, await db.get_honeytrap_settings(guild.id), user, "timeout", message, success=False, error="User already timed out")
            return

        # Apply timeout (24 hours = 86400 seconds)
        timeout_duration = discord.utils.utcnow() + timedelta(seconds=DEFAULT_TIMEOUT_DURATION)
        await user.timeout(timeout_duration, reason="Posted in honeytrap channel")
        
        # Send DM
        await self._send_dm(user, guild, "timeout")
        
        # Log action
        await self._log_action(guild, await db.get_honeytrap_settings(guild.id), user, "timeout", message, success=True)
        
        # Delete the triggering message
        try:
            await message.delete()
        except discord.NotFound:
            pass

    async def _apply_ban(self, guild: discord.Guild, user: discord.Member, message: discord.Message):
        """Ban the user from the guild."""
        await guild.ban(user, reason="Posted in honeytrap channel", delete_message_days=0)
        
        # Send DM
        await self._send_dm(user, guild, "ban")
        
        # Log action
        await self._log_action(guild, await db.get_honeytrap_settings(guild.id), user, "ban", message, success=True)
        
        # Delete the triggering message
        try:
            await message.delete()
        except discord.NotFound:
            pass

    async def _apply_warn(self, guild: discord.Guild, user: discord.Member, message: discord.Message):
        """Send a warning to the user in the channel."""
        warning_msg = await message.channel.send(
            f"⚠️ {user.mention}, you have been warned for posting in the honeytrap channel."
        )
        
        # Send DM
        await self._send_dm(user, guild, "warn")
        
        # Log action
        await self._log_action(guild, await db.get_honeytrap_settings(guild.id), user, "warn", message, success=True)
        
        # Delete the triggering message
        try:
            await message.delete()
        except discord.NotFound:
            pass
        
        # Delete warning after 10 seconds
        await warning_msg.delete(delay=10)

    async def _disable_honeytrap(self, guild: discord.Guild, message: discord.Message):
        """Disable the honeytrap for this guild."""
        await db.set_honeytrap_setting(guild.id, "enabled", False)
        
        await message.channel.send("🔒 Honeytrap has been **disabled** for this server.")
        
        # Log action
        await self._log_action(guild, await db.get_honeytrap_settings(guild.id), message.author, "disable", message, success=True)

    async def _send_dm(self, user: discord.User, guild: discord.Guild, action: str):
        """Send a DM to the caught user."""
        settings = await db.get_honeytrap_settings(guild.id)
        
        if settings and settings["dm_message"]:
            dm_text = settings["dm_message"]
        else:
            action_messages = {
                "timeout": f"You have been timed out for 24 hours for posting in the honeytrap channel in **{guild.name}**.",
                "ban": f"You have been banned from **{guild.name}** for posting in the honeytrap channel.",
                "warn": f"You have been warned for posting in the honeytrap channel in **{guild.name}**.",
            }
            dm_text = action_messages.get(action, f"Action taken: {action}")

        try:
            embed = discord.Embed(
                title="🛡️ Honeytrap Alert",
                description=dm_text,
                color=discord.Color.red()
            )
            embed.add_field(name="Server", value=guild.name, inline=True)
            if action == "timeout":
                embed.add_field(name="Duration", value="24 hours", inline=True)
            await user.send(embed=embed)
        except discord.Forbidden:
            # User has DMs disabled, that's fine
            pass

    async def _log_action(
        self,
        guild: discord.Guild,
        settings: dict,
        user: discord.User,
        action: str,
        message: discord.Message,
        success: bool,
        error: str = None
    ):
        """Log the honeytrap action to the log channel."""
        if not settings or not settings["log_channel_id"]:
            return

        log_channel = guild.get_channel(settings["log_channel_id"])
        if not log_channel:
            return

        color = discord.Color.green() if success else discord.Color.red()
        embed = discord.Embed(
            title="🛡️ Honeytrap Action",
            color=color,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{user} ({user.id})", inline=True)
        embed.add_field(name="Action", value=action.capitalize(), inline=True)
        embed.add_field(name="Status", value="Success" if success else "Failed", inline=True)
        
        if message.content:
            embed.add_field(name="Message", value=message.content[:500], inline=False)
        
        if error:
            embed.add_field(name="Error", value=error, inline=False)

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            log.warning(f"Cannot send log to channel {settings['log_channel_id']} in guild {guild.id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(HoneytrapCog(bot))