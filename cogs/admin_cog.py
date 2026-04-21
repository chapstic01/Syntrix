import discord
from discord import app_commands
from discord.ext import commands
from config import ADMIN_USER_ID, GAMES, MAX_GAMES_FREE, MAX_GAMES_PREMIUM
import database as db
from matchmaking import calculate_elo


async def game_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=v["name"], value=k)
        for k, v in GAMES.items()
        if current.lower() in v["name"].lower()
    ][:25]


async def mode_autocomplete(interaction: discord.Interaction, current: str):
    modes = await db.get_queue_modes()
    return [
        app_commands.Choice(name=m["display_name"], value=m["mode_id"])
        for m in modes if current.lower() in m["display_name"].lower()
    ]


def is_bot_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == ADMIN_USER_ID


class AdminGroup(app_commands.Group, name="admin", description="Admin-only player management"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    def _check(self, interaction: discord.Interaction) -> bool:
        return is_bot_admin(interaction)

    @app_commands.command(name="setelo", description="Set a player's ELO directly")
    @app_commands.describe(user="Target player", elo="New ELO value")
    async def set_elo(self, interaction: discord.Interaction, user: discord.User, elo: int):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if elo < 0:
            await interaction.response.send_message("ELO cannot be negative.", ephemeral=True)
            return
        await db.get_or_create_player(user.id, str(user))
        await db.set_player_elo_direct(user.id, elo)
        await interaction.response.send_message(f"Set {user}'s ELO to {elo}.", ephemeral=True)

    @app_commands.command(name="resetstats", description="Reset a player's stats to defaults")
    @app_commands.describe(user="Target player")
    async def reset_stats(self, interaction: discord.Interaction, user: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        await db.reset_player_stats(user.id)
        await interaction.response.send_message(f"Reset {user}'s stats.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a player from matchmaking in this server")
    @app_commands.describe(user="Player to ban", reason="Reason for ban")
    async def ban_player(self, interaction: discord.Interaction, user: discord.User, reason: str = ""):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.upsert_server_player(user.id, interaction.guild_id, banned=1, notes=reason)
        await db.dequeue(user.id)
        await interaction.response.send_message(
            f"Banned {user} from matchmaking in this server. Reason: {reason or 'none'}",
            ephemeral=True,
        )

    @app_commands.command(name="unban", description="Unban a player in this server")
    @app_commands.describe(user="Player to unban")
    async def unban_player(self, interaction: discord.Interaction, user: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.upsert_server_player(user.id, interaction.guild_id, banned=0)
        await interaction.response.send_message(f"Unbanned {user}.", ephemeral=True)

    @app_commands.command(name="forcewinner", description="Force a match result by match ID")
    @app_commands.describe(match_id="Match ID", winner="Winning player")
    async def force_winner(self, interaction: discord.Interaction, match_id: int, winner: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        match = await db.get_match(match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return
        if winner.id not in (match["player1_id"], match["player2_id"]):
            await interaction.response.send_message("That player is not in this match.", ephemeral=True)
            return

        loser_id = match["player2_id"] if winner.id == match["player1_id"] else match["player1_id"]
        w_data = await db.get_player(winner.id)
        l_data = await db.get_player(loser_id)
        new_w, new_l = calculate_elo(w_data["elo"], l_data["elo"])
        await db.update_player_elo(winner.id, new_w, won=True)
        await db.update_player_elo(loser_id, new_l, won=False)
        await db.complete_match(match_id, winner.id)

        loser = await self.bot.fetch_user(loser_id)
        await interaction.response.send_message(
            f"Match #{match_id} resolved. Winner: **{winner}** (+{new_w - w_data['elo']} ELO → {new_w}). "
            f"Loser: **{loser}** ({new_l - l_data['elo']} ELO → {new_l}).",
            ephemeral=True,
        )

    @app_commands.command(name="removequeue", description="Remove a player from the queue")
    @app_commands.describe(user="Player to remove")
    async def remove_queue(self, interaction: discord.Interaction, user: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        await db.dequeue(user.id)
        await interaction.response.send_message(f"Removed {user} from the queue.", ephemeral=True)

    @app_commands.command(name="setup", description="Configure the bot for this server")
    @app_commands.describe(
        queue_channel="Channel for queue updates",
        results_channel="Channel for match results",
    )
    async def setup_server(
        self,
        interaction: discord.Interaction,
        queue_channel: discord.TextChannel = None,
        results_channel: discord.TextChannel = None,
    ):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        kwargs = {}
        if queue_channel:
            kwargs["queue_channel_id"] = queue_channel.id
        if results_channel:
            kwargs["results_channel_id"] = results_channel.id
        if kwargs:
            await db.update_server_config(interaction.guild_id, **kwargs)
        await interaction.response.send_message("Server config updated.", ephemeral=True)

    # ── Premium management ────────────────────────────────────────────────────

    @app_commands.command(name="grantpremium", description="Manually grant premium to a user")
    @app_commands.describe(user="User to grant premium to")
    async def grant_premium(self, interaction: discord.Interaction, user: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        await db.get_or_create_player(user.id, str(user))
        await db.grant_premium(user.id, "admin-granted", granted_by=interaction.user.id)
        await interaction.response.send_message(f"Granted premium to {user}.", ephemeral=True)
        try:
            await user.send("⭐ You have been granted **Syntrix Premium** by an admin! Use `/premium` to see your perks.")
        except Exception:
            pass

    @app_commands.command(name="revokepremium", description="Revoke premium from a user")
    @app_commands.describe(user="User to revoke premium from")
    async def revoke_premium(self, interaction: discord.Interaction, user: discord.User):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        await db.revoke_premium(user.id)
        await interaction.response.send_message(f"Revoked premium from {user}.", ephemeral=True)

    # ── Queue mode management ─────────────────────────────────────────────────

    @app_commands.command(name="addmode", description="Add a new queue mode")
    @app_commands.describe(mode_id="Short ID (e.g. 2v2)", display_name="Display name", description="Description")
    async def add_mode(self, interaction: discord.Interaction, mode_id: str, display_name: str, description: str = ""):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        mode_id = mode_id.lower().replace(" ", "_")
        await db.create_queue_mode(mode_id, display_name, description)
        await interaction.response.send_message(f"Added queue mode `{mode_id}` ({display_name}).", ephemeral=True)

    @app_commands.command(name="removemode", description="Remove a queue mode")
    @app_commands.describe(mode_id="Mode ID to remove")
    async def remove_mode(self, interaction: discord.Interaction, mode_id: str):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if mode_id in ("ranked", "casual"):
            await interaction.response.send_message("Cannot remove built-in modes.", ephemeral=True)
            return
        await db.delete_queue_mode(mode_id)
        await interaction.response.send_message(f"Removed queue mode `{mode_id}`.", ephemeral=True)

    # ── Game & map configuration ───────────────────────────────────────────────

    @app_commands.command(name="setgame", description="Assign a game (and its maps) to a queue mode for this server")
    @app_commands.describe(mode="Queue mode to configure", game="Game to assign")
    @app_commands.autocomplete(mode=mode_autocomplete, game=game_autocomplete)
    async def set_game(self, interaction: discord.Interaction, mode: str, game: str):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        if game not in GAMES:
            await interaction.response.send_message("Unknown game.", ephemeral=True)
            return

        server_premium = await db.is_server_premium(interaction.guild_id)
        limit = MAX_GAMES_PREMIUM if server_premium else MAX_GAMES_FREE
        current = await db.get_server_queue_games(interaction.guild_id)
        already_set = any(g["queue_mode"] == mode for g in current)
        if not already_set and len(current) >= limit:
            await interaction.response.send_message(
                f"This server can have at most **{limit}** game{'s' if limit > 1 else ''} configured. "
                f"{'Upgrade to server premium for up to 3.' if not server_premium else ''}",
                ephemeral=True,
            )
            return

        await db.set_server_queue_game(interaction.guild_id, mode, game)
        game_name = GAMES[game]["name"]
        maps = GAMES[game]["maps"]
        map_note = f" ({len(maps)} maps available)" if maps else " (no map voting — no maps configured)"
        await interaction.response.send_message(
            f"Queue `{mode}` → **{game_name}**{map_note}.", ephemeral=True
        )

    @app_commands.command(name="removegame", description="Remove the game assignment from a queue mode")
    @app_commands.describe(mode="Queue mode to clear")
    @app_commands.autocomplete(mode=mode_autocomplete)
    async def remove_game(self, interaction: discord.Interaction, mode: str):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.remove_server_queue_game(interaction.guild_id, mode)
        await interaction.response.send_message(f"Removed game from queue `{mode}`.", ephemeral=True)

    @app_commands.command(name="listgames", description="List game assignments for this server")
    async def list_games(self, interaction: discord.Interaction):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        rows = await db.get_server_queue_games(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("No games configured for this server.", ephemeral=True)
            return
        lines = []
        for r in rows:
            g = GAMES.get(r["game_id"], {})
            lines.append(f"**{r['queue_mode']}** → {g.get('name', r['game_id'])} ({len(g.get('maps', []))} maps)")
        embed = discord.Embed(title="Server Game Configuration", description="\n".join(lines), color=0x7c3aed)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Match settings ────────────────────────────────────────────────────────

    @app_commands.command(name="scoremode", description="Toggle score-based result reporting (instead of Win/Loss buttons)")
    @app_commands.describe(enabled="True to enable score reporting")
    async def score_mode(self, interaction: discord.Interaction, enabled: bool):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, score_mode=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(f"Score mode {state}.", ephemeral=True)

    @app_commands.command(name="requireevidence", description="Require players to submit a screenshot URL when reporting scores")
    @app_commands.describe(enabled="True to require evidence")
    async def require_evidence(self, interaction: discord.Interaction, enabled: bool):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, require_evidence=1 if enabled else 0)
        state = "required" if enabled else "not required"
        await interaction.response.send_message(f"Evidence {state}.", ephemeral=True)

    @app_commands.command(name="setrounds", description="Set the target number of rounds per match (0 = unlimited)")
    @app_commands.describe(rounds="Target rounds (e.g. 16 for first to 16). Set 0 to disable.")
    async def set_rounds(self, interaction: discord.Interaction, rounds: app_commands.Range[int, 0, 999]):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, rounds_per_match=rounds)
        note = f"Rounds per match set to **{rounds}**." if rounds else "Round limit removed (unlimited)."
        await interaction.response.send_message(note, ephemeral=True)

    @app_commands.command(name="rematchcooldown", description="Set how long (minutes) before two players can be matched again")
    @app_commands.describe(minutes="Cooldown in minutes. Set 0 to disable.")
    async def rematch_cooldown(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 0, 1440]):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, rematch_cooldown=minutes * 60)
        note = f"Rematch cooldown set to **{minutes} minutes**." if minutes else "Rematch cooldown disabled."
        await interaction.response.send_message(note, ephemeral=True)

    @app_commands.command(name="anonymous", description="Toggle anonymous queue — hides player names until both are ready")
    @app_commands.describe(enabled="True to enable anonymous mode")
    async def anonymous_queue(self, interaction: discord.Interaction, enabled: bool):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, anonymous_queue=1 if enabled else 0)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(f"Anonymous queue {state}.", ephemeral=True)

    @app_commands.command(name="matchcategory", description="Set the category where match voice/text channels are created")
    @app_commands.describe(category="Category channel for match rooms")
    async def match_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, match_category_id=category.id)
        await interaction.response.send_message(
            f"Match channels will be created under **{category.name}**.", ephemeral=True
        )

    @app_commands.command(name="setupdate", description="Set the channel where bot update announcements are posted")
    @app_commands.describe(channel="Channel to receive Syntrix update announcements")
    async def set_update_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, update_channel_id=channel.id)
        await interaction.response.send_message(
            f"Update announcements will be posted to {channel.mention}.", ephemeral=True
        )

    @app_commands.command(name="serverpremium", description="Grant or revoke server premium (unlocks 3 game slots)")
    @app_commands.describe(enabled="True to grant server premium")
    async def server_premium(self, interaction: discord.Interaction, enabled: bool):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.update_server_config(interaction.guild_id, server_premium=1 if enabled else 0)
        state = "granted" if enabled else "revoked"
        await interaction.response.send_message(f"Server premium {state}.", ephemeral=True)

    @app_commands.command(name="serversettings", description="Show all current matchmaking settings for this server")
    async def server_settings(self, interaction: discord.Interaction):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        cfg = await db.get_server_config(interaction.guild_id)
        games = await db.get_server_queue_games(interaction.guild_id)
        game_lines = "\n".join(
            f"• `{g['queue_mode']}` → {GAMES.get(g['game_id'], {}).get('name', g['game_id'])}"
            for g in games
        ) or "None configured"
        cooldown_m = int(cfg.get("rematch_cooldown") or 0) // 60
        embed = discord.Embed(title=f"Server Settings — {interaction.guild.name}", color=0x7c3aed)
        embed.add_field(name="Score Mode", value="On" if cfg.get("score_mode") else "Off", inline=True)
        embed.add_field(name="Require Evidence", value="Yes" if cfg.get("require_evidence") else "No", inline=True)
        embed.add_field(name="Rounds/Match", value=str(cfg.get("rounds_per_match") or "Unlimited"), inline=True)
        embed.add_field(name="Rematch Cooldown", value=f"{cooldown_m}m" if cooldown_m else "Off", inline=True)
        embed.add_field(name="Anonymous Queue", value="On" if cfg.get("anonymous_queue") else "Off", inline=True)
        embed.add_field(name="Server Premium", value="Yes" if cfg.get("server_premium") else "No", inline=True)
        embed.add_field(name="Game Assignments", value=game_lines, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class AdminRankGroup(app_commands.Group, name="adminranks", description="Custom server rank management (server premium)"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    def _check(self, interaction: discord.Interaction) -> bool:
        return is_bot_admin(interaction)

    @app_commands.command(name="add", description="Add a custom rank tier for this server (server premium required)")
    @app_commands.describe(min_elo="Minimum ELO for this rank", name="Rank name", emoji="Emoji prefix (optional)")
    async def add_rank(self, interaction: discord.Interaction, min_elo: app_commands.Range[int, 0, 9999], name: str, emoji: str = ""):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        if not await db.is_server_premium(interaction.guild_id):
            await interaction.response.send_message("Custom ranks require **server premium**.", ephemeral=True)
            return
        await db.add_server_rank(interaction.guild_id, min_elo, name, emoji)
        display = f"{emoji} {name}" if emoji else name
        await interaction.response.send_message(f"Rank **{display}** added at ≥{min_elo} ELO.", ephemeral=True)

    @app_commands.command(name="remove", description="Remove a custom rank tier")
    @app_commands.describe(min_elo="The minimum ELO of the rank to remove")
    async def remove_rank(self, interaction: discord.Interaction, min_elo: int):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        await db.remove_server_rank(interaction.guild_id, min_elo)
        await interaction.response.send_message(f"Rank at ≥{min_elo} ELO removed.", ephemeral=True)

    @app_commands.command(name="list", description="Show this server's custom rank tiers")
    async def list_ranks(self, interaction: discord.Interaction):
        if not self._check(interaction):
            await interaction.response.send_message("Not authorised.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        ranks = await db.get_server_ranks(interaction.guild_id)
        if not ranks:
            await interaction.response.send_message(
                "No custom ranks set — using global defaults. Add ranks with `/adminranks add`.", ephemeral=True
            )
            return
        lines = [f"**≥{r['min_elo']} ELO** → {r['emoji']} {r['name']}".strip() for r in ranks]
        embed = discord.Embed(
            title=f"Custom Ranks — {interaction.guild.name}",
            description="\n".join(lines),
            color=0x7c3aed,
        )
        embed.set_footer(text="Server premium feature  •  Use /adminranks add or /adminranks remove to manage")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_group = AdminGroup(bot)
        self.admin_rank_group = AdminRankGroup(bot)
