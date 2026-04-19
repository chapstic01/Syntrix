import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

INITIAL_ELO = 1000
ELO_K_FACTOR = 32
MATCH_ELO_RANGE_START = 200
QUEUE_EXPAND_INTERVAL = 60   # seconds before widening search
QUEUE_EXPAND_AMOUNT = 100    # ELO added to range per interval
READY_CHECK_TIMEOUT = 30     # seconds to accept a match
REPORT_TIMEOUT = 300         # seconds to report match result
