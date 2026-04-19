import asyncio
import time
from config import (
    ELO_K_FACTOR,
    MATCH_ELO_RANGE_START,
    QUEUE_EXPAND_INTERVAL,
    QUEUE_EXPAND_AMOUNT,
    READY_CHECK_TIMEOUT,
)
import database as db


def calculate_elo(winner_elo: int, loser_elo: int) -> tuple[int, int]:
    expected_w = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_l = 1 - expected_w
    new_winner = round(winner_elo + ELO_K_FACTOR * (1 - expected_w))
    new_loser = round(loser_elo + ELO_K_FACTOR * (0 - expected_l))
    return max(new_winner, 100), max(new_loser, 100)


def elo_range_for_wait(joined_at_ts: float) -> int:
    waited = time.time() - joined_at_ts
    expansions = int(waited // QUEUE_EXPAND_INTERVAL)
    return MATCH_ELO_RANGE_START + expansions * QUEUE_EXPAND_AMOUNT


async def find_match(player_id: int, player_elo: int, joined_at_ts: float) -> dict | None:
    queue = await db.get_all_queue()
    elo_range = elo_range_for_wait(joined_at_ts)

    for entry in queue:
        if entry["discord_id"] == player_id:
            continue
        if abs(entry["elo_at_join"] - player_elo) <= elo_range:
            return entry
    return None


async def run_matchmaking_loop(bot, interval: float = 5.0):
    await asyncio.sleep(5)
    while True:
        try:
            await _tick(bot)
        except Exception as e:
            print(f"[matchmaking] error: {e}")
        await asyncio.sleep(interval)


async def _tick(bot):
    import time as _time
    from datetime import datetime

    queue = await db.get_all_queue()
    matched = set()

    for entry in queue:
        pid = entry["discord_id"]
        if pid in matched:
            continue

        joined_ts = datetime.fromisoformat(entry["joined_at"]).timestamp()
        elo_range = elo_range_for_wait(joined_ts)

        for other in queue:
            oid = other["discord_id"]
            if oid == pid or oid in matched:
                continue
            if abs(other["elo_at_join"] - entry["elo_at_join"]) <= elo_range:
                matched.add(pid)
                matched.add(oid)

                await db.dequeue(pid)
                await db.dequeue(oid)

                match_id = await db.create_match(pid, oid, entry["server_id"])
                bot.dispatch("match_found", match_id, pid, oid)
                break
