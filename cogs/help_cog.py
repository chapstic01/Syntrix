import discord
from discord import app_commands
from discord.ext import commands
from config import BOT_INVITE_URL


ACCENT = discord.Color.from_str("#7c3aed")

HELP_PAGES = {
    "overview": {
        "label": "Overview",
        "emoji": "🏠",
        "title": "Syntrix — Global Matchmaking",
        "description": (
            "**Syntrix** connects players across every server into a single global matchmaking queue. "
            "Match by ELO, climb the ranks, and compete across seasons.\n\n"
            "Use the dropdown below to explore each command category."
        ),
        "fields": [
            ("How it works", (
                "1. `/join` — enter the queue\n"
                "2. Syntrix finds an opponent near your ELO\n"
                "3. Both players confirm via a **Ready Check** DM\n"
                "4. Play your match, then report the result via DM\n"
                "5. ELO updates automatically"
            )),
            ("ELO & Ranking", (
                "You start at **1000 ELO**. Every win/loss adjusts your ELO using the standard chess formula (K=32).\n"
                "🪨 Iron · 🥉 Bronze · 🥈 Silver · 🥇 Gold · 💎 Platinum · 💠 Diamond · 👑 Master"
            )),
            ("Queue Expansion", "If no match is found, your search range widens by ±100 ELO every 60 seconds."),
        ],
        "color": ACCENT,
    },
    "queue": {
        "label": "Queue",
        "emoji": "📋",
        "title": "Syntrix — Queue Commands",
        "description": "Commands for entering, managing, and browsing the global matchmaking queue.",
        "fields": [
            ("`/join [mode]`", "Enter the matchmaking queue. Defaults to **Ranked**. Use the `mode` option to join Casual or any custom mode."),
            ("`/leave`", "Exit the queue at any time before a match is found."),
            ("`/queue [mode]`", "See all players currently waiting in the queue. Filter by mode with the optional argument."),
            ("`/modes`", "List every available queue mode (Ranked, Casual, and any server-specific modes)."),
        ],
        "color": discord.Color.blue(),
    },
    "match": {
        "label": "Match",
        "emoji": "⚔️",
        "title": "Syntrix — Match Commands",
        "description": "Once you're matched, Syntrix sends you a DM with **Ready Check** buttons. You have 30 seconds to accept.",
        "fields": [
            ("`/match`", "View your current active match — shows both players, ELO, and current status."),
            ("`/cancel`", "Cancel your active match. Your opponent will be notified via DM."),
            ("Ready Check", "Click **Accept** in the DM within 30 s. If a player times out, the ready player is re-queued automatically."),
            ("Reporting Results", "After your match, click **I Won** or **I Lost** in the DM. Both reports must agree — conflicts require an admin to use `/admin forcewinner`."),
            ("Casual Mode", "Casual matches skip ELO changes — great for practice or custom games."),
        ],
        "color": discord.Color.orange(),
    },
    "profile": {
        "label": "Profile & Stats",
        "emoji": "📊",
        "title": "Syntrix — Profile & Stats",
        "description": "Track your performance, compare with others, and follow the competitive ladder.",
        "fields": [
            ("`/profile [user]`", "View full stats for yourself or another player — ELO, rank, wins, losses, win rate, and match count. Leave `user` blank for your own profile."),
            ("`/leaderboard`", "The top 10 players by ELO across all servers. 🥇🥈🥉 medals for the podium."),
        ],
        "color": discord.Color.green(),
    },
    "premium": {
        "label": "Premium ⭐",
        "emoji": "⭐",
        "title": "Syntrix Premium",
        "description": "Upgrade your experience with **Syntrix Premium** — purchase a license on Gumroad and activate it in seconds.",
        "fields": [
            ("`/premium`", "Check your current premium status."),
            ("`/premium [license_key]`", "Activate premium with your Gumroad license key."),
            ("Priority Matching ⚡", "Premium users get a **1.5× wider ELO search range**, so you find matches faster."),
            ("Visual Flair ✨", "Your queue entry shows a ⭐ star, and your join confirmation uses a special purple embed."),
            ("How to get it", "Purchase at the Syntrix Gumroad page, copy the license key from your receipt email, and paste it into `/premium`."),
        ],
        "color": discord.Color.from_str("#a855f7"),
    },
    "seasons": {
        "label": "Seasons",
        "emoji": "🏆",
        "title": "Syntrix — Seasons",
        "description": "Seasons let you compete in time-limited ranked cycles. At the end of each season, stats are archived and ELO soft-resets.",
        "fields": [
            ("`/season info`", "View the current active season — name, start date, and your standing."),
            ("`/season history`", "Browse all past seasons and their final standings."),
            ("`/season list`", "List every season (active and ended) with IDs and dates."),
            ("ELO Soft Reset", "When a season ends, your ELO moves halfway back toward 1000. For example: 1400 ELO → 1200 next season. Past seasons are permanently archived."),
        ],
        "color": discord.Color.gold(),
    },
    "admin": {
        "label": "Admin",
        "emoji": "🔧",
        "title": "Syntrix — Admin Commands",
        "description": "Admin commands are restricted to the bot owner (set via `ADMIN_USER_ID` in the bot's environment). Server admins can use `/admin setup` to configure their server.",
        "fields": [
            ("`/admin setup [queue_channel] [results_channel]`", "Point the bot at the channels it should use for queue announcements and match results in this server."),
            ("`/admin setelo <user> <elo>`", "Override a player's ELO to any value (min 0)."),
            ("`/admin resetstats <user>`", "Reset a player's ELO to 1000 and clear their win/loss record."),
            ("`/admin ban <user> [reason]`", "Prevent a player from using matchmaking in this server. Server-specific — does not affect other servers."),
            ("`/admin unban <user>`", "Lift a server ban."),
            ("`/admin forcewinner <match_id> <user>`", "Force a match result and apply ELO when players submit conflicting reports."),
            ("`/admin removequeue <user>`", "Remove a specific player from the queue immediately."),
            ("`/admin grantpremium <user>`", "Manually grant premium to a player without a Gumroad key."),
            ("`/admin revokepremium <user>`", "Remove premium from a player."),
            ("`/admin addmode <id> <name> [desc]`", "Create a new custom queue mode."),
            ("`/admin removemode <id>`", "Delete a custom queue mode (built-in modes cannot be removed)."),
            ("`/season start <name>` · `/season end`", "Start or end a ranked season (owner only)."),
        ],
        "color": discord.Color.red(),
    },
}


