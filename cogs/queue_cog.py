import discord
from discord import app_commands
from discord.ext import commands
from config import get_rank
import database as db


async def mode_autocomplete(interaction: discord.Interaction, current: str):
    modes = await db.get_queue_modes()
    return [
        app_commands.Choice(name=m["display_name"], value=m["mode_id"])
        for m in modes if current.lower() in m["display_name"].lower()
    ]


class QueueCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="join", description="Join the matchmaking queue")
    @app_commands.describe(mode="Queue mode to join (default: ranked)")
    @app_commands.autocomplete(mode=mode_autocomplete)
    async def join_queue(self, interaction: discord.Interaction, mode: str = "ranked"):
        mode_data = await db.get_queue_mode(mode)
        if not mode_data:
            await interaction.response.send_message(
                f"Unknown or disabled queue mode `{mode}`. Use `/modes` to see available modes.",
                ephemeral=True,
            )
            return

        player = await db.get_or_create_player(interaction.user.id, str(interaction.user))

        sp = await db.get_server_player(interaction.user.id, interaction.guild_id) if interaction.guild_id else None
        if sp and sp["banned"]:
            await interaction.response.send_message(
                "You are banned from matchmaking in this server.", ephemeral=True
            )
            return

        if await db.get_active_match_for_player(interaction.user.id):
            await interaction.response.send_message("You already have an active match.", ephemeral=True)
            return

        existing = await db.get_queue_entry(interaction.user.id)
        if existing:
            await interaction.response.send_message("You are already in the queue.", ephemeral=True)
            return

        premium = await db.is_premium(interaction.user.id)
        server_id = interaction.guild_id or 0
        await db.enqueue(interaction.user.id, server_id, player["elo"], mode=mode)

        rank = get_rank(player["elo"])
        color = discord.Color.purple() if premium else discord.Color.green()
        embed = discord.Embed(
            title=f"{'⭐ ' if premium else ''}Joined {mode_data['display_name']} Queue",
            description=(
                f"**ELO:** {player['elo']} · **Rank:** {rank}\n"
                f"{'**Premium:** Wider match range active ✨\n' if premium else ''}"
                f"Use `/leave` to exit at any time."
            ),
            color=color,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leave", description="Leave the matchmaking queue")
    async def leave_queue(self, interaction: discord.Interaction):
        if not await db.get_queue_entry(interaction.user.id):
            await interaction.response.send_message("You are not in the queue.", ephemeral=True)
            return
        await db.dequeue(interaction.user.id)
        await interaction.response.send_message("You left the queue.", ephemeral=True)

    @app_commands.command(name="queue", description="Show who is currently in the queue")
    @app_commands.describe(mode="Filter by queue mode (default: all)")
    @app_commands.autocomplete(mode=mode_autocomplete)
    async def show_queue(self, interaction: discord.Interaction, mode: str = None):
        entries = await db.get_all_queue(mode=mode)
        if not entries:
            label = f"`{mode}` queue" if mode else "queue"
            await interaction.response.send_message(f"The {label} is empty.", ephemeral=True)
            return

        lines = []
        for i, e in enumerate(entries, 1):
            premium = await db.is_premium(e["discord_id"])
            star = "⭐ " if premium else ""
            lines.append(f"`{i}.` {star}**{e['username']}** — ELO {e['elo_at_join']} · `{e['mode']}`")

        embed = discord.Embed(
            title=f"Matchmaking Queue ({len(entries)} player{'s' if len(entries) != 1 else ''})",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="modes", description="List all available queue modes")
    async def list_modes(self, interaction: discord.Interaction):
        modes = await db.get_queue_modes()
        if not modes:
            await interaction.response.send_message("No queue modes available.", ephemeral=True)
            return

        lines = [f"**{m['display_name']}** (`{m['mode_id']}`)\n{m['description']}" for m in modes]
        embed = discord.Embed(
            title="Available Queue Modes",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use /join mode:<name> to choose a mode")
        await interaction.response.send_message(embed=embed)
