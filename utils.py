import discord
import database as db

async def is_admin_or_trusted(interaction: discord.Interaction) -> bool:
    """Check if the user is a server admin or a trusted user."""
    # 1. Native Discord permissions check (Administrator or Manage Server)
    if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
        return True
        
    # 2. Check Database for Trusted User status
    return await db.is_trusted_user(interaction.guild_id, interaction.user.id)
