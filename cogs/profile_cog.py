import discord
from discord import app_commands
from discord.ext import commands
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
            msg = "That player has not played any matches yet." if user else "You have no profile yet. Use /join to get started."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        total = player["wins"] + player["losses"]
        winrate = f"{(player['wins'] / total * 100):.1f}%" if total > 0 else "N/A"

        embed = discord.Embed(title=f"{target}'s Profile", color=discord.Color.blurple())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="ELO", value=str(player["elo"]), inline=True)
        embed.add_field(name="Wins", value=str(player["wins"]), inline=True)
        embed.add_field(name="Losses", value=str(player["losses"]), inline=True)
        embed.add_field(name="Win Rate", value=winrate, inline=True)
        embed.add_field(name="Matches Played", value=str(total), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show the top 10 players by ELO")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await db.get_leaderboard(10)
        if not rows:
            await interaction.response.send_message("No ranked players yet.", ephemeral=True)
            return

        lines = []
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, row in enumerate(rows, 1):
            prefix = medals.get(i, f"`{i}.`")
            lines.append(f"{prefix} **{row['username']}** — ELO {row['elo']} ({row['wins']}W/{row['losses']}L)")

        embed = discord.Embed(
            title="Leaderboard — Top 10",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)
