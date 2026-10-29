"""Self-service CID linking: /link-cid, /unlink-cid (multi-server)."""

import logging
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import utils
from config import FOOTER_TEXT

log = logging.getLogger("RentManager.linking")


def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


async def _is_admin(interaction: discord.Interaction) -> bool:
    """Return True if the member has admin permissions OR is in the trusted users list."""
    # Check Discord permissions first
    perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
    if perms and (perms.administrator or perms.manage_guild):
        return True
    # Check database for trusted user status
    return await db.is_trusted_user(interaction.guild_id, interaction.user.id)


def _get_bot_highest_role(guild: discord.Guild) -> discord.Role | None:
    """Get the bot's highest role in the guild."""
    if guild.me.top_role:
        return guild.me.top_role
    return None


async def _assign_linked_user_role(member: discord.Member) -> str | None:
    """Assign the linked user role to a member. Returns None on success, error message on failure."""
    guild = member.guild
    guild_id = guild.id

    # Get linked user role ID from settings
    linked_role_id = await db.get_setting(guild_id, "linked_user_role_id")
    if not linked_role_id:
        return None  # No role configured

    try:
        role_id = int(linked_role_id)
    except (ValueError, TypeError):
        return f"Invalid linked role ID in settings: {linked_role_id}"

    # Get the role object
    role = guild.get_role(role_id)
    if not role:
        return f"Linked user role not found (ID: {role_id})"

    # Check if bot can assign this role
    bot_member = guild.me
    if not bot_member.guild_permissions.manage_roles:
        return "Bot lacks Manage Roles permission"

    bot_highest = _get_bot_highest_role(guild)
    if bot_highest and role >= bot_highest:
        return f"Role {role.name} is higher than bot's highest role {bot_highest.name}"

    # Check if member already has the role
    if role in member.roles:
        return None  # Already has role

    # Assign the role
    try:
        await member.add_roles(role, reason="Linked user role assignment")
        return None
    except discord.Forbidden:
        return f"Permission denied to assign role {role.name}"
    except discord.HTTPException as e:
        return f"Discord API error: {str(e)}"


async def _remove_linked_user_role(member: discord.Member) -> str | None:
    """Remove the linked user role from a member. Returns None on success, error message on failure."""
    guild = member.guild
    guild_id = guild.id

    # Get linked user role ID from settings
    linked_role_id = await db.get_setting(guild_id, "linked_user_role_id")
    if not linked_role_id:
        return None  # No role configured

    try:
        role_id = int(linked_role_id)
    except (ValueError, TypeError):
        return None  # Invalid ID, nothing to remove

    # Get the role object
    role = guild.get_role(role_id)
    if not role:
        return None  # Role doesn't exist, nothing to remove

    # Check if member has the role
    if role not in member.roles:
        return None  # Member doesn't have role

    # Remove the role
    try:
        await member.remove_roles(role, reason="Linked user role removal")
        return None
    except discord.Forbidden:
        return f"Permission denied to remove role {role.name}"
    except discord.HTTPException as e:
        return f"Discord API error: {str(e)}"


async def _remove_all_block_roles(member: discord.Member, cid: int) -> tuple[list[str], list[str]]:
    """Remove all block roles from a member for a specific CID.
    Returns tuple of (successes, errors).
    """
    guild = member.guild
    guild_id = guild.id

    # Get all block roles the user might have
    block_mappings = await db.list_all_role_mappings(guild_id)
    successes = []
    errors = []

    for block_name, role_id in block_mappings.items():
        role = guild.get_role(role_id)
        if not role:
            continue  # Role doesn't exist

        # Check if member has this role
        if role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Unlinking CID {cid}")
                successes.append(block_name)
            except discord.Forbidden:
                errors.append(f"Permission denied to remove role {role.name}")
            except discord.HTTPException as e:
                errors.append(f"Discord API error for {role.name}: {str(e)}")

    # Update renter block history
    await db.set_renter_block(guild_id, cid, None)

    return successes, errors


async def _log_role_operation(guild_id: int, log_text: str) -> None:
    """Log role operations to the configured channel."""
    try:
        logs_channel_id = await db.get_setting(guild_id, "role_logs_channel_id")
        if not logs_channel_id:
            return  # No logs channel configured

        # Get the bot instance - we'll need to fetch it properly
        from bot import bot
        guild = bot.get_guild(guild_id)
        if not guild:
            return

        channel = guild.get_channel(int(logs_channel_id))
        if not channel:
            return

        embed = _embed(
            "🔧 Role Management",
            log_text,
            0x5865F2,
        )
        await channel.send(embed=embed)
    except Exception as e:
        log.warning(f"Error logging role operation: {e}")


