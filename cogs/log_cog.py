import discord
from discord.ext import commands
from config import ADMIN_USER_ID


class LogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._dm: discord.DMChannel | None = None

    async def _log(self, text: str):
        if not ADMIN_USER_ID:
            return
        try:
            if not self._dm:
                user = await self.bot.fetch_user(ADMIN_USER_ID)
                self._dm = await user.create_dm()
            await self._dm.send(text[:2000])
        except Exception as e:
            print(f"[log] Could not DM owner: {e}")

    # ── Slash commands only (not buttons/selects — too noisy) ─────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.application_command:
            return
        cmd = interaction.command
        name = f"/{cmd.qualified_name}" if cmd else "/unknown"
        opts = _fmt_options(interaction.data.get("options", []))
        guild = f"{interaction.guild.name} (`{interaction.guild_id}`)" if interaction.guild else "DM"
        user = f"{interaction.user} (`{interaction.user.id}`)"
        ts = int(interaction.created_at.timestamp())
        await self._log(
            f"🔧 **{name}**{opts}\n"
            f"👤 {user}\n"
            f"🏠 {guild}\n"
            f"⏰ <t:{ts}:f>"
        )

    # ── Match events ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_match_found(self, match_id: int, p1_id: int, p2_id: int, mode: str):
        try:
            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            await self._log(
                f"🟡 **Match #{match_id} started** · `{mode}`\n"
                f"👥 {p1} vs {p2}"
            )
        except Exception as e:
            await self._log(f"🟡 **Match #{match_id} started** · `{mode}`")

    @commands.Cog.listener()
    async def on_match_state_changed(self, match_id: int = 0, winner_id: int = 0,
                                     p1_id: int = 0, p2_id: int = 0, mode: str = ""):
        if not match_id:
            return
        try:
            winner = await self.bot.fetch_user(winner_id) if winner_id else None
            p1 = await self.bot.fetch_user(p1_id) if p1_id else None
            p2 = await self.bot.fetch_user(p2_id) if p2_id else None
            players = f"{p1} vs {p2}" if p1 and p2 else ""
            result = f"\n🏆 Winner: **{winner}**" if winner else "\n❌ Match cancelled"
            await self._log(
                f"✅ **Match #{match_id} ended** · `{mode}`\n"
                f"👥 {players}{result}"
            )
        except Exception:
            await self._log(f"✅ **Match #{match_id} ended**")

    # ── Server join / leave ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        import datetime
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        await self._log(
            f"✅ **Bot added to server**\n"
            f"🏠 {guild.name} (`{guild.id}`)\n"
            f"👥 {guild.member_count} members\n"
            f"⏰ <t:{ts}:f>"
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        import datetime
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        await self._log(
            f"❌ **Bot removed from server**\n"
            f"🏠 {guild.name} (`{guild.id}`)\n"
            f"⏰ <t:{ts}:f>"
        )


def _fmt_options(options: list) -> str:
    if not options:
        return ""
    parts = []
    for opt in options:
        name = opt.get("name", "?")
        value = str(opt.get("value", ""))
        if len(value) > 40:
            value = value[:40] + "…"
        parts.append(f"`{name}:{value}`")
    return " " + " ".join(parts)
