import discord
from discord import app_commands
from discord.ext import commands
from config import get_rank
import database as db


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"<t:{int(dt.timestamp())}:d>"
    except Exception:
        return iso[:10]


class HistoryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="history", description="View recent match history for yourself or another player")
    @app_commands.describe(user="Player to look up (defaults to you)", limit="Number of matches to show (max 15)")
    async def history(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
        limit: app_commands.Range[int, 1, 15] = 10,
    ):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        matches = await db.get_match_history(target.id, limit)

        if not matches:
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f":x: No completed matches found for **{target.display_name}**.",
                    color=0xED4245,
                ),
                ephemeral=True,
            )
            return

        lines = []
        for m in matches:
            result = m["result"]
            if result == "win":
                icon = "🟢"
                label = "Win"
            elif result == "loss":
                icon = "🔴"
                label = "Loss"
            else:
                icon = "⚪"
                label = "Cancelled"

            mode_tag = f"`{m['mode']}`" if m["mode"] != "ranked" else ""
            date = _fmt_date(m.get("completed_at") or m.get("created_at"))
            opponent = m["opponent_name"]
            match_id = m["match_id"]
            parts = [f"{icon} **{label}** vs **{opponent}**", f"· {date}", f"· #{match_id}"]
            if mode_tag:
                parts.append(f"· {mode_tag}")
            lines.append(" ".join(parts))

        player = await db.get_player(target.id)
        rank = get_rank(player["elo"]) if player else "—"
        elo = player["elo"] if player else "—"

        wins = sum(1 for m in matches if m["result"] == "win")
        losses = sum(1 for m in matches if m["result"] == "loss")

        embed = discord.Embed(
            title=f"Match History — {target.display_name}",
            description="\n".join(lines),
            color=0x7c3aed,
        )
        embed.add_field(name="ELO", value=str(elo), inline=True)
        embed.add_field(name="Rank", value=rank, inline=True)
        embed.add_field(name=f"Last {len(matches)}", value=f"{wins}W / {losses}L", inline=True)
        embed.set_footer(text=f"Showing last {len(matches)} matches  •  /profile for full stats")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="stats", description="Show matchmaking statistics for this server")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild_id:
            await interaction.followup.send(
                embed=discord.Embed(description=":x: This command must be used in a server.", color=0xED4245)
            )
            return

        s = await db.get_server_stats(interaction.guild_id)
        queue = await db.get_all_queue()
        server_queue = [e for e in queue if e.get("server_id") == interaction.guild_id]

        embed = discord.Embed(
            title=f"Syntrix Stats — {interaction.guild.name}",
            color=0x7c3aed,
        )
        embed.add_field(name="Total Matches", value=str(s["total_matches"] or 0), inline=True)
        embed.add_field(name="Completed", value=str(s["completed"] or 0), inline=True)
        embed.add_field(name="Cancelled", value=str(s["cancelled"] or 0), inline=True)
        embed.add_field(name="Active Now", value=str(s["active"] or 0), inline=True)
        embed.add_field(name="In Queue", value=str(len(server_queue)), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        active_season = await db.get_active_season()
        embed.set_footer(
            text=f"Season: {active_season['name'] if active_season else 'None'}  •  Global queue — matches cross all servers"
        )
        await interaction.followup.send(embed=embed)
