import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from config import READY_CHECK_TIMEOUT, REPORT_TIMEOUT
import database as db
from matchmaking import calculate_elo


class ReadyView(discord.ui.View):
    def __init__(self, match_id: int, player1_id: int, player2_id: int, bot):
        super().__init__(timeout=READY_CHECK_TIMEOUT)
        self.match_id = match_id
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.bot = bot
        self.ready_players: set[int] = set()

    @discord.ui.button(label="Ready", style=discord.ButtonStyle.green, emoji="✅")
    async def ready_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.player1_id, self.player2_id):
            await interaction.response.send_message("This is not your match.", ephemeral=True)
            return
        if interaction.user.id in self.ready_players:
            await interaction.response.send_message("You are already ready.", ephemeral=True)
            return

        self.ready_players.add(interaction.user.id)
        await db.set_ready(self.match_id, interaction.user.id)
        await interaction.response.send_message("You are ready!", ephemeral=True)

        if len(self.ready_players) == 2:
            self.stop()
            match = await db.get_match(self.match_id)
            self.bot.dispatch("match_ready", self.match_id, self.player1_id, self.player2_id, match["mode"] if match else "ranked")

    async def on_timeout(self):
        rc = await db.get_ready_check(self.match_id)
        if not rc:
            return
        not_ready = [pid for pid, ready in [(rc["player1_id"], rc["p1_ready"]), (rc["player2_id"], rc["p2_ready"])] if not ready]
        ready = [pid for pid, r in [(rc["player1_id"], rc["p1_ready"]), (rc["player2_id"], rc["p2_ready"])] if r]

        await db.cancel_match(self.match_id)
        for pid in not_ready:
            try:
                user = await self.bot.fetch_user(pid)
                await user.send("You did not ready up in time. Your match has been cancelled.")
            except Exception:
                pass
        for pid in ready:
            try:
                user = await self.bot.fetch_user(pid)
                p = await db.get_or_create_player(pid, str(user))
                entry = await db.get_queue_entry(pid)
                mode = entry["mode"] if entry else "ranked"
                await db.enqueue(pid, 0, p["elo"], mode=mode)
                await user.send("Your opponent did not ready up. You have been re-queued.")
            except Exception:
                pass


class ReportView(discord.ui.View):
    def __init__(self, match_id: int, player1_id: int, player2_id: int, mode: str, bot):
        super().__init__(timeout=REPORT_TIMEOUT)
        self.match_id = match_id
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.mode = mode
        self.bot = bot
        self.reported: dict[int, int] = {}

    async def _try_resolve(self, interaction: discord.Interaction):
        if len(self.reported) < 2:
            return

        votes = list(self.reported.values())
        if votes[0] == votes[1]:
            winner_id = votes[0]
            loser_id = self.player2_id if winner_id == self.player1_id else self.player1_id
            winner = await db.get_player(winner_id)
            loser = await db.get_player(loser_id)

            if self.mode == "ranked":
                new_w, new_l = calculate_elo(winner["elo"], loser["elo"])
                await db.update_player_elo(winner_id, new_w, won=True)
                await db.update_player_elo(loser_id, new_l, won=False)
                elo_change = f"+{new_w - winner['elo']} ELO → {new_w}"
                loser_change = f"{new_l - loser['elo']} ELO → {new_l}"
            else:
                new_w, new_l = winner["elo"], loser["elo"]
                elo_change = "No ELO change (casual)"
                loser_change = "No ELO change (casual)"

            await db.complete_match(self.match_id, winner_id)
            self.stop()

            w_user = await self.bot.fetch_user(winner_id)
            l_user = await self.bot.fetch_user(loser_id)
            embed = discord.Embed(
                title="Match Result",
                description=(
                    f"**Winner:** {w_user.mention} ({elo_change})\n"
                    f"**Loser:** {l_user.mention} ({loser_change})"
                ),
                color=discord.Color.gold(),
            )
            try:
                await interaction.message.edit(embed=embed, view=None)
            except Exception:
                pass
        else:
            await interaction.followup.send(
                "Results conflict. An admin can resolve with `/admin forcewinner`.",
            )
            await db.cancel_match(self.match_id)
            self.stop()

    @discord.ui.button(label="I Won", style=discord.ButtonStyle.green)
    async def i_won(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.player1_id, self.player2_id):
            await interaction.response.send_message("This is not your match.", ephemeral=True)
            return
        if interaction.user.id in self.reported:
            await interaction.response.send_message("You already reported.", ephemeral=True)
            return
        self.reported[interaction.user.id] = interaction.user.id
        await interaction.response.send_message("Reported: you won.", ephemeral=True)
        await self._try_resolve(interaction)

    @discord.ui.button(label="I Lost", style=discord.ButtonStyle.red)
    async def i_lost(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.player1_id, self.player2_id):
            await interaction.response.send_message("This is not your match.", ephemeral=True)
            return
        if interaction.user.id in self.reported:
            await interaction.response.send_message("You already reported.", ephemeral=True)
            return
        opponent = self.player2_id if interaction.user.id == self.player1_id else self.player1_id
        self.reported[interaction.user.id] = opponent
        await interaction.response.send_message("Reported: you lost.", ephemeral=True)
        await self._try_resolve(interaction)


class MatchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_match_found(self, match_id: int, p1_id: int, p2_id: int, mode: str = "ranked"):
        try:
            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            mode_data = await db.get_queue_mode(mode)
            mode_label = mode_data["display_name"] if mode_data else mode.title()

            embed = discord.Embed(
                title=f"Match Found! — {mode_label}",
                description=(
                    f"**{p1}** vs **{p2}**\n\n"
                    f"Click **Ready** within {READY_CHECK_TIMEOUT}s to confirm."
                ),
                color=discord.Color.orange(),
            )
            view = ReadyView(match_id, p1_id, p2_id, self.bot)
            for user in (p1, p2):
                try:
                    await user.send(embed=embed, view=view)
                except discord.Forbidden:
                    pass
        except Exception as e:
            print(f"[match_found] {e}")

    @commands.Cog.listener()
    async def on_match_ready(self, match_id: int, p1_id: int, p2_id: int, mode: str = "ranked"):
        try:
            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            p1_data = await db.get_player(p1_id)
            p2_data = await db.get_player(p2_id)
            mode_data = await db.get_queue_mode(mode)
            mode_label = mode_data["display_name"] if mode_data else mode.title()
            elo_note = "ELO is **not** affected in this mode." if mode != "ranked" else "ELO will be updated after reporting."

            embed = discord.Embed(
                title=f"Both Players Ready — {mode_label}",
                description=(
                    f"**{p1}** (ELO {p1_data['elo']}) vs **{p2}** (ELO {p2_data['elo']})\n\n"
                    f"{elo_note}\nReport the result below. You have {REPORT_TIMEOUT // 60} minutes."
                ),
                color=discord.Color.green(),
            )
            report_view = ReportView(match_id, p1_id, p2_id, mode, self.bot)
            for user in (p1, p2):
                try:
                    await user.send(embed=embed, view=report_view)
                except discord.Forbidden:
                    pass
        except Exception as e:
            print(f"[match_ready] {e}")

    @app_commands.command(name="match", description="View your current active match")
    async def view_match(self, interaction: discord.Interaction):
        match = await db.get_active_match_for_player(interaction.user.id)
        if not match:
            await interaction.response.send_message("You have no active match.", ephemeral=True)
            return
        p1 = await self.bot.fetch_user(match["player1_id"])
        p2 = await self.bot.fetch_user(match["player2_id"])
        embed = discord.Embed(
            title=f"Match #{match['match_id']} — {match['mode'].title()}",
            description=f"**{p1}** vs **{p2}**\nStatus: `{match['status']}`",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cancel", description="Cancel your current active match")
    async def cancel_match(self, interaction: discord.Interaction):
        match = await db.get_active_match_for_player(interaction.user.id)
        if not match:
            await interaction.response.send_message("You have no active match.", ephemeral=True)
            return
        await db.cancel_match(match["match_id"])
        await interaction.response.send_message(f"Match #{match['match_id']} cancelled.", ephemeral=True)
        other_id = match["player2_id"] if interaction.user.id == match["player1_id"] else match["player1_id"]
        try:
            other = await self.bot.fetch_user(other_id)
            await other.send(f"Your match #{match['match_id']} was cancelled by your opponent.")
        except Exception:
            pass
