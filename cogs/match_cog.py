import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from config import READY_CHECK_TIMEOUT, REPORT_TIMEOUT, GAMES
import database as db
from matchmaking import calculate_elo


# ── Channel helpers ───────────────────────────────────────────────────────────

async def _create_match_channels(bot, match_id: int, p1_id: int, p2_id: int, guild_id: int):
    try:
        guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        if not guild:
            return
        cfg = await db.get_server_config(guild_id)
        category_id = cfg.get("match_category_id")
        category = guild.get_channel(category_id) if category_id else None

        deny_all = discord.PermissionOverwrite(view_channel=False)
        allow_me = discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True)
        allow_player = discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, read_message_history=True)

        overwrites = {guild.default_role: deny_all, guild.me: allow_me}
        for uid in (p1_id, p2_id):
            m = guild.get_member(uid)
            if m:
                overwrites[m] = allow_player

        voice_ch = await guild.create_voice_channel(
            f"match-{match_id}-voice", category=category, overwrites=overwrites
        )
        text_ch = await guild.create_text_channel(
            f"match-{match_id}-chat", category=category, overwrites=overwrites
        )
        await db.set_match_channels(match_id, voice_ch.id, text_ch.id)
        return voice_ch, text_ch
    except Exception as e:
        print(f"[channels] Could not create match channels: {e}")
        return None, None


async def _delete_match_channels(bot, match: dict):
    vc_id = match.get("voice_channel_id")
    tc_id = match.get("text_channel_id")
    for ch_id in (vc_id, tc_id):
        if not ch_id:
            continue
        try:
            ch = bot.get_channel(ch_id)
            if ch:
                await ch.delete(reason=f"Match #{match['match_id']} ended")
        except Exception:
            pass


# ── Map vote ──────────────────────────────────────────────────────────────────

class MapVoteSelect(discord.ui.Select):
    def __init__(self, maps: list[str], match_id: int, player_id: int, other_id: int, mode: str, bot):
        self.match_id = match_id
        self.player_id = player_id
        self.other_id = other_id
        self.mode = mode
        self.bot = bot
        options = [discord.SelectOption(label=m, value=m) for m in maps[:25]]
        super().__init__(placeholder="Vote for a map…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This is not your vote.", ephemeral=True)
            return
        await db.submit_map_vote(self.match_id, self.player_id, self.values[0])
        await interaction.response.edit_message(
            content=f"Voted for **{self.values[0]}**. Waiting for your opponent…",
            view=None,
        )
        votes = await db.get_map_votes(self.match_id)
        if len(votes) == 2:
            choices = [v["map_choice"] for v in votes]
            chosen = choices[0] if choices[0] == choices[1] else random.choice(choices)
            await db.set_match_map(self.match_id, chosen)
            match = await db.get_match(self.match_id)
            self.bot.dispatch("map_resolved", self.match_id, self.player_id, self.other_id, self.mode, chosen, match)


class MapVoteView(discord.ui.View):
    def __init__(self, maps: list[str], match_id: int, player_id: int, other_id: int, mode: str, bot):
        super().__init__(timeout=60)
        self.add_item(MapVoteSelect(maps, match_id, player_id, other_id, mode, bot))

    async def on_timeout(self):
        pass


# ── Score reporting ───────────────────────────────────────────────────────────

class ScoreModal(discord.ui.Modal, title="Report Match Score"):
    my_score = discord.ui.TextInput(label="Your score", placeholder="e.g. 16", max_length=6)
    opp_score = discord.ui.TextInput(label="Opponent's score", placeholder="e.g. 9", max_length=6)
    evidence = discord.ui.TextInput(
        label="Evidence URL (screenshot)",
        placeholder="https://imgur.com/...",
        required=False,
        max_length=300,
    )

    def __init__(self, view: "ScoreView", require_evidence: bool):
        super().__init__()
        if require_evidence:
            self.evidence.required = True
            self.evidence.label = "Evidence URL (required)"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ms = int(self.my_score.value.strip())
            os_ = int(self.opp_score.value.strip())
        except ValueError:
            await interaction.response.send_message("Scores must be numbers.", ephemeral=True)
            return
        view: ScoreView = self.view  # type: ignore
        if view is None:
            await interaction.response.send_message("This report session has expired.", ephemeral=True)
            return
        await interaction.response.defer()
        await view.submit_score(interaction, ms, os_)


