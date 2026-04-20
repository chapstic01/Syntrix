import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
GUMROAD_PRODUCT_ID = os.getenv("GUMROAD_PRODUCT_ID", "")
BOT_INVITE_URL = os.getenv("BOT_INVITE_URL", "#")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")

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
