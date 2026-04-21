import discord
from discord import app_commands
from discord.ext import commands
from config import get_rank, RANK_TIERS
import database as db


class ProfileCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View your or another player's profile")
    @app_commands.describe(user="The user to look up (leave blank for yourself)")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        player = await db.get_player(target.id)
        if not player:
            msg = "That player has no profile yet." if user else "You have no profile yet. Use `/join` to get started."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        premium = await db.is_premium(target.id)
        server_id = interaction.guild_id or 0
        rank = await db.get_rank_for_server(player["elo"], server_id)
        total = player["wins"] + player["losses"]
        winrate = f"{(player['wins'] / total * 100):.1f}%" if total > 0 else "N/A"
        season_history = await db.get_season_history(target.id)

        color = discord.Color.purple() if premium else discord.Color.blurple()
        title = f"{'⭐ ' if premium else ''}{target}'s Profile"
        embed = discord.Embed(title=title, color=color)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Rank", value=rank, inline=True)
        embed.add_field(name="ELO", value=str(player["elo"]), inline=True)
        embed.add_field(name="Win Rate", value=winrate, inline=True)
        embed.add_field(name="Wins", value=str(player["wins"]), inline=True)
        embed.add_field(name="Losses", value=str(player["losses"]), inline=True)
        embed.add_field(name="Matches", value=str(total), inline=True)

        if premium:
            embed.add_field(name="Status", value="⭐ Premium Member", inline=False)

        if season_history:
            last = season_history[0]
            embed.add_field(
                name=f"Last Season ({last['season_name']})",
                value=f"{last['rank_title']} · ELO {last['final_elo']} · {last['wins']}W/{last['losses']}L",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show the top 10 players by ELO")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await db.get_leaderboard(10)
        if not rows:
            await interaction.response.send_message("No ranked players yet.", ephemeral=True)
            return

        server_id = interaction.guild_id or 0
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows, 1):
            prefix = medals.get(i, f"`{i}.`")
            star = "⭐ " if row.get("is_premium") else ""
            rank = await db.get_rank_for_server(row["elo"], server_id)
            lines.append(
                f"{prefix} {star}**{row['username']}** — {rank} · ELO {row['elo']} ({row['wins']}W/{row['losses']}L)"
            )

        embed = discord.Embed(
            title="Leaderboard — Top 10",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ranks", description="Show all rank tiers and their ELO thresholds")
    async def show_ranks(self, interaction: discord.Interaction):
        lines = []
        tiers = list(reversed(RANK_TIERS))
        for i, (threshold, name) in enumerate(tiers):
            next_threshold = tiers[i + 1][0] if i + 1 < len(tiers) else None
            if next_threshold:
                lines.append(f"{name} — **{threshold}–{next_threshold - 1}** ELO")
            else:
                lines.append(f"{name} — **{threshold}+** ELO")

        embed = discord.Embed(
            title="Rank Tiers",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)