class ScoreView(discord.ui.View):
    def __init__(self, match_id: int, p1_id: int, p2_id: int, mode: str, bot,
                 require_evidence: bool = False, rounds: int = 0):
        super().__init__(timeout=REPORT_TIMEOUT)
        self.match_id = match_id
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.mode = mode
        self.bot = bot
        self.require_evidence = require_evidence
        self.rounds = rounds
        self.scores: dict[int, tuple[int, int]] = {}  # player_id → (my_score, opp_score)

    @discord.ui.button(label="Report Score", style=discord.ButtonStyle.primary, emoji="📊")
    async def report_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("This is not your match.", ephemeral=True)
            return
        if interaction.user.id in self.scores:
            await interaction.response.send_message("You already reported.", ephemeral=True)
            return
        modal = ScoreModal(self, self.require_evidence)
        modal.view = self
        await interaction.response.send_modal(modal)

    async def submit_score(self, interaction: discord.Interaction, my_score: int, opp_score: int):
        pid = interaction.user.id
        self.scores[pid] = (my_score, opp_score)
        await db.set_match_score(self.match_id, pid, my_score, self.p1_id)

        try:
            await interaction.message.edit(
                content=f"Score reported: **{my_score}–{opp_score}**. Waiting for opponent…",
                view=None if len(self.scores) >= 2 else self,
            )
        except Exception:
            pass

        if len(self.scores) == 2:
            await self._resolve()

    async def _resolve(self):
        match = await db.get_match(self.match_id)
        if not match or match["status"] != "active":
            return

        p1_scores = self.scores.get(self.p1_id)
        p2_scores = self.scores.get(self.p2_id)
        if not p1_scores or not p2_scores:
            return

        p1_my, p1_opp = p1_scores
        p2_my, p2_opp = p2_scores

        # Determine each player's claimed winner
        p1_claims_win = p1_my > p1_opp
        p2_claims_win = p2_my > p2_opp

        if p1_claims_win == (not p2_claims_win):
            # Agreement: p1 wins if p1 claimed win, p2 wins if p2 claimed win
            winner_id = self.p1_id if p1_claims_win else self.p2_id
            loser_id = self.p2_id if p1_claims_win else self.p1_id
            w_score = p1_my if p1_claims_win else p2_my
            l_score = p1_opp if p1_claims_win else p2_opp

            winner = await db.get_player(winner_id)
            loser = await db.get_player(loser_id)
            if self.mode == "ranked":
                new_w, new_l = calculate_elo(winner["elo"], loser["elo"])
                await db.update_player_elo(winner_id, new_w, won=True)
                await db.update_player_elo(loser_id, new_l, won=False)
                elo_msg = f"+{new_w - winner['elo']} ELO → {new_w}"
                l_elo_msg = f"{new_l - loser['elo']} ELO → {new_l}"
            else:
                new_w, new_l = winner["elo"], loser["elo"]
                elo_msg = "No ELO change"
                l_elo_msg = "No ELO change"

            await db.complete_match(self.match_id, winner_id)
            self.stop()

            w_user = await self.bot.fetch_user(winner_id)
            l_user = await self.bot.fetch_user(loser_id)
            embed = discord.Embed(
                title="Match Result",
                description=(
                    f"**Winner:** {w_user.mention} — Score **{w_score}–{l_score}** ({elo_msg})\n"
                    f"**Loser:** {l_user.mention} ({l_elo_msg})"
                ),
                color=discord.Color.gold(),
            )
            # Post to results channel
            await _post_result(self.bot, match, embed)
            await _delete_match_channels(self.bot, match)
        elif p1_claims_win and p2_claims_win:
            # Both claim to have won — conflict
            await db.cancel_match(self.match_id)
            self.stop()
            for uid in (self.p1_id, self.p2_id):
                try:
                    u = await self.bot.fetch_user(uid)
                    await u.send(
                        f"⚠️ Match #{self.match_id} — conflicting scores reported. "
                        "An admin can resolve with `/admin forcewinner`."
                    )
                except Exception:
                    pass
            await _delete_match_channels(self.bot, match)
        else:
            # Tie (both reported same score on each side) — flag for admin
            await db.cancel_match(self.match_id)
            self.stop()
            await _delete_match_channels(self.bot, match)


# ── Legacy win/loss reporting (used when score_mode is off) ───────────────────

