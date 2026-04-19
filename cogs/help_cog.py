import discord
from discord import app_commands
from discord.ext import commands
from config import ADMIN_USER_ID


HELP_PAGES = {
    "general": {
        "title": "Matchmaking Bot — General Help",
        "fields": [
            ("/join", "Enter the global matchmaking queue"),
            ("/leave", "Leave the queue"),
            ("/queue", "See who is in the queue"),
            ("/match", "View your active match"),
            ("/cancel", "Cancel your current match"),
            ("/profile [user]", "View your or someone's stats & ELO"),
            ("/leaderboard", "Top 10 players by ELO"),
            ("/help", "Show this help menu"),
        ],
        "color": discord.Color.blurple(),
    },
    "admin": {
        "title": "Matchmaking Bot — Admin Commands",
        "fields": [
            ("/admin setelo <user> <elo>", "Set a player's ELO"),
            ("/admin resetstats <user>", "Reset a player's stats"),
            ("/admin ban <user> [reason]", "Ban player from this server's matchmaking"),
            ("/admin unban <user>", "Unban a player"),
            ("/admin forcewinner <match_id> <user>", "Force a match result"),
            ("/admin removequeue <user>", "Remove a player from the queue"),
            ("/admin setup [queue_channel] [results_channel]", "Configure this server"),
        ],
        "color": discord.Color.red(),
    },
}


class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General Commands", value="general", emoji="📋"),
            discord.SelectOption(label="Admin Commands", value="admin", emoji="🔧"),
        ]
        super().__init__(placeholder="Choose a help category…", options=options)

    async def callback(self, interaction: discord.Interaction):
        page = HELP_PAGES[self.values[0]]
        embed = discord.Embed(title=page["title"], color=page["color"])
        for name, value in page["fields"]:
            embed.add_field(name=name, value=value, inline=False)
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpSelect())


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Get help with the matchmaking bot")
    async def help_cmd(self, interaction: discord.Interaction):
        page = HELP_PAGES["general"]
        embed = discord.Embed(title=page["title"], color=page["color"])
        for name, value in page["fields"]:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="Use the dropdown to see admin commands.")
        await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        content = message.content.lower().strip()
        triggers = ("help", "!help", "how do i", "how to use", "commands")
        if any(t in content for t in triggers) and self.bot.user.mentioned_in(message):
            page = HELP_PAGES["general"]
            embed = discord.Embed(title=page["title"], color=page["color"])
            for name, value in page["fields"]:
                embed.add_field(name=name, value=value, inline=False)
            await message.reply(embed=embed, view=HelpView())
