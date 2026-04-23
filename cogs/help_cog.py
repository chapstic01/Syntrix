import discord
from discord import app_commands
from discord.ext import commands
from config import BOT_INVITE_URL, PREMIUM_URL, PREMIUM_PRICE, SUPPORT_SERVER, DASHBOARD_URL


ACCENT = discord.Color.from_str("#f59e0b")

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
            ("Utility", "`/welcome` — post a public intro embed\n`/stats` — server match activity\n`/history` — your recent match results\n`/help` — this menu"),
            ("Support", (f"[Join the support server]({SUPPORT_SERVER})" if SUPPORT_SERVER else "Contact the bot owner for help.")),
        ],
        "color": ACCENT,
    },
    "queue": {
        "label": "Queue",
        "emoji": "📋",
        "title": "Syntrix — Queue Commands",
        "description": "Commands for entering, managing, and browsing the global matchmaking queue.",
        "fields": [
            ("`/join [mode]`", "Enter the matchmaking queue. Defaults to **Ranked**. Use the `mode` option to join Casual or a custom mode."),
            ("`/leave`", "Exit the queue at any time before a match is found."),
            ("`/queue [mode]`", "See everyone currently waiting. Includes a tip showing the exact `/join` command for that mode."),
            ("`/modes`", "List every available queue mode with descriptions."),
            ("`/recruit [mode]`", "Post a public embed to recruit players into a queue — shows current count and the join command. Great for drumming up a match."),
        ],
        "color": discord.Color.blue(),
    },
    "match": {
        "label": "Match",
        "emoji": "⚔️",
        "title": "Syntrix — Match Commands",
        "description": "Once matched, Syntrix sends both players a **Ready Check** DM. You have 30 seconds to accept — if either player times out, the other is re-queued.",
        "fields": [
            ("`/match`", "View your current active match — players, ELO, mode, and status."),
            ("`/cancel`", "Cancel your active match. Your opponent is notified via DM."),
            ("Map Voting 🗺️", "If the server has a game assigned to your queue, both players receive a **map vote** DM after the ready check. Same pick = that map; split = random selection."),
            ("Auto Channels 🔊", "A private **voice + text channel** is created for your match automatically and deleted when the match ends. Requires a category to be configured by a server admin."),
            ("Reporting Results", (
                "After your match, report via DM button.\n"
                "• **Standard mode:** click **I Won** or **I Lost**\n"
                "• **Score mode:** enter the final score (e.g. 3–1) — higher score wins\n"
                "• If **evidence is required**, include a screenshot URL\n"
                "Conflicting reports cancel the match — use `/admin forcewinner` to resolve."
            )),
            ("Casual Mode", "Casual matches never change ELO — good for practice or custom games."),
        ],
        "color": discord.Color.orange(),
    },
    "profile": {
        "label": "Profile & Stats",
        "emoji": "📊",
        "title": "Syntrix — Profile & Stats",
        "description": "Track your performance, compare with others, and follow the competitive ladder.",
        "fields": [
            ("`/profile [user]`", "View full stats for yourself or another player — ELO, rank, wins, losses, win rate, and total matches. Leave `user` blank for your own profile."),
            ("`/leaderboard`", "The top 10 players by ELO across all servers. 🥇🥈🥉 medals for the podium."),
            ("`/history [user] [limit]`", "View recent match results — shows outcome (win/loss/cancelled), opponent name, date, and mode for each match. Defaults to your last 10 matches; max 15."),
            ("`/stats`", "Server-scoped statistics: total matches played here, completed, cancelled, active now, and current queue size."),
        ],
        "color": discord.Color.green(),
    },
    "premium": {
        "label": "Premium ⭐",
        "emoji": "⭐",
        "title": "Syntrix Premium",
        "description": (
            "Upgrade your experience with **Syntrix Premium**"
            + (f" — only **${PREMIUM_PRICE}**" if PREMIUM_PRICE else "")
            + ". Purchase a license on Gumroad and activate it in seconds."
        ),
        "fields": [
            ("`/premium`", "Check your current premium status."),
            ("`/premium [license_key]`", "Activate premium with your Gumroad license key."),
            ("Priority Matching ⚡", "Premium users get a **1.5× wider ELO search range**, so you find matches faster."),
            ("Visual Flair ✨", "Your queue entry shows a ⭐ star, and your join confirmation uses a special purple embed."),
            ("More Game Queues 🎮", "Premium servers can assign **up to 3 different games** with separate queues."),
            ("How to get it", (
                (f"[Purchase on Gumroad]({PREMIUM_URL})" if PREMIUM_URL else "Purchase on Gumroad")
                + ", copy the license key from your receipt email, and paste it into `/premium`."
            )),
        ],
        "color": discord.Color.from_str("#fbbf24"),
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
    "setup": {
        "label": "Server Setup",
        "emoji": "⚙️",
        "title": "Syntrix — Server Setup",
        "description": (
            "Anyone with **Manage Server** permission can configure Syntrix for their server — "
            "via slash commands or the **dashboard**."
            + (f"\n\n🖥️ **Dashboard:** {DASHBOARD_URL}" if DASHBOARD_URL else "")
        ),
        "fields": [
            ("`/admin setup`", "Set the queue announcements channel and results channel for this server."),
            ("`/admin setgame <mode> <game>`", "Assign a game to a queue mode. Players vote on the map before each match. Free: 1 game — Server Premium: up to 3."),
            ("`/admin removegame <mode>`", "Remove a game assignment from a queue mode."),
            ("`/admin listgames`", "Show all game assignments for this server."),
            ("`/admin scoremode <on/off>`", "Toggle score-based match reporting. When on, players enter the final score instead of Win/Loss buttons."),
            ("`/admin requireevidence <on/off>`", "Require a screenshot URL when players submit results."),
            ("`/admin setrounds <number>`", "Set the expected rounds per match (shown to players)."),
            ("`/admin rematchcooldown <minutes>`", "Prevent the same two players matching again for N minutes. `0` = disabled."),
            ("`/admin anonymous <on/off>`", "Hide both players' names in the Match Found DM until both click Ready."),
            ("`/admin matchcategory <category_id>`", "Discord category where auto voice + text channels are created per match."),
            ("`/admin setupdate <channel>`", "Channel where `/update` owner broadcasts are posted in this server."),
            ("`/admin serverpremium <on/off>`", "Enable server premium — unlocks up to 3 game queues."),
            ("`/admin serversettings`", "View all current settings for this server in one embed."),
        ],
        "color": discord.Color.from_str("#f59e0b"),
    },
    "admin": {
        "label": "Admin — Players",
        "emoji": "🔧",
        "title": "Syntrix — Admin Player Commands",
        "description": "Player management commands. Bot owner commands (`/update`, `/season start/end`) are marked — all others work for any server admin.",
        "fields": [
            ("`/admin setelo <user> <elo>`", "Override a player's ELO to any value (min 0)."),
            ("`/admin resetstats <user>`", "Reset a player's ELO to 1000 and clear their win/loss record."),
            ("`/admin ban <user> [reason]`", "Ban a player from matchmaking in this server only. Does not affect other servers."),
            ("`/admin unban <user>`", "Lift a server ban."),
            ("`/admin forcewinner <match_id> <user>`", "Force a match result and apply ELO — use when players submit conflicting reports."),
            ("`/admin removequeue <user>`", "Remove a specific player from the queue immediately."),
            ("`/admin grantpremium <user>`", "Manually grant premium to a player. *(Owner only)*"),
            ("`/admin revokepremium <user>`", "Remove premium from a player. *(Owner only)*"),
            ("`/admin addmode <id> <name>`", "Create a custom queue mode. *(Owner only)*"),
            ("`/admin removemode <id>`", "Disable a custom queue mode. *(Owner only)*"),
            ("`/update`", "Open a modal to broadcast an update embed to all servers. *(Owner only)*"),
            ("`/season start <name>` · `/season end`", "Start or end a ranked season. *(Owner only)*"),
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
    footer = "Syntrix Global Matchmaking  •  /join to get started"
    if SUPPORT_SERVER:
        footer += f"  •  Support: {SUPPORT_SERVER}"
    embed.set_footer(text=footer)
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
        premium_val = "Priority matching with a 1.5× wider ELO range."
        if PREMIUM_URL:
            premium_val += f" [Get Premium]({PREMIUM_URL})"
        if PREMIUM_PRICE:
            premium_val += f" · **${PREMIUM_PRICE}**"
        embed.add_field(name="Premium ⭐", value=premium_val, inline=False)
        links = []
        if BOT_INVITE_URL and BOT_INVITE_URL != "#":
            links.append(f"[Add to your server]({BOT_INVITE_URL})")
        if SUPPORT_SERVER:
            links.append(f"[Support server]({SUPPORT_SERVER})")
        if links:
            embed.add_field(name="Links", value="  •  ".join(links), inline=False)
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