class ReportView(discord.ui.View):
    def __init__(self, match_id: int, p1_id: int, p2_id: int, mode: str, bot):
        super().__init__(timeout=REPORT_TIMEOUT)
        self.match_id = match_id
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.mode = mode
        self.bot = bot
        self.reported: dict[int, int] = {}

    async def _try_resolve(self, interaction: discord.Interaction):
        if len(self.reported) < 2:
            return
        votes = list(self.reported.values())
        if votes[0] == votes[1]:
            winner_id = votes[0]
            loser_id = self.p2_id if winner_id == self.p1_id else self.p1_id
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
            match = await db.get_match(self.match_id)
            await _post_result(self.bot, match, embed)
            await _delete_match_channels(self.bot, match)
        else:
            await interaction.followup.send(
                "Results conflict. An admin can resolve with `/admin forcewinner`."
            )
            match = await db.get_match(self.match_id)
            await db.cancel_match(self.match_id)
            await _delete_match_channels(self.bot, match)
            self.stop()

    @discord.ui.button(label="I Won", style=discord.ButtonStyle.green)
    async def i_won(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1_id, self.p2_id):
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
        if interaction.user.id not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("This is not your match.", ephemeral=True)
            return
        if interaction.user.id in self.reported:
            await interaction.response.send_message("You already reported.", ephemeral=True)
            return
        opponent = self.p2_id if interaction.user.id == self.p1_id else self.p1_id
        self.reported[interaction.user.id] = opponent
        await interaction.response.send_message("Reported: you lost.", ephemeral=True)
        await self._try_resolve(interaction)


# ── Ready check ───────────────────────────────────────────────────────────────

class ReadyView(discord.ui.View):
    def __init__(self, match_id: int, p1_id: int, p2_id: int, bot):
        super().__init__(timeout=READY_CHECK_TIMEOUT)
        self.match_id = match_id
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.bot = bot
        self.ready_players: set[int] = set()

    @discord.ui.button(label="Ready", style=discord.ButtonStyle.green, emoji="✅")
    async def ready_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1_id, self.p2_id):
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
            mode = match["mode"] if match else "ranked"
            self.bot.dispatch("match_ready", self.match_id, self.p1_id, self.p2_id, mode)

    async def on_timeout(self):
        rc = await db.get_ready_check(self.match_id)
        if not rc:
            return
        not_ready = [pid for pid, r in [(rc["player1_id"], rc["p1_ready"]), (rc["player2_id"], rc["p2_ready"])] if not r]
        ready = [pid for pid, r in [(rc["player1_id"], rc["p1_ready"]), (rc["player2_id"], rc["p2_ready"])] if r]
        match = await db.get_match(self.match_id)
        await db.cancel_match(self.match_id)
        if match:
            await _delete_match_channels(self.bot, match)
        for pid in not_ready:
            try:
                u = await self.bot.fetch_user(pid)
                await u.send("You did not ready up in time. Your match has been cancelled.")
            except Exception:
                pass
        for pid in ready:
            try:
                u = await self.bot.fetch_user(pid)
                p = await db.get_or_create_player(pid, str(u))
                entry = await db.get_queue_entry(pid)
                mode = entry["mode"] if entry else "ranked"
                await db.enqueue(pid, 0, p["elo"], mode=mode)
                await u.send("Your opponent did not ready up. You have been re-queued.")
            except Exception:
                pass


# ── Result posting helper ─────────────────────────────────────────────────────

