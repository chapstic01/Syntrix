import asyncio
import discord
from discord.ext import commands
from config import DISCORD_TOKEN
import database as db
import matchmaking
from cogs.queue_cog import QueueCog
from cogs.match_cog import MatchCog
from cogs.profile_cog import ProfileCog
from cogs.admin_cog import AdminCog
from cogs.help_cog import HelpCog
from cogs.premium_cog import PremiumCog
from cogs.season_cog import SeasonCog
from cogs.history_cog import HistoryCog


class MatchmakingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init_db()

        queue_cog = QueueCog(self)
        match_cog = MatchCog(self)
        profile_cog = ProfileCog(self)
        admin_cog = AdminCog(self)
        help_cog = HelpCog(self)
        premium_cog = PremiumCog(self)
        season_cog = SeasonCog(self)
        history_cog = HistoryCog(self)

        await self.add_cog(queue_cog)
        await self.add_cog(match_cog)
        await self.add_cog(profile_cog)
        await self.add_cog(admin_cog)
        await self.add_cog(help_cog)
        await self.add_cog(premium_cog)
        await self.add_cog(season_cog)
        await self.add_cog(history_cog)

        self.tree.add_command(admin_cog.admin_group)
        self.tree.add_command(season_cog.season_group)

        await self.tree.sync()
        asyncio.create_task(matchmaking.run_matchmaking_loop(self))
        print("[bot] Commands synced and matchmaking loop started.")

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the queue | /help",
            )
        )
        await db.sync_guilds([(g.id, g.name, g.member_count) for g in self.guilds])
        await self._notify_admin_startup()

    async def _notify_admin_startup(self):
        if not ADMIN_USER_ID:
            return
        try:
            admin = await self.fetch_user(ADMIN_USER_ID)
            guilds = self.guilds
            lines = []
            for g in guilds[:20]:
                invite_url = await self._get_invite(g)
                lines.append(self._invite_line(g.name, g.member_count, invite_url, g.id))
            if len(guilds) > 20:
                lines.append(f"…and {len(guilds) - 20} more")
            embed = discord.Embed(
                title="✅ Syntrix is Online",
                description="\n".join(lines) if lines else "Not in any servers yet.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Total Servers", value=str(len(guilds)), inline=True)
            embed.add_field(name="Bot User", value=str(self.user), inline=True)
            await admin.send(embed=embed)
        except Exception as e:
            print(f"[startup] Could not DM admin: {e}")

    async def _get_invite(self, guild: discord.Guild) -> str | None:
        channel = guild.system_channel
        if channel is None:
            channel = next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite),
                None,
            )
        if channel is None:
            return None
        try:
            invite = await channel.create_invite(max_age=0, max_uses=0, unique=False, reason="Syntrix startup")
            return invite.url
        except Exception:
            return None

    def _invite_line(self, name: str, member_count: int, invite_url: str | None, guild_id: int) -> str:
        link = f"[Join]({invite_url})" if invite_url else f"Invite unavailable · ID: `{guild_id}`"
        return f"• **{name}** — {member_count} members · {link}"

    async def on_guild_join(self, guild: discord.Guild):
        await db.get_server_config(guild.id)
        await db.sync_guilds([(guild.id, guild.name, guild.member_count)])
        print(f"[bot] Joined guild: {guild.name} ({guild.id})")

    async def on_guild_remove(self, guild: discord.Guild):
        await db.remove_guild(guild.id)
        print(f"[bot] Left guild: {guild.name} ({guild.id})")

        system_channel = guild.system_channel
        if system_channel and system_channel.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                title="Syntrix Matchmaking is here!",
                description=(
                    "Thanks for adding me!\n\n"
                    "Use `/join` to enter the **global matchmaking queue**.\n"
                    "Try `/modes` to see available game modes.\n"
                    "Type `/help` for a full command list.\n\n"
                    "⭐ Upgrade to **Premium** with `/premium` for priority matching!"
                ),
                color=discord.Color.purple(),
            )
            await system_channel.send(embed=embed)


bot = MatchmakingBot()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN not set in .env")
    bot.run(DISCORD_TOKEN)
