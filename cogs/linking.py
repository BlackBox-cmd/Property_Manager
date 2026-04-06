"""Self-service CID linking: /link-cid, /unlink-cid (multi-server)."""

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from config import FOOTER_TEXT


def _embed(title: str, description: str, color: int) -> discord.Embed:
    """Build an embed with the global footer."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


def _is_admin(member: discord.Member) -> bool:
    """Return True if the member has administrator or manage_guild permission."""
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


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
        guild_id = interaction.guild_id

        # If targeting another user, require admin
        if target_user is not None and target_user.id != interaction.user.id:
            if not _is_admin(interaction.user):
                return await interaction.response.send_message(
                    embed=_embed(
                        "❌ Permission Denied",
                        "Only admins can link a CID to another user.",
                        0xE74C3C,
                    ),
                    ephemeral=True,
                )
            subject = target_user
        else:
            subject = interaction.user

        result = db.link_cid(guild_id, subject.id, cid)

        if result == "linked":
            all_cids = db.get_cids_for_user(guild_id, subject.id)
            cid_list = ", ".join(str(c) for c in all_cids)

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

            await interaction.response.send_message(
                embed=_embed("✅ CID Linked", desc, 0x2ECC71),
                ephemeral=True,
            )

        elif result == "already_linked":
            target_label = "your account" if subject.id == interaction.user.id else subject.mention
            await interaction.response.send_message(
                embed=_embed(
                    "⚠️ Already Linked",
                    f"CID **{cid}** is already linked to {target_label}.",
                    0xF39C12,
                ),
                ephemeral=True,
            )

        elif result == "cid_taken":
            owner_id = db.get_discord_id_for_cid(guild_id, cid)
            owner_mention = f"<@{owner_id}>" if owner_id else "another user"
            await interaction.response.send_message(
                embed=_embed(
                    "❌ CID Already Owned",
                    f"CID **{cid}** is already linked to {owner_mention}.\n\n"
                    "Each CID can only belong to one user per server.\n"
                    "An admin can `/unlink-cid` it from the current owner first.",
                    0xE74C3C,
                ),
                ephemeral=True,
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
        guild_id = interaction.guild_id

        # If targeting another user, require admin
        if target_user is not None and target_user.id != interaction.user.id:
            if not _is_admin(interaction.user):
                return await interaction.response.send_message(
                    embed=_embed(
                        "❌ Permission Denied",
                        "Only admins can unlink a CID from another user.",
                        0xE74C3C,
                    ),
                    ephemeral=True,
                )
            subject = target_user
        else:
            subject = interaction.user

        removed = db.unlink_cid(guild_id, subject.id, cid)
        if removed:
            remaining = db.get_cids_for_user(guild_id, subject.id)

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

            await interaction.response.send_message(
                embed=_embed("✅ CID Unlinked", desc, 0x2ECC71),
                ephemeral=True,
            )
        else:
            target_label = "your account" if subject.id == interaction.user.id else subject.mention
            await interaction.response.send_message(
                embed=_embed(
                    "❌ Not Found",
                    f"CID **{cid}** is not linked to {target_label}.",
                    0xE74C3C,
                ),
                ephemeral=True,
            )

    @app_commands.command(
        name="my-cids",
        description="View all CIDs linked to your Discord account",
    )
    @app_commands.guild_only()
    async def my_cids(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        cids = db.get_cids_for_user(guild_id, interaction.user.id)
        if cids:
            cid_list = "\n".join(f"• CID **{c}**" for c in cids)
            await interaction.response.send_message(
                embed=_embed("🔗 Your Linked CIDs", cid_list, 0x3498DB),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=_embed(
                    "🔗 No CIDs Linked",
                    "You haven't linked any CIDs yet.\nUse `/link-cid` to link your in-game character.",
                    0x95A5A6,
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkingCog(bot))
