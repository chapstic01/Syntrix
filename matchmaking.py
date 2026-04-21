import asyncio
import time
from datetime import datetime
from config import (
    ELO_K_FACTOR,
    MATCH_ELO_RANGE_START,
    QUEUE_EXPAND_INTERVAL,
    QUEUE_EXPAND_AMOUNT,
    PREMIUM_RANGE_MULTIPLIER,
)
import database as db


def calculate_elo(winner_elo: int, loser_elo: int) -> tuple[int, int]:
    expected_w = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_l = 1 - expected_w
    new_winner = round(winner_elo + ELO_K_FACTOR * (1 - expected_w))
    new_loser = round(loser_elo + ELO_K_FACTOR * (0 - expected_l))
    return max(new_winner, 100), max(new_loser, 100)


def elo_range_for_wait(joined_at_ts: float, premium: bool = False) -> int:
    waited = time.time() - joined_at_ts
    expansions = int(waited // QUEUE_EXPAND_INTERVAL)
    base = MATCH_ELO_RANGE_START + expansions * QUEUE_EXPAND_AMOUNT
    return round(base * PREMIUM_RANGE_MULTIPLIER) if premium else base


async def run_matchmaking_loop(bot, interval: float = 5.0):
    await asyncio.sleep(5)
    while True:
        try:
            await _tick(bot)
        except Exception as e:
            print(f"[matchmaking] error: {e}")
        await asyncio.sleep(interval)


async def _tick(bot):
    _cooldown_cache.clear()
    modes = await db.get_queue_modes()
    for mode in modes:
        await _tick_mode(bot, mode["mode_id"])


_cooldown_cache: dict[int, int] = {}


async def _get_cooldown(server_id: int) -> int:
    if server_id not in _cooldown_cache:
        cfg = await db.get_server_config(server_id) if server_id else {}
        _cooldown_cache[server_id] = int(cfg.get("rematch_cooldown") or 0)
    return _cooldown_cache[server_id]


async def _tick_mode(bot, mode: str):
    queue = await db.get_all_queue(mode=mode)
    matched: set[int] = set()

    premium_cache: dict[int, bool] = {}

    async def is_premium(pid: int) -> bool:
        if pid not in premium_cache:
            premium_cache[pid] = await db.is_premium(pid)
        return premium_cache[pid]

    for entry in queue:
        pid = entry["discord_id"]
        if pid in matched:
            continue

        joined_ts = datetime.fromisoformat(entry["joined_at"]).timestamp()
        p_premium = await is_premium(pid)
        elo_range = elo_range_for_wait(joined_ts, premium=p_premium)

        for other in queue:
            oid = other["discord_id"]
            if oid == pid or oid in matched:
                continue

            o_joined_ts = datetime.fromisoformat(other["joined_at"]).timestamp()
            o_premium = await is_premium(oid)
            o_range = elo_range_for_wait(o_joined_ts, premium=o_premium)

            effective_range = max(elo_range, o_range)
            if abs(other["elo_at_join"] - entry["elo_at_join"]) <= effective_range:
                # Check rematch cooldown (uses the server of the first player's queue entry)
                cooldown = await _get_cooldown(entry["server_id"])
                if not await db.check_rematch_cooldown(pid, oid, cooldown):
                    continue
                matched.add(pid)
                matched.add(oid)
                await db.dequeue(pid)
                await db.dequeue(oid)
                match_id = await db.create_match(pid, oid, entry["server_id"], mode=mode)
                bot.dispatch("match_found", match_id, pid, oid, mode)
                break
