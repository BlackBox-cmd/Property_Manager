"""Role configuration cog — /role commands for managing Discord role assignments."""

import logging
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT

log = logging.getLogger("RentManager.roles")


def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


def _extract_block_from_address(address: str) -> str:
    """Extract block name from address.

    Examples:
        "Forum Drive 2 / Apt 1" -> "Forum Drive 2"
        "Janitor Apartment" -> "Janitor Apartment"
        "Forum Drive 2" -> "Forum Drive 2"
    """
    if "/" in address:
        return address.split("/")[0].strip()
    return address.strip()


def _get_bot_highest_role(guild: discord.Guild) -> discord.Role | None:
    """Get the bot's highest role in the guild."""
    if guild.me.top_role:
        return guild.me.top_role
    return None


def _can_assign_role(bot_member: discord.Member, role: discord.Role) -> bool:
    """Check if the bot can assign the given role to users."""
    return bot_member.top_role >= role and role.id != guild.id


class RolesCog(commands.Cog):
    """Role configuration and management commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="role",
        description="Configure automatic role assignments for linked users",
    )
    @app_commands.guild_only()
    async def role(self, interaction: discord.Interaction):
        """Base command - show help."""
        embed = _embed(
            "Role Configuration",
            "Use the subcommands to configure role assignments:\n\n"
            "• `/role link-role <role>` — Set the role assigned when a user links their CID\n"
            "• `/role block-role <block> <role>` — Map a block address to a Discord role\n"
            "• `/role logs <channel>` — Set the channel for role sync logs\n"
            "• `/role list` — Show all current role configurations",
            0x5865F2
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="role-link-role",
        description="Set the role to assign when a user links their KID",
    )
    @app_commands.describe(
        role="The Discord role to assign to linked users",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_link_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ):
        """Configure the Linked User Role."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        guild_id = guild.id

        # Check if bot has Manage Roles permission
        if not guild.me.guild_permissions.manage_roles:
            return await interaction.followup.send(
                embed=_embed(
                    "❌ Permission Error",
                    "I don't have the **Manage Roles** permission on this server. "
                    "Please grant this permission to configure role assignments.",
                    0xE74C3C,
                ),
            )

        # Check role hierarchy
        bot_highest = _get_bot_highest_role(guild)
        if bot_highest and role >= bot_highest:
            return await interaction.followup.send(
                embed=_embed(
                    "⚠️ Role Hierarchy Issue",
                    f"The role **{role.name}** is higher than or equal to my highest role **{bot_highest.name}**. "
                    "I cannot assign this role. Please move my role higher in the server settings.",
                    0xE74C3C,
                ),
            )

        # Store in settings
        await db.set_setting(guild_id, "linked_user_role_id", str(role.id))

        log.info(f"Linked User Role configured: {role.name} (ID: {role.id}) in guild {guild_id}")

        embed = _embed(
            "✅ Linked User Role Set",
            f"The role **{role.name}** will be automatically assigned when a user links their CID.",
            0x57F287,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="role-block-role",
        description="Map a block address to a Discord role for automatic assignment",
    )
    @app_commands.describe(
        block="The block name (e.g., 'Forum Drive 2')",
        role="The Discord role to assign for this block",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_block_role(
        self,
        interaction: discord.Interaction,
        block: str,
        role: discord.Role,
    ):
        """Configure a block-to-role mapping."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        guild_id = guild.id

        # Check if bot has Manage Roles permission
        if not guild.me.guild_permissions.manage_roles:
            return await interaction.followup.send(
                embed=_embed(
                    "❌ Permission Error",
                    "I don't have the **Manage Roles** permission on this server. "
                    "Please grant this permission to configure role assignments.",
                    0xE74C3C,
                ),
            )

        # Check role hierarchy
        bot_highest = _get_bot_highest_role(guild)
        if bot_highest and role >= bot_highest:
            return await interaction.followup.send(
                embed=_embed(
                    "⚠️ Role Hierarchy Issue",
                    f"The role **{role.name}** is higher than or equal to my highest role **{bot_highest.name}**. "
                    "I cannot assign this role. Please move my role higher in the server settings.",
                    0xE74C3C,
                ),
            )

        # Store the mapping
        await db.set_role_mapping(guild_id, block, role.id)

        log.info(f"Block role mapping configured: '{block}' -> {role.name} (ID: {role.id}) in guild {guild_id}")

        embed = _embed(
            "✅ Block Role Mapping Set",
            f"Block **{block}** will automatically assign the role **{role.name}** to linked users.",
            0x57F287,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="role-logs",
        description="Set the channel for role assignment/removal logs",
    )
    @app_commands.describe(
        channel="The Discord channel for role sync logs",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_logs(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
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
        name="role-list",
        description="Show all current role configurations",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_list(self, interaction: discord.Interaction):
        """Display all configured role mappings."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        guild_id = guild.id

        # Get linked user role
        linked_role_id = await db.get_setting(guild_id, "linked_user_role_id")
        linked_role = None
        if linked_role_id:
            try:
                linked_role = guild.get_role(int(linked_role_id))
            except (ValueError, TypeError):
                pass

        # Get logs channel
        logs_channel_id = await db.get_setting(guild_id, "role_logs_channel_id")
        logs_channel = None
        if logs_channel_id:
            try:
                logs_channel = guild.get_channel(int(logs_channel_id))
            except (ValueError, TypeError):
                pass

        # Get block role mappings
        block_mappings = await db.list_all_role_mappings(guild_id)

        # Build embed
        embed = _embed(
            "🔧 Role Configuration",
            "Current role settings for this server",
            0x5865F2,
        )

        # Linked User Role
        if linked_role:
            embed.add_field(
                name="📌 Linked User Role",
                value=f"**{linked_role.name}**",
                inline=False,
            )
        else:
            embed.add_field(
                name="📌 Linked User Role",
                value="_Not configured_",
                inline=False,
            )

        # Logs Channel
        if logs_channel:
            embed.add_field(
                name="📝 Logs Channel",
                value=f"#{logs_channel.name}",
                inline=False,
            )
        else:
            embed.add_field(
                name="📝 Logs Channel",
                value="_Not configured_",
                inline=False,
            )

        # Block Role Mappings
        if block_mappings:
            mappings_text = []
            for block_name, role_id in sorted(block_mappings.items()):
                role = guild.get_role(role_id)
                if role:
                    mappings_text.append(f"• **{block_name}** → {role.mention}")
                else:
                    mappings_text.append(f"• **{block_name}** → <@{role_id}> (role not found)")
            embed.add_field(
                name="🏠 Block Role Mappings",
                value="\n".join(mappings_text),
                inline=False,
            )
        else:
            embed.add_field(
                name="🏠 Block Role Mappings",
                value="_No block mappings configured_",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @role_block_role.autocomplete("block")
    async def block_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ):
        """Autocomplete for block names - suggest blocks from recent data."""
        guild_id = interaction.guild_id
        if not guild_id:
            return []

        # Get recent block names from rent data
        try:
            rent_data = await db.get_all_rent_data(guild_id)
            if rent_data:
                # Extract unique block names from addresses
                blocks = set()
                for row in rent_data:
                    address = row.get("address", "")
                    if address:
                        block = _extract_block_from_address(address)
                        if current.lower() in block.lower():
                            blocks.add(block)
                # Sort and limit results
                sorted_blocks = sorted(blocks)[:25]
                return [
                    app_commands.Choice(name=block, value=block)
                    for block in sorted_blocks
                ]
        except Exception as e:
            log.warning(f"Error fetching blocks for autocomplete: {e}")

        return []


async def setup(bot: commands.Bot):
    """Add the roles cog to the bot."""
    await bot.add_cog(RolesCog(bot))