async def _post_result(bot, match: dict | None, embed: discord.Embed):
    if not match:
        return
    server_id = match.get("origin_server")
    if not server_id:
        return
    try:
        cfg = await db.get_server_config(server_id)
        ch_id = cfg.get("results_channel_id")
        if ch_id:
            ch = bot.get_channel(ch_id)
            if ch:
                await ch.send(embed=embed)
    except Exception:
        pass

    # Also post in match text channel if it exists
    tc_id = match.get("text_channel_id")
    if tc_id:
        try:
            tc = bot.get_channel(tc_id)
            if tc:
                await tc.send(embed=embed)
        except Exception:
            pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class MatchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_match_found(self, match_id: int, p1_id: int, p2_id: int, mode: str = "ranked"):
        try:
            match = await db.get_match(match_id)
            server_id = match["origin_server"] if match else 0
            cfg = await db.get_server_config(server_id) if server_id else {}
            anonymous = bool(cfg.get("anonymous_queue")) if cfg else False

            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            mode_data = await db.get_queue_mode(mode)
            mode_label = mode_data["display_name"] if mode_data else mode.title()

            p1_name = "Anonymous" if anonymous else str(p1)
            p2_name = "Anonymous" if anonymous else str(p2)

            embed = discord.Embed(
                title=f"Match Found — {mode_label}",
                description=(
                    f"**{p1_name}** vs **{p2_name}**\n\n"
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
            match = await db.get_match(match_id)
            server_id = match["origin_server"] if match else 0
            cfg = await db.get_server_config(server_id) if server_id else {}

            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            p1_data = await db.get_player(p1_id)
            p2_data = await db.get_player(p2_id)
            mode_data = await db.get_queue_mode(mode)
            mode_label = mode_data["display_name"] if mode_data else mode.title()

            rounds = int(cfg.get("rounds_per_match") or 0)
            rounds_note = f"\nPlaying to: **{rounds} rounds**" if rounds else ""
            elo_note = "ELO is **not** affected in this mode." if mode != "ranked" else "ELO will be updated after reporting."

            # Create voice + text channels
            vc, tc = await _create_match_channels(self.bot, match_id, p1_id, p2_id, server_id)
            channels_note = ""
            if vc and tc:
                channels_note = f"\n\nVoice: {vc.mention} · Chat: {tc.mention}"

            embed = discord.Embed(
                title=f"Both Players Ready — {mode_label}",
                description=(
                    f"**{p1}** (ELO {p1_data['elo']}) vs **{p2}** (ELO {p2_data['elo']})\n\n"
                    f"{elo_note}{rounds_note}{channels_note}"
                ),
                color=discord.Color.green(),
            )

            # Check if this server has a game assigned to this queue mode
            game_id = await db.get_server_queue_game(server_id, mode) if server_id else None
            maps = GAMES.get(game_id, {}).get("maps", []) if game_id else []

            if maps:
                embed.add_field(name="Map Vote", value="Vote for your preferred map below.", inline=False)
                for user, uid, other_id in ((p1, p1_id, p2_id), (p2, p2_id, p1_id)):
                    vote_view = MapVoteView(maps, match_id, uid, other_id, mode, self.bot)
                    try:
                        await user.send(embed=embed, view=vote_view)
                    except discord.Forbidden:
                        pass
            else:
                # No map voting — go straight to reporting
                score_mode = bool(cfg.get("score_mode"))
                require_ev = bool(cfg.get("require_evidence"))
                report_view = (
                    ScoreView(match_id, p1_id, p2_id, mode, self.bot, require_ev, rounds)
                    if score_mode
                    else ReportView(match_id, p1_id, p2_id, mode, self.bot)
                )
                embed.add_field(
                    name="Report Result",
                    value=f"You have {REPORT_TIMEOUT // 60} minutes to report.",
                    inline=False,
                )
                for user in (p1, p2):
                    try:
                        await user.send(embed=embed, view=report_view)
                    except discord.Forbidden:
                        pass

        except Exception as e:
            print(f"[match_ready] {e}")

    @commands.Cog.listener()
    async def on_map_resolved(self, match_id: int, p1_id: int, p2_id: int, mode: str, chosen_map: str, match: dict):
        try:
            server_id = match["origin_server"] if match else 0
            cfg = await db.get_server_config(server_id) if server_id else {}
            score_mode = bool(cfg.get("score_mode"))
            require_ev = bool(cfg.get("require_evidence"))
            rounds = int(cfg.get("rounds_per_match") or 0)

            embed = discord.Embed(
                title=f"Map Selected — {chosen_map}",
                description=f"Good luck! Report the result below. You have {REPORT_TIMEOUT // 60} minutes.",
                color=discord.Color.blurple(),
            )
            if rounds:
                embed.add_field(name="Playing to", value=str(rounds), inline=True)

            report_view = (
                ScoreView(match_id, p1_id, p2_id, mode, self.bot, require_ev, rounds)
                if score_mode
                else ReportView(match_id, p1_id, p2_id, mode, self.bot)
            )

            for uid in (p1_id, p2_id):
                try:
                    user = await self.bot.fetch_user(uid)
                    await user.send(embed=embed, view=report_view)
                except Exception:
                    pass

            # Post map to text channel
            tc_id = match.get("text_channel_id")
            if tc_id:
                tc = self.bot.get_channel(tc_id)
                if tc:
                    try:
                        await tc.send(embed=embed)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[map_resolved] {e}")

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
        if match.get("map_played"):
            embed.add_field(name="Map", value=match["map_played"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cancel", description="Cancel your current active match")
    async def cancel_match_cmd(self, interaction: discord.Interaction):
        match = await db.get_active_match_for_player(interaction.user.id)
        if not match:
            await interaction.response.send_message("You have no active match.", ephemeral=True)
            return
        await db.cancel_match(match["match_id"])
        await _delete_match_channels(self.bot, match)
        await interaction.response.send_message(f"Match #{match['match_id']} cancelled.", ephemeral=True)
        other_id = match["player2_id"] if interaction.user.id == match["player1_id"] else match["player1_id"]
        try:
            other = await self.bot.fetch_user(other_id)
            await other.send(f"Your match #{match['match_id']} was cancelled by your opponent.")
        except Exception:
            pass
