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
        interaction.client.dispatch("queue_changed")

        rank = get_rank(player["elo"])
        color = discord.Color.purple() if premium else discord.Color.green()
        mode_name = mode_data["display_name"]
        prefix = "⭐ " if premium else ""
        premium_line = "**Premium:** Wider match range active ✨\n" if premium else ""
        player_elo = player["elo"]
        embed = discord.Embed(
            title=f"{prefix}Joined {mode_name} Queue",
            description=(
                f"**ELO:** {player_elo} · **Rank:** {rank}\n"
                f"{premium_line}"
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
        interaction.client.dispatch("queue_changed")
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

        mode_label = mode or "all modes"
        join_hint = f"`/join{' mode:' + mode if mode else ''}`"
        embed = discord.Embed(
            title=f"Matchmaking Queue ({len(entries)} player{'s' if len(entries) != 1 else ''})",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Want to play?",
            value=f"Type {join_hint} to jump in and get matched!",
            inline=False,
        )
        embed.set_footer(text=f"Showing: {mode_label}  •  Syntrix Global Matchmaking")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="recruit", description="Post a public embed encouraging others to join a queue")
    @app_commands.describe(mode="Queue mode to recruit for (default: ranked)")
    @app_commands.autocomplete(mode=mode_autocomplete)
    async def recruit(self, interaction: discord.Interaction, mode: str = "ranked"):
        mode_data = await db.get_queue_mode(mode)
        if not mode_data:
            await interaction.response.send_message(
                f"Unknown mode `{mode}`. Use `/modes` to see available modes.", ephemeral=True
            )
            return

        entries = await db.get_all_queue(mode=mode)
        count = len(entries)
        mode_name = mode_data["display_name"]

        if count == 0:
            status = "The queue is empty — be the first in!"
        elif count == 1:
            status = f"**1 player** is already waiting. Jump in and get matched instantly!"
        else:
            status = f"**{count} players** are waiting. Join now for a fast match!"

        embed = discord.Embed(
            title=f"🎮 {mode_name} Queue is Open!",
            description=(
                f"{status}\n\n"
                f"Type `/join mode:{mode}` to enter the queue and get matched by ELO.\n"
                f"Ready checks are sent via DM — make sure your DMs are open."
            ),
            color=discord.Color.from_str("#7c3aed"),
        )
        embed.add_field(name="Mode", value=mode_name, inline=True)
        embed.add_field(name="In Queue", value=str(count), inline=True)
        embed.add_field(name="How to Join", value=f"`/join mode:{mode}`", inline=True)
        embed.set_footer(text="Syntrix Global Matchmaking  •  ELO-based fair matches")
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
