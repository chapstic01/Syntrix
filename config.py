import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
GUMROAD_PRODUCT_ID = os.getenv("GUMROAD_PRODUCT_ID", "")
BOT_INVITE_URL = os.getenv("BOT_INVITE_URL", "#")
PREMIUM_URL = os.getenv("PREMIUM_URL", "")
PREMIUM_PRICE = os.getenv("PREMIUM_PRICE", "")
SUPPORT_SERVER = os.getenv("SUPPORT_SERVER", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/dashboard/callback")

INITIAL_ELO = 1000
ELO_K_FACTOR = 32
MATCH_ELO_RANGE_START = 200
QUEUE_EXPAND_INTERVAL = 60    # seconds before widening search
QUEUE_EXPAND_AMOUNT = 100     # ELO added to range per interval
READY_CHECK_TIMEOUT = 30      # seconds to accept a match
REPORT_TIMEOUT = 300          # seconds to report match result

PREMIUM_RANGE_MULTIPLIER = 1.5  # premium users match 50% wider range

RANK_TIERS = [
    (1800, "👑 Master"),
    (1600, "💠 Diamond"),
    (1400, "💎 Platinum"),
    (1200, "🥇 Gold"),
    (1000, "🥈 Silver"),
    (800,  "🥉 Bronze"),
    (0,    "🪨 Iron"),
]

DEFAULT_QUEUE_MODES = [
    ("ranked",  "Ranked",  "Competitive ELO-based matchmaking"),
    ("casual",  "Casual",  "Relaxed games, no ELO on the line"),
]


def get_rank(elo: int) -> str:
    for threshold, name in RANK_TIERS:
        if elo >= threshold:
            return name
    return "🪨 Iron"


MAX_GAMES_FREE = 1
MAX_GAMES_PREMIUM = 3

GAMES: dict[str, dict] = {
    "valorant":    {"name": "Valorant",          "maps": ["Abyss","Ascent","Bind","Breeze","Fracture","Haven","Icebox","Lotus","Pearl","Split","Sunset"]},
    "cs2":         {"name": "CS2",               "maps": ["Ancient","Anubis","Dust 2","Inferno","Mirage","Nuke","Overpass","Vertigo"]},
    "rocket_league":{"name": "Rocket League",    "maps": ["Champions Field","DFH Stadium","Farmstead","Mannfield","Neo Tokyo","Salty Shores","Utopia Coliseum","Wasteland"]},
    "call_of_duty":{"name": "Call of Duty",      "maps": ["Shipment","Nuketown","Rust","Hijacked","Firing Range","Summit","Array","Standoff"]},
    "r6_siege":    {"name": "Rainbow Six Siege", "maps": ["Bank","Border","Chalet","Clubhouse","Coastline","Consulate","Kafe","Oregon","Skyscraper","Villa"]},
    "apex_legends":{"name": "Apex Legends",      "maps": ["World's Edge","Kings Canyon","Olympus","Storm Point","Broken Moon"]},
    "fortnite":    {"name": "Fortnite",          "maps": ["Chapter 1 OG","Chapter 2","Chapter 3","Chapter 4","Chapter 5","Chapter 6 Delta Force"]},
    "war_tycoon":  {"name": "War Tycoon",        "maps": ["Desert Dunes","Arctic Outpost","Urban Warfare","Jungle Basin","Mountain Pass","Island Strike"]},
    "minecraft_pvp":{"name": "Minecraft PvP",   "maps": ["Bedwars","Skywars","The Bridge","Sumo","UHC","Crystal PvP"]},
    "custom":      {"name": "Custom / Other",    "maps": []},
}
