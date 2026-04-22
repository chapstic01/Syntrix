import datetime
import discord
from discord.ext import commands
from config import ADMIN_USER_ID


class LogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._dm: discord.DMChannel | None = None

    async def _log(self, embed: discord.Embed):
        if not ADMIN_USER_ID:
            return
        try:
            if not self._dm:
                user = await self.bot.fetch_user(ADMIN_USER_ID)
                self._dm = await user.create_dm()
            await self._dm.send(embed=embed)
        except Exception as e:
            print(f"[log] Could not DM owner: {e}")

    async def _get_invite(self, guild: discord.Guild) -> str | None:
        try:
            return await self.bot._get_invite(guild)
        except Exception:
            return None

    # ── Bot messages sent in guild channels ───────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id != self.bot.user.id:
            return
        if not message.guild:
            return

        invite = await self._get_invite(message.guild)
        preview = message.clean_content[:300] if message.clean_content else None
        embed_count = len(message.embeds)

        embed = discord.Embed(
            title=f"Message Sent in #{message.channel.name}",
            color=0x5865f2,
            timestamp=message.created_at,
        )
        embed.add_field(name="Server", value=f"**{message.guild.name}**", inline=True)
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
        embed.add_field(name="Jump", value=f"[View Message]({message.jump_url})", inline=True)

        if preview:
            embed.add_field(name="Content", value=preview, inline=False)
        elif embed_count:
            embed.add_field(name="Content", value=f"*{embed_count} embed{'s' if embed_count != 1 else ''}*", inline=False)

        if invite:
            embed.add_field(name="Permanent Invite", value=invite, inline=False)

        embed.set_footer(text="Syntrix · Message Log")
        await self._log(embed)

    # ── Slash commands ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.application_command:
            return
        cmd = interaction.command
        name = f"/{cmd.qualified_name}" if cmd else "/unknown"
        opts = _fmt_options(interaction.data.get("options", []))
        ts = int(interaction.created_at.timestamp())

        embed = discord.Embed(
            title=f"{name}{opts}",
            color=0x7c3aed,
            timestamp=interaction.created_at,
        )
        embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url,
        )

        if interaction.guild:
            invite = await self._get_invite(interaction.guild)
            server_val = f"**{interaction.guild.name}**"
            if invite:
                server_val += f"\n[Join Server]({invite})"
            else:
                server_val += f"\n`{interaction.guild_id}`"
            embed.add_field(name="Server", value=server_val, inline=True)
        else:
            embed.add_field(name="Server", value="DM", inline=True)

        embed.add_field(name="User ID", value=f"`{interaction.user.id}`", inline=True)
        embed.add_field(name="When", value=f"<t:{ts}:f>", inline=True)
        embed.set_footer(text="Syntrix · Command Log")
        await self._log(embed)

    # ── Match events ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_match_found(self, match_id: int, p1_id: int, p2_id: int, mode: str):
        try:
            p1 = await self.bot.fetch_user(p1_id)
            p2 = await self.bot.fetch_user(p2_id)
            p1_str = f"{p1} (`{p1_id}`)"
            p2_str = f"{p2} (`{p2_id}`)"
        except Exception:
            p1_str, p2_str = f"`{p1_id}`", f"`{p2_id}`"

        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(
            title=f"Match #{match_id} Started",
            color=0xf59e0b,
            timestamp=now,
        )
        embed.add_field(name="Mode", value=f"`{mode}`", inline=True)
        embed.add_field(name="Match ID", value=f"`#{match_id}`", inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(name="Player 1", value=p1_str, inline=True)
        embed.add_field(name="Player 2", value=p2_str, inline=True)
        embed.set_footer(text="Syntrix · Match Log")
        await self._log(embed)

    @commands.Cog.listener()
    async def on_match_state_changed(self, match_id: int = 0, winner_id: int = 0,
                                     p1_id: int = 0, p2_id: int = 0, mode: str = ""):
        if not match_id:
            return

        try:
            winner = await self.bot.fetch_user(winner_id) if winner_id else None
            p1 = await self.bot.fetch_user(p1_id) if p1_id else None
            p2 = await self.bot.fetch_user(p2_id) if p2_id else None
        except Exception:
            winner = p1 = p2 = None

        cancelled = not winner_id
        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(
            title=f"Match #{match_id} {'Cancelled' if cancelled else 'Completed'}",
            color=0xef4444 if cancelled else 0x22c55e,
            timestamp=now,
        )
        embed.add_field(name="Mode", value=f"`{mode}`", inline=True)
        embed.add_field(name="Match ID", value=f"`#{match_id}`", inline=True)
        embed.add_field(name="​", value="​", inline=True)

        if p1 and p2:
            embed.add_field(name="Player 1", value=str(p1), inline=True)
            embed.add_field(name="Player 2", value=str(p2), inline=True)
            embed.add_field(name="​", value="​", inline=True)

        if winner:
            embed.add_field(name="Winner", value=f"🏆 **{winner}**", inline=False)
        elif cancelled:
            embed.add_field(name="Result", value="Match was cancelled", inline=False)

        embed.set_footer(text="Syntrix · Match Log")
        await self._log(embed)

    # ── Server join / leave ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        invite = await self._get_invite(guild)
        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(
            title="Bot Added to Server",
            color=0x22c55e,
            timestamp=now,
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Server", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        if invite:
            embed.add_field(name="Permanent Invite", value=invite, inline=False)
        embed.add_field(
            name="Total Servers",
            value=str(len(self.bot.guilds)),
            inline=True,
        )
        embed.set_footer(text="Syntrix · Server Log")
        await self._log(embed)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(
            title="Bot Removed from Server",
            color=0xef4444,
            timestamp=now,
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Server", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(
            name="Total Servers",
            value=str(len(self.bot.guilds)),
            inline=True,
        )
        embed.set_footer(text="Syntrix · Server Log")
        await self._log(embed)


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
    return "  " + "  ".join(parts)
