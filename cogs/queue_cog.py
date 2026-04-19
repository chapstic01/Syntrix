import discord
from discord import app_commands
from discord.ext import commands
import database as db


class QueueCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="join", description="Join the global matchmaking queue")
    async def join_queue(self, interaction: discord.Interaction):
        player = await db.get_or_create_player(
            interaction.user.id, str(interaction.user)
        )
        sp = await db.get_server_player(interaction.user.id, interaction.guild_id) if interaction.guild_id else None
        if sp and sp["banned"]:
            await interaction.response.send_message("You are banned from matchmaking in this server.", ephemeral=True)
            return

        active = await db.get_active_match_for_player(interaction.user.id)
        if active:
            await interaction.response.send_message("You are already in an active match.", ephemeral=True)
            return

        existing = await db.get_queue_entry(interaction.user.id)
        if existing:
            await interaction.response.send_message("You are already in the queue.", ephemeral=True)
            return

        server_id = interaction.guild_id or 0
        await db.enqueue(interaction.user.id, server_id, player["elo"])

        embed = discord.Embed(
            title="Joined Queue",
            description=f"You joined the global matchmaking queue.\n**ELO:** {player['elo']}",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Use /leave to exit the queue at any time.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leave", description="Leave the matchmaking queue")
    async def leave_queue(self, interaction: discord.Interaction):
        entry = await db.get_queue_entry(interaction.user.id)
        if not entry:
            await interaction.response.send_message("You are not in the queue.", ephemeral=True)
            return
        await db.dequeue(interaction.user.id)
        await interaction.response.send_message("You left the queue.", ephemeral=True)

    @app_commands.command(name="queue", description="Show who is currently in the queue")
    async def show_queue(self, interaction: discord.Interaction):
        entries = await db.get_all_queue()
        if not entries:
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        lines = []
        for i, e in enumerate(entries, 1):
            lines.append(f"`{i}.` **{e['username']}** — ELO {e['elo_at_join']}")

        embed = discord.Embed(
            title=f"Matchmaking Queue ({len(entries)} player{'s' if len(entries) != 1 else ''})",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)