class LinkingCog(commands.Cog):
    """Self-service CID linking for players."""

    pass

    @app_commands.command(
        name="link-cid",
        description="Link a Discord account to an in-game CID",
    )
    @app_commands.describe(
        cid="The in-game Character ID to link (e.g., 292)",
        target_user="(Admin only) The Discord user to link the CID to",
    )
    @app_commands.guild_only()
    async def link_cid(
        self,
        interaction: discord.Interaction,
        cid: int,
        target_user: discord.Member = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        # If targeting another user, require admin
        if target_user is not None and target_user.id != interaction.user.id:
            if not await _is_admin(interaction):
                return await interaction.followup.send(
                    embed=_embed(
                        "❌ Permission Denied",
                        "Only admins or trusted users can link a CID to another user.",
                        0xE74C3C,
                    ),
                )
            subject = target_user
        else:
            subject = interaction.user

        result = await db.link_cid(guild_id, subject.id, cid)

        if result == "linked":
            all_cids = await db.get_cids_for_user(guild_id, subject.id)
            cid_list = ", ".join(str(c) for c in all_cids)

            # Try to assign linked user role
            role_error = await _assign_linked_user_role(subject)

            if subject.id == interaction.user.id:
                desc = (
                    f"Your Discord account is now linked to CID **{cid}**.\n\n"
                    f"**Your linked CIDs:** {cid_list}\n\n"
                    "You will receive rent reminders for all properties under this CID."
                )
            else:
                desc = (
                    f"{subject.mention} is now linked to CID **{cid}**.\n\n"
                    f"**Their linked CIDs:** {cid_list}"
                )

            # Add role assignment status
            if role_error:
                desc += f"\n\n⚠️ Role assignment failed: {role_error}"

            await interaction.followup.send(
                embed=_embed("✅ CID Linked", desc, 0x2ECC71),
            )

        elif result == "already_linked":
            target_label = "your account" if subject.id == interaction.user.id else subject.mention
            await interaction.followup.send(
                embed=_embed(
                    "⚠️ Already Linked",
                    f"CID **{cid}** is already linked to {target_label}.",
                    0xF39C12,
                ),
            )

        elif result == "cid_taken":
            owner_id = await db.get_discord_id_for_cid(guild_id, cid)
            owner_mention = f"<@{owner_id}>" if owner_id else "another user"
            await interaction.followup.send(
                embed=_embed(
                    "❌ CID Already Owned",
                    f"CID **{cid}** is already linked to {owner_mention}.\n\n"
                    "Each CID can only belong to one user per server.\n"
                    "An admin can `/unlink-cid` it from the current owner first.",
                    0xE74C3C,
                ),
            )

    @app_commands.command(
        name="unlink-cid",
        description="Remove a CID link from a Discord account",
    )
    @app_commands.describe(
        cid="The CID to unlink",
        target_user="(Admin only) The Discord user to unlink the CID from",
    )
    @app_commands.guild_only()
    async def unlink_cid(
        self,
        interaction: discord.Interaction,
        cid: int,
        target_user: discord.Member = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        # If targeting another user, require admin
        if target_user is not None and target_user.id != interaction.user.id:
            if not await _is_admin(interaction):
                return await interaction.followup.send(
                    embed=_embed(
                        "❌ Permission Denied",
                        "Only admins or trusted users can unlink a CID from another user.",
                        0xE74C3C,
                    ),
                )
            subject = target_user
        else:
            subject = interaction.user

        removed = await db.unlink_cid(guild_id, subject.id, cid)
        if removed:
            remaining = await db.get_cids_for_user(guild_id, subject.id)

            # Remove linked user role if no more CIDs remain
            role_removals = []
            role_errors = []
            if not remaining:
                role_error = await _remove_linked_user_role(subject)
                if role_error:
                    role_errors.append(role_error)
                else:
                    role_removals.append("Linked User Role")

            # Remove block roles for this CID
            block_successes, block_errors = await _remove_all_block_roles(subject, cid)
            role_removals.extend(block_successes)
            role_errors.extend(block_errors)

            if subject.id == interaction.user.id:
                desc = f"CID **{cid}** has been unlinked from your account."
                if remaining:
                    desc += f"\n\n**Remaining CIDs:** {', '.join(str(c) for c in remaining)}"
                else:
                    desc += "\n\nYou have no more linked CIDs."
            else:
                desc = f"CID **{cid}** has been unlinked from {subject.mention}."
                if remaining:
                    desc += f"\n\n**Their remaining CIDs:** {', '.join(str(c) for c in remaining)}"
                else:
                    desc += f"\n\n{subject.mention} has no more linked CIDs."

            # Add role removal status
            if role_removals:
                desc += f"\n\n✅ Removed roles: {', '.join(role_removals)}"
            if role_errors:
                desc += f"\n\n⚠️ Role removal errors: {'; '.join(role_errors)}"

            await interaction.followup.send(
                embed=_embed("✅ CID Unlinked", desc, 0x2ECC71),
            )
        else:
            target_label = "your account" if subject.id == interaction.user.id else subject.mention
            await interaction.followup.send(
                embed=_embed(
                    "❌ Not Found",
                    f"CID **{cid}** is not linked to {target_label}.",
                    0xE74C3C,
                ),
            )

    @app_commands.command(
        name="my-cids",
        description="View all CIDs linked to your Discord account",
    )
    @app_commands.guild_only()
    async def my_cids(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        cids = await db.get_cids_for_user(guild_id, interaction.user.id)
        if cids:
            cid_list = "\n".join(f"• CID **{c}**" for c in cids)
            await interaction.followup.send(
                embed=_embed("🔗 Your Linked CIDs", cid_list, 0x3498DB),
            )
        else:
            await interaction.followup.send(
                embed=_embed(
                    "🔗 No CIDs Linked",
                    "You haven't linked any CIDs yet.\nUse `/link-cid` to link your in-game character.",
                    0x95A5A6,
                ),
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkingCog(bot))