class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"])
            for k, v in HELP_PAGES.items()
        ]
        super().__init__(placeholder="Choose a category…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        page = HELP_PAGES[self.values[0]]
        embed = _build_embed(page)
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(HelpSelect())


def _build_embed(page: dict) -> discord.Embed:
    embed = discord.Embed(
        title=page["title"],
        description=page["description"],
        color=page["color"],
    )
    for name, value in page["fields"]:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="Syntrix Global Matchmaking  •  /join to get started")
    return embed


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Learn how Syntrix works and browse all commands")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = _build_embed(HELP_PAGES["overview"])
        await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)

    @app_commands.command(name="welcome", description="Post a Syntrix introduction embed in this channel")
    async def welcome_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Syntrix — Global Matchmaking",
            description=(
                "Syntrix connects players across every Discord server into a single ranked queue. "
                "Get matched by ELO, confirm via DM, report your result, and climb the leaderboard."
            ),
            color=ACCENT,
        )
        embed.add_field(
            name="Queue Commands",
            value=(
                "`/join` — enter the queue\n"
                "`/leave` — exit the queue\n"
                "`/queue` — see who is waiting\n"
                "`/modes` — list game modes"
            ),
            inline=True,
        )
        embed.add_field(
            name="Stats & Rankings",
            value=(
                "`/profile` — your ELO & record\n"
                "`/leaderboard` — top 10 players\n"
                "`/history` — recent match results\n"
                "`/stats` — server match activity"
            ),
            inline=True,
        )
        embed.add_field(
            name="How It Works",
            value=(
                "1. `/join` to enter the global queue\n"
                "2. Syntrix finds an opponent near your ELO\n"
                "3. Accept the **Ready Check** DM within 30 s\n"
                "4. Play, then report your result via DM\n"
                "5. ELO updates automatically"
            ),
            inline=False,
        )
        embed.add_field(
            name="Premium",
            value="Priority matching with a 1.5× wider ELO range. Activate with `/premium <license_key>`.",
            inline=False,
        )
        if BOT_INVITE_URL and BOT_INVITE_URL != "#":
            embed.add_field(name="Invite", value=f"[Add Syntrix to your server]({BOT_INVITE_URL})", inline=False)
        embed.set_footer(text="Use /help for a full command reference")
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        content = message.content.lower().strip()
        triggers = ("help", "!help", "how do i", "how to use", "commands")
        if any(t in content for t in triggers) and self.bot.user.mentioned_in(message):
            embed = _build_embed(HELP_PAGES["overview"])
            await message.reply(embed=embed, view=HelpView())
