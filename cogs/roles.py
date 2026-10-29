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
        name="role-resync",
        description="Resync all roles for all linked users (Linked User Role + Block Roles)",
    )
    @app_commands.guild_only()
    async def role_resync(self, interaction: discord.Interaction):
        """Resync all linked user roles based on current data."""
        await interaction.response.defer(ephemeral=True)

        # Check permissions - admin or trusted users only
        if not await utils.is_admin_or_trusted(interaction):
            return await interaction.followup.send(
                embed=_embed(
                    "❌ Permission Denied",
                    "Only admins or trusted users can resync roles.",
                    0xE74C3C,
                ),
            )

        guild = interaction.guild
        guild_id = guild.id

        # Check if bot has Manage Roles permission
        if not guild.me.guild_permissions.manage_roles:
            return await interaction.followup.send(
                embed=_embed(
                    "❌ Permission Error",
                    "I don't have the **Manage Roles** permission on this server.",
                    0xE74C3C,
                ),
            )

        # Get configuration
        linked_role_id = await db.get_setting(guild_id, "linked_user_role_id")
        block_mappings = await db.list_all_role_mappings(guild_id)

        if not linked_role_id and not block_mappings:
            return await interaction.followup.send(
                embed=_embed(
                    "⚠️ No Roles Configured",
                    "No linked user role or block roles have been configured yet.\n"
                    "Use `/role link-role` and `/role block-role` to set up role assignments.",
                    0xF39C12,
                ),
            )

        # Get all linked users
        all_links = await db.get_all_links(guild_id)
        if not all_links:
            return await interaction.followup.send(
                embed=_embed(
                    "ℹ️ No Linked Users",
                    "No Discord accounts are currently linked to any CIDs.",
                    0x3498DB,
                ),
            )

        # Group links by discord_id
        links_by_user = {}
        for link in all_links:
            discord_id = link.get("discord_id")
            cid = link.get("in_game_cid")
            if discord_id and cid:
                if discord_id not in links_by_user:
                    links_by_user[discord_id] = []
                links_by_user[discord_id].append(cid)

        # Get current rent data
        rent_data = await db.get_all_rent_data(guild_id)
        rent_data_by_cid = {}
        if rent_data:
            for row in rent_data:
                cid = row.get("renter_cid")
                if cid:
                    rent_data_by_cid[cid] = row

        # Track results
        assigned_linked = []
        assigned_block = []
        removed_block = []
        errors = []

        # Process each linked user
        for discord_id, cids in links_by_user.items():
            member = guild.get_member(discord_id)
            if not member:
                errors.append(f"<@{discord_id}>: Member not found in server")
                continue

            # Assign Linked User Role
            if linked_role_id:
                try:
                    role_id = int(linked_role_id)
                    linked_role = guild.get_role(role_id)
                    if linked_role:
                        if linked_role not in member.roles:
                            # Check hierarchy
                            bot_highest = _get_bot_highest_role(guild)
                            if bot_highest and linked_role < bot_highest:
                                await member.add_roles(linked_role, reason="Role resync - linked user")
                                assigned_linked.append(member.display_name)
                    else:
                        errors.append(f"Linked user role not found (ID: {role_id})")
                except discord.Forbidden:
                    errors.append(f"{member.display_name}: Permission denied for linked role")
                except discord.HTTPException as e:
                    errors.append(f"{member.display_name}: API error - {str(e)}")
                except (ValueError, TypeError):
                    errors.append(f"Invalid linked role ID: {linked_role_id}")

            # Process each CID for this user
            for cid in cids:
                renter_data = rent_data_by_cid.get(cid)
                if not renter_data:
                    # CID not in current rent data - remove any block roles
                    old_block = await db.get_renter_block(guild_id, cid)
                    if old_block:
                        old_role_id = block_mappings.get(old_block)
                        if old_role_id:
                            old_role = guild.get_role(old_role_id)
                            if old_role and old_role in member.roles:
                                try:
                                    await member.remove_roles(old_role, reason="Role resync - CID removed from data")
                                    removed_block.append(f"{member.display_name}: {old_role.name}")
                                except discord.Forbidden:
                                    errors.append(f"{member.display_name}: Permission denied to remove {old_role.name}")
                                except discord.HTTPException as e:
                                    errors.append(f"{member.display_name}: API error - {str(e)}")
                        await db.set_renter_block(guild_id, cid, None)
                    continue

                # CID is in rent data - get block and assign role
                address = renter_data.get("address", "")
                block = _extract_block_from_address(address)

                # Get block role
                role_id = block_mappings.get(block)
                if not role_id:
                    continue  # No role configured for this block

                role = guild.get_role(role_id)
                if not role:
                    errors.append(f"Block '{block}': Role not found (ID: {role_id})")
                    continue

                # Check hierarchy
                bot_highest = _get_bot_highest_role(guild)
                if bot_highest and role >= bot_highest:
                    errors.append(f"{member.display_name}: Cannot assign {role.name} (hierarchy issue)")
                    continue

                # Check if already has the role
                if role in member.roles:
                    # Update history
                    await db.set_renter_block(guild_id, cid, block)
                    continue

                # Assign the role
                try:
                    await member.add_roles(role, reason="Role resync - block assignment")
                    assigned_block.append(f"{member.display_name}: {role.name}")
                    # Update history
                    await db.set_renter_block(guild_id, cid, block)
                except discord.Forbidden:
                    errors.append(f"{member.display_name}: Permission denied for {role.name}")
                except discord.HTTPException as e:
                    errors.append(f"{member.display_name}: API error - {str(e)}")

        # Build response embed
        embed = _embed(
            "🔄 Role Resync Complete",
            f"Processed **{len(links_by_user)}** linked users.",
            0x5865F2,
        )

        # Add fields for results
        if assigned_linked:
            embed.add_field(
                name=f"✅ Linked Roles Assigned ({len(assigned_linked)})",
                value="\n".join(assigned_linked[:10]) + ("..." if len(assigned_linked) > 10 else ""),
                inline=False,
            )

        if assigned_block:
            embed.add_field(
                name=f"🏠 Block Roles Assigned ({len(assigned_block)})",
                value="\n".join(assigned_block[:10]) + ("..." if len(assigned_block) > 10 else ""),
                inline=False,
            )

        if removed_block:
            embed.add_field(
                name=f"❌ Block Roles Removed ({len(removed_block)})",
                value="\n".join(removed_block[:10]) + ("..." if len(removed_block) > 10 else ""),
                inline=False,
            )

        if errors:
            embed.add_field(
                name=f"⚠️ Errors ({len(errors)})",
                value="\n".join(errors[:5]) + ("..." if len(errors) > 5 else ""),
                inline=False,
            )

        await interaction.followup.send(embed=embed)

        # Log to channel if configured
        logs_channel_id = await db.get_setting(guild_id, "role_logs_channel_id")
        if logs_channel_id:
            try:
                logs_channel = guild.get_channel(int(logs_channel_id))
                if logs_channel:
                    log_embed = _embed(
                        "🔄 Manual Role Resync",
                        f"Triggered by {interaction.user.mention}",
                        0x5865F2,
                    )
                    log_embed.add_field(
                        name="Summary",
                        value=f"Linked Roles: **{len(assigned_linked)}**\n"
                              f"Block Roles Assigned: **{len(assigned_block)}**\n"
                              f"Block Roles Removed: **{len(removed_block)}**\n"
                              f"Errors: **{len(errors)}**",
                        inline=False,
                    )
                    await logs_channel.send(embed=log_embed)
            except (ValueError, TypeError, discord.Forbidden, discord.HTTPException) as e:
                log.warning(f"Could not send resync log to channel: {e}")

        log.info(f"[Guild {guild_id}] Role resync: {len(assigned_linked)} linked, {len(assigned_block)} block assigned, {len(removed_block)} removed, {len(errors)} errors")

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

    @app_commands.command(
        name="role-remove-link-role",
        description="Remove the configured linked user role",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_remove_link_role(self, interaction: discord.Interaction):
        """Remove the linked user role configuration."""
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id

        # Check if there's a linked role configured
        current_role_id = await db.get_setting(guild_id, "linked_user_role_id")
        if not current_role_id:
            return await interaction.followup.send(
                embed=_embed(
                    "ℹ️ No Linked Role",
                    "No linked user role is currently configured.",
                    0x3498DB,
                ),
            )

        # Get role name for display
        role_name = "Unknown"
        try:
            role = interaction.guild.get_role(int(current_role_id))
            if role:
                role_name = role.name
        except (ValueError, TypeError):
            pass

        # Remove the setting
        await db.set_setting(guild_id, "linked_user_role_id", "")

        await interaction.followup.send(
            embed=_embed(
                "✅ Linked Role Removed",
                f"The linked user role (**{role_name}**) has been removed.",
                0x57F287,
            ),
        )
        log.info(f"Linked user role removed in guild {guild_id}")

    @app_commands.command(
        name="role-remove-block-role",
        description="Remove a block role mapping",
    )
    @app_commands.describe(
        block="The block name to remove the role mapping for",
    )
    @app_commands.default_permissions(administrator=True)
    async def role_remove_block_role(
        self,
        interaction: discord.Interaction,
        block: str,
    ):
        """Remove a block role mapping."""
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id

        # Check if there's a mapping for this block
        current_role_id = await db.get_role_mapping(guild_id, block)
        if not current_role_id:
            return await interaction.followup.send(
                embed=_embed(
                    "ℹ️ No Mapping Found",
                    f"No role mapping exists for block **'{block}'**.",
                    0x3498DB,
                ),
            )

        # Get role name for display
        role_name = "Unknown"
        try:
            role = interaction.guild.get_role(current_role_id)
            if role:
                role_name = role.name
        except (ValueError, TypeError):
            pass

        # Delete the mapping
        await db.delete_role_mapping(guild_id, block)

        await interaction.followup.send(
            embed=_embed(
                "✅ Block Role Removed",
                f"The role mapping for block **'{block}'** (role: **{role_name}**) has been removed.",
                0x57F287,
            ),
        )
        log.info(f"Block role mapping removed: '{block}' in guild {guild_id}")

    @role_remove_block_role.autocomplete("block")
    async def block_remove_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ):
        """Autocomplete for block names - suggest configured blocks."""
        guild_id = interaction.guild_id
        if not guild_id:
            return []

        try:
            block_mappings = await db.list_all_role_mappings(guild_id)
            if block_mappings:
                # Filter by current input
                filtered = [b for b in block_mappings.keys() if current.lower() in b.lower()]
                sorted_blocks = sorted(filtered)[:25]
                return [
                    app_commands.Choice(name=block, value=block)
                    for block in sorted_blocks
                ]
        except Exception as e:
            log.warning(f"Error fetching blocks for autocomplete: {e}")

        return []

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