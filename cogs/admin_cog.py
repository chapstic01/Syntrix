import discord
from discord import app_commands
from discord.ext import commands
from config import ADMIN_USER_ID
import database as db
from matchmaking import calculate_elo


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


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_group = AdminGroup(bot)
