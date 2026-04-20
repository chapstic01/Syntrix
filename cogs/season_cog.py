import discord
from discord import app_commands
from discord.ext import commands
from config import ADMIN_USER_ID
import database as db


class SeasonGroup(app_commands.Group, name="season", description="Season management commands"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == ADMIN_USER_ID

    @app_commands.command(name="info", description="Show the current active season")
    async def season_info(self, interaction: discord.Interaction):
        season = await db.get_active_season()
        if not season:
            await interaction.response.send_message("No active season right now.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"Season: {season['name']}",
            description=f"Started: {season['started_at'][:10]}\nStatus: 🟢 Active",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="View your past season results")
    @app_commands.describe(user="Player to look up (leave blank for yourself)")
    async def season_history(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        history = await db.get_season_history(target.id)
        if not history:
            await interaction.response.send_message(
                f"{'That player has' if user else 'You have'} no season history yet.",
                ephemeral=True,
            )
            return

        lines = [
            f"**{h['season_name']}** — {h['rank_title']} · ELO {h['final_elo']} · {h['wins']}W/{h['losses']}L"
            for h in history
        ]
        embed = discord.Embed(
            title=f"{target}'s Season History",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="list", description="Show all past and current seasons")
    async def season_list(self, interaction: discord.Interaction):
        seasons = await db.get_all_seasons()
        if not seasons:
            await interaction.response.send_message("No seasons have been run yet.", ephemeral=True)
            return
        lines = []
        for s in seasons:
            status = "🟢 Active" if s["active"] else f"🔴 Ended {s['ended_at'][:10] if s['ended_at'] else '?'}"
            lines.append(f"**Season {s['season_id']}: {s['name']}** — {status}")
        embed = discord.Embed(
            title="All Seasons",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="start", description="[Admin] Start a new season")
    @app_commands.describe(name="Season name (e.g. Season 1)")
    async def season_start(self, interaction: discord.Interaction, name: str):
        if not self._is_admin(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        existing = await db.get_active_season()
        if existing:
            await interaction.response.send_message(
                f"Season **{existing['name']}** is already active. End it first with `/season end`.",
                ephemeral=True,
            )
            return
        season_id = await db.start_season(name)
        await interaction.response.send_message(
            f"🟢 Season **{name}** (ID: {season_id}) has started!", ephemeral=False
        )

    @app_commands.command(name="end", description="[Admin] End the current season and apply soft ELO reset")
    async def season_end(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        season = await db.get_active_season()
        if not season:
            await interaction.response.send_message("No active season to end.", ephemeral=True)
            return

        await interaction.response.defer()
        await db.end_season(season["season_id"], soft_reset=True)
        embed = discord.Embed(
            title=f"Season {season['name']} Ended",
            description=(
                "All player stats have been archived.\n"
                "ELO has been soft-reset (moved 50% back toward 1000).\n"
                "Use `/season start` to begin a new season."
            ),
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)


class SeasonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.season_group = SeasonGroup(bot)
