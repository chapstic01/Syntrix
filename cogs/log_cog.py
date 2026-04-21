import datetime
import discord
from discord.ext import commands
from config import ADMIN_USER_ID


def _fmt_options(options: list) -> str:
    if not options:
        return ""
    parts = []
    for opt in options:
        name = opt.get("name", "?")
        value = opt.get("value", "")
        if isinstance(value, str) and len(str(value)) > 40:
            value = str(value)[:40] + "…"
        parts.append(f"`{name}:{value}`")
    return " " + " ".join(parts)


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

    # ── Slash commands & interactions ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        ts = int(interaction.created_at.timestamp())
        guild = f"{interaction.guild.name} (`{interaction.guild_id}`)" if interaction.guild else "DM"
        user = f"{interaction.user} (`{interaction.user.id}`)"

        if interaction.type == discord.InteractionType.application_command:
            cmd = interaction.command
            name = f"/{cmd.qualified_name}" if cmd else "/unknown"
            opts = _fmt_options(interaction.data.get("options", []))
            await self._log(
                f"🔧 **Command** {name}{opts}\n"
                f"👤 {user}\n"
                f"🏠 {guild}\n"
                f"⏰ <t:{ts}:f>"
            )

        elif interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id", "unknown")
            await self._log(
                f"🖱️ **Button/Select** `{custom_id}`\n"
                f"👤 {user}\n"
                f"🏠 {guild}\n"
                f"⏰ <t:{ts}:f>"
            )

        elif interaction.type == discord.InteractionType.modal_submit:
            custom_id = interaction.data.get("custom_id", "unknown")
            await self._log(
                f"📝 **Modal submit** `{custom_id}`\n"
                f"👤 {user}\n"
                f"🏠 {guild}\n"
                f"⏰ <t:{ts}:f>"
            )

    # ── Bot-sent messages ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.bot.user or message.author.id != self.bot.user.id:
            return

        # Avoid infinite loop: never log DMs sent to the owner
        if isinstance(message.channel, discord.DMChannel):
            try:
                if message.channel.recipient and message.channel.recipient.id == ADMIN_USER_ID:
                    return
            except Exception:
                pass

        ts = int(message.created_at.timestamp())

        if isinstance(message.channel, discord.DMChannel):
            try:
                dest = f"DM → {message.channel.recipient}"
            except Exception:
                dest = "DM → unknown"
        elif hasattr(message.channel, "guild") and message.channel.guild:
            dest = f"{message.channel.guild.name} #{message.channel.name}"
        else:
            dest = str(message.channel)

        embed_note = (
            f" +{len(message.embeds)} embed{'s' if len(message.embeds) != 1 else ''}"
            if message.embeds else ""
        )
        content = message.content
        preview = f"\n> {content[:120]}{'…' if len(content) > 120 else ''}" if content else ""

        await self._log(
            f"📨 **Bot message**\n"
            f"📍 {dest}{embed_note}\n"
            f"⏰ <t:{ts}:f>"
            f"{preview}"
        )

    # ── Server join / leave ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        owner_id = guild.owner_id
        await self._log(
            f"✅ **Bot added to server**\n"
            f"🏠 {guild.name} (`{guild.id}`)\n"
            f"👥 {guild.member_count} members\n"
            f"👑 Owner ID: `{owner_id}`\n"
            f"⏰ <t:{ts}:f>"
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        await self._log(
            f"❌ **Bot removed from server**\n"
            f"🏠 {guild.name} (`{guild.id}`)\n"
            f"⏰ <t:{ts}:f>"
        )
