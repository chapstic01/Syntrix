import discord
from discord import app_commands
from discord.ext import commands
from config import ADMIN_USER_ID
import database as db


class BroadcastModal(discord.ui.Modal, title="Broadcast Syntrix Update"):
    update_title = discord.ui.TextInput(
        label="Title",
        placeholder="e.g. v1.2 — New Features",
        max_length=100,
    )
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="What's new in this update…",
        max_length=1500,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title=self.update_title.value,
            description=self.message.value,
            color=0x7c3aed,
        )
        embed.set_author(name="Syntrix Update", icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Syntrix Global Matchmaking")

        sent, failed = 0, 0
        for guild in self.bot.guilds:
            try:
                cfg = await db.get_server_config(guild.id)
                ch_id = cfg.get("update_channel_id")
                delivered = False
                if ch_id:
                    ch = guild.get_channel(ch_id)
                    if ch:
                        try:
                            await ch.send(embed=embed)
                            delivered = True
                        except Exception:
                            pass
                if not delivered:
                    owner = await self.bot.fetch_user(guild.owner_id)
                    await owner.send(embed=embed)
                sent += 1
            except Exception:
                failed += 1

        await interaction.followup.send(
            f"Broadcast sent to **{sent}** server{'s' if sent != 1 else ''}."
            + (f" ({failed} failed)" if failed else ""),
            ephemeral=True,
        )


class UpdateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="update", description="Broadcast an update announcement to all servers (owner only)")
    async def update_cmd(self, interaction: discord.Interaction):
        if interaction.user.id != ADMIN_USER_ID:
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        await interaction.response.send_modal(BroadcastModal(self.bot))
