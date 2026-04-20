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

        await self.add_cog(queue_cog)
        await self.add_cog(match_cog)
        await self.add_cog(profile_cog)
        await self.add_cog(admin_cog)
        await self.add_cog(help_cog)
        await self.add_cog(premium_cog)
        await self.add_cog(season_cog)

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

    async def on_guild_join(self, guild: discord.Guild):
        await db.get_server_config(guild.id)
        print(f"[bot] Joined guild: {guild.name} ({guild.id})")

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
