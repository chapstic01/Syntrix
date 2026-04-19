import aiosqlite
import json
from config import INITIAL_ELO

DB_PATH = "matchmaking.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id   INTEGER PRIMARY KEY,
                username     TEXT NOT NULL,
                elo          INTEGER DEFAULT 1000,
                wins         INTEGER DEFAULT 0,
                losses       INTEGER DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS server_players (
                discord_id   INTEGER,
                server_id    INTEGER,
                banned       INTEGER DEFAULT 0,
                notes        TEXT DEFAULT '',
                PRIMARY KEY (discord_id, server_id)
            );

            CREATE TABLE IF NOT EXISTS queue (
                discord_id   INTEGER PRIMARY KEY,
                server_id    INTEGER NOT NULL,
                elo_at_join  INTEGER NOT NULL,
                joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS matches (
                match_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                player1_id   INTEGER NOT NULL,
                player2_id   INTEGER NOT NULL,
                origin_server INTEGER,
                status       TEXT DEFAULT 'pending',
                winner_id    INTEGER,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ready_checks (
                match_id     INTEGER PRIMARY KEY,
                player1_id   INTEGER NOT NULL,
                player2_id   INTEGER NOT NULL,
                p1_ready     INTEGER DEFAULT 0,
                p2_ready     INTEGER DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS server_config (
                server_id        INTEGER PRIMARY KEY,
                queue_channel_id INTEGER,
                results_channel_id INTEGER,
                settings         TEXT DEFAULT '{}'
            );
        """)
        await db.commit()


async def get_or_create_player(discord_id: int, username: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO players (discord_id, username, elo) VALUES (?, ?, ?)",
            (discord_id, username, INITIAL_ELO),
        )
        await db.execute(
            "UPDATE players SET username = ? WHERE discord_id = ?",
            (username, discord_id),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row)


async def get_player(discord_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_player_elo(discord_id: int, new_elo: int, won: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute(
                "UPDATE players SET elo = ?, wins = wins + 1 WHERE discord_id = ?",
                (new_elo, discord_id),
            )
        else:
            await db.execute(
                "UPDATE players SET elo = ?, losses = losses + 1 WHERE discord_id = ?",
                (new_elo, discord_id),
            )
        await db.commit()


async def enqueue(discord_id: int, server_id: int, elo: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO queue (discord_id, server_id, elo_at_join) VALUES (?, ?, ?)",
            (discord_id, server_id, elo),
        )
        await db.commit()


async def dequeue(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM queue WHERE discord_id = ?", (discord_id,))
        await db.commit()


async def get_queue_entry(discord_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM queue WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_queue() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT q.*, p.username FROM queue q JOIN players p ON q.discord_id = p.discord_id ORDER BY q.joined_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def create_match(player1_id: int, player2_id: int, server_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO matches (player1_id, player2_id, origin_server) VALUES (?, ?, ?)",
            (player1_id, player2_id, server_id),
        )
        await db.execute(
            "INSERT INTO ready_checks (match_id, player1_id, player2_id) VALUES (?, ?, ?)",
            (cur.lastrowid, player1_id, player2_id),
        )
        await db.commit()
        return cur.lastrowid


async def get_match(match_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM matches WHERE match_id = ?", (match_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_match_for_player(discord_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM matches
               WHERE (player1_id = ? OR player2_id = ?)
               AND status IN ('pending', 'active')
               ORDER BY created_at DESC LIMIT 1""",
            (discord_id, discord_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def complete_match(match_id: int, winner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE matches SET status = 'completed', winner_id = ?,
               completed_at = CURRENT_TIMESTAMP WHERE match_id = ?""",
            (winner_id, match_id),
        )
        await db.commit()


async def cancel_match(match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET status = 'cancelled' WHERE match_id = ?",
            (match_id,),
        )
        await db.commit()


async def get_ready_check(match_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ready_checks WHERE match_id = ?", (match_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_ready(match_id: int, player_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        rc = await get_ready_check(match_id)
        if not rc:
            return
        if rc["player1_id"] == player_id:
            await db.execute(
                "UPDATE ready_checks SET p1_ready = 1 WHERE match_id = ?", (match_id,)
            )
        else:
            await db.execute(
                "UPDATE ready_checks SET p2_ready = 1 WHERE match_id = ?", (match_id,)
            )
        await db.commit()


async def get_leaderboard(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT discord_id, username, elo, wins, losses
               FROM players
               WHERE wins + losses > 0
               ORDER BY elo DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_server_config(server_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO server_config (server_id) VALUES (?)", (server_id,)
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM server_config WHERE server_id = ?", (server_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row)


async def update_server_config(server_id: int, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_config (server_id) VALUES (?)", (server_id,)
        )
        for key, value in kwargs.items():
            await db.execute(
                f"UPDATE server_config SET {key} = ? WHERE server_id = ?",
                (value, server_id),
            )
        await db.commit()


async def get_server_player(discord_id: int, server_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_players WHERE discord_id = ? AND server_id = ?",
            (discord_id, server_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_server_player(discord_id: int, server_id: int, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_players (discord_id, server_id) VALUES (?, ?)",
            (discord_id, server_id),
        )
        for key, value in kwargs.items():
            await db.execute(
                f"UPDATE server_players SET {key} = ? WHERE discord_id = ? AND server_id = ?",
                (value, discord_id, server_id),
            )
        await db.commit()


async def set_player_elo_direct(discord_id: int, elo: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE players SET elo = ? WHERE discord_id = ?", (elo, discord_id)
        )
        await db.commit()


async def reset_player_stats(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE players SET elo = ?, wins = 0, losses = 0 WHERE discord_id = ?",
            (INITIAL_ELO, discord_id),
        )
        await db.commit()
