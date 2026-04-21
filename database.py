import aiosqlite
from config import INITIAL_ELO, DEFAULT_QUEUE_MODES

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
                mode         TEXT NOT NULL DEFAULT 'ranked',
                joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS matches (
                match_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                player1_id    INTEGER NOT NULL,
                player2_id    INTEGER NOT NULL,
                origin_server INTEGER,
                mode          TEXT NOT NULL DEFAULT 'ranked',
                status        TEXT DEFAULT 'pending',
                winner_id     INTEGER,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at  TIMESTAMP
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
                server_id          INTEGER PRIMARY KEY,
                queue_channel_id   INTEGER,
                results_channel_id INTEGER,
                settings           TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS premium_users (
                discord_id   INTEGER PRIMARY KEY,
                license_key  TEXT NOT NULL,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                granted_by   INTEGER
            );

            CREATE TABLE IF NOT EXISTS seasons (
                season_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                active       INTEGER DEFAULT 0,
                started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at     TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS season_history (
                discord_id   INTEGER,
                season_id    INTEGER,
                final_elo    INTEGER,
                wins         INTEGER,
                losses       INTEGER,
                rank_title   TEXT,
                PRIMARY KEY (discord_id, season_id)
            );

            CREATE TABLE IF NOT EXISTS queue_modes (
                mode_id      TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                description  TEXT DEFAULT '',
                enabled      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS guilds (
                guild_id     INTEGER PRIMARY KEY,
                name         TEXT NOT NULL,
                member_count INTEGER DEFAULT 0,
                last_seen    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        for mode_id, display_name, description in DEFAULT_QUEUE_MODES:
            await db.execute(
                "INSERT OR IGNORE INTO queue_modes (mode_id, display_name, description) VALUES (?, ?, ?)",
                (mode_id, display_name, description),
            )

        await db.commit()


# ── Players ──────────────────────────────────────────────────────────────────

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
            return dict(await cur.fetchone())


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


async def get_leaderboard(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.discord_id, p.username, p.elo, p.wins, p.losses,
                      CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
               FROM players p
               LEFT JOIN premium_users pr ON p.discord_id = pr.discord_id
               WHERE p.wins + p.losses > 0
               ORDER BY p.elo DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Queue ─────────────────────────────────────────────────────────────────────

async def enqueue(discord_id: int, server_id: int, elo: int, mode: str = "ranked"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO queue (discord_id, server_id, elo_at_join, mode) VALUES (?, ?, ?, ?)",
            (discord_id, server_id, elo, mode),
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


async def get_all_queue(mode: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if mode:
            sql = """SELECT q.*, p.username FROM queue q
                     JOIN players p ON q.discord_id = p.discord_id
                     WHERE q.mode = ? ORDER BY q.joined_at"""
            params = (mode,)
        else:
            sql = """SELECT q.*, p.username FROM queue q
                     JOIN players p ON q.discord_id = p.discord_id
                     ORDER BY q.joined_at"""
            params = ()
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Matches ───────────────────────────────────────────────────────────────────

async def create_match(player1_id: int, player2_id: int, server_id: int, mode: str = "ranked") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO matches (player1_id, player2_id, origin_server, mode) VALUES (?, ?, ?, ?)",
            (player1_id, player2_id, server_id, mode),
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
            "UPDATE matches SET status = 'cancelled' WHERE match_id = ?", (match_id,)
        )
        await db.commit()


# ── Ready checks ──────────────────────────────────────────────────────────────

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
        col = "p1_ready" if rc["player1_id"] == player_id else "p2_ready"
        await db.execute(
            f"UPDATE ready_checks SET {col} = 1 WHERE match_id = ?", (match_id,)
        )
        await db.commit()


# ── Server config ─────────────────────────────────────────────────────────────

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
            return dict(await cur.fetchone())


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


# ── Premium ───────────────────────────────────────────────────────────────────

async def is_premium(discord_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM premium_users WHERE discord_id = ?", (discord_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def grant_premium(discord_id: int, license_key: str, granted_by: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO premium_users (discord_id, license_key, granted_by) VALUES (?, ?, ?)",
            (discord_id, license_key, granted_by),
        )
        await db.commit()


async def revoke_premium(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM premium_users WHERE discord_id = ?", (discord_id,)
        )
        await db.commit()


async def get_premium_info(discord_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM premium_users WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── Seasons ───────────────────────────────────────────────────────────────────

async def get_active_season() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM seasons WHERE active = 1 LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def start_season(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE seasons SET active = 0")
        cur = await db.execute(
            "INSERT INTO seasons (name, active) VALUES (?, 1)", (name,)
        )
        await db.commit()
        return cur.lastrowid


async def end_season(season_id: int, soft_reset: bool = True):
    from config import INITIAL_ELO
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE wins + losses > 0"
        ) as cur:
            players = [dict(r) for r in await cur.fetchall()]

        from config import get_rank
        for p in players:
            await db.execute(
                """INSERT OR REPLACE INTO season_history
                   (discord_id, season_id, final_elo, wins, losses, rank_title)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (p["discord_id"], season_id, p["elo"], p["wins"], p["losses"], get_rank(p["elo"])),
            )
            if soft_reset:
                new_elo = INITIAL_ELO + round((p["elo"] - INITIAL_ELO) * 0.5)
                await db.execute(
                    "UPDATE players SET elo = ?, wins = 0, losses = 0 WHERE discord_id = ?",
                    (max(new_elo, 100), p["discord_id"]),
                )

        await db.execute(
            "UPDATE seasons SET active = 0, ended_at = CURRENT_TIMESTAMP WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def get_season_history(discord_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT sh.*, s.name as season_name
               FROM season_history sh JOIN seasons s ON sh.season_id = s.season_id
               WHERE sh.discord_id = ? ORDER BY sh.season_id DESC""",
            (discord_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_seasons() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM seasons ORDER BY season_id DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Queue modes ───────────────────────────────────────────────────────────────

async def sync_guilds(guilds: list[tuple]):
    async with aiosqlite.connect(DB_PATH) as db:
        for guild_id, name, member_count in guilds:
            await db.execute(
                """INSERT OR REPLACE INTO guilds (guild_id, name, member_count, last_seen)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
                (guild_id, name, member_count),
            )
        await db.commit()


async def remove_guild(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guilds WHERE guild_id = ?", (guild_id,))
        await db.commit()


async def get_guilds() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds ORDER BY name") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_players(limit: int = 50, offset: int = 0, search: str = "") -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.discord_id, p.username, p.elo, p.wins, p.losses,
                      CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
               FROM players p
               LEFT JOIN premium_users pr ON p.discord_id = pr.discord_id
               WHERE p.username LIKE ?
               ORDER BY p.elo DESC LIMIT ? OFFSET ?""",
            (f"%{search}%", limit, offset),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_premium_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT pr.*, p.username, p.elo
               FROM premium_users pr JOIN players p ON pr.discord_id = p.discord_id
               ORDER BY pr.activated_at DESC"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_queue_modes() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM queue_modes WHERE enabled = 1 ORDER BY mode_id"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_queue_mode(mode_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM queue_modes WHERE mode_id = ? AND enabled = 1", (mode_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_queue_mode(mode_id: str, display_name: str, description: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO queue_modes (mode_id, display_name, description, enabled) VALUES (?, ?, ?, 1)",
            (mode_id, display_name, description),
        )
        await db.commit()


async def delete_queue_mode(mode_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE queue_modes SET enabled = 0 WHERE mode_id = ?", (mode_id,)
        )
        await db.commit()


# ── Match history & server stats ──────────────────────────────────────────────

async def get_match_history(discord_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT m.*,
                 CASE WHEN m.player1_id = ? THEN p2.username ELSE p1.username END AS opponent_name,
                 CASE WHEN m.player1_id = ? THEN m.player2_id  ELSE m.player1_id  END AS opponent_id,
                 CASE
                   WHEN m.winner_id = ?           THEN 'win'
                   WHEN m.winner_id IS NOT NULL   THEN 'loss'
                   WHEN m.status = 'cancelled'    THEN 'cancelled'
                   ELSE m.status
                 END AS result
               FROM matches m
               JOIN players p1 ON m.player1_id = p1.discord_id
               JOIN players p2 ON m.player2_id = p2.discord_id
               WHERE (m.player1_id = ? OR m.player2_id = ?)
                 AND m.status IN ('completed', 'cancelled')
               ORDER BY m.created_at DESC LIMIT ?""",
            (discord_id, discord_id, discord_id, discord_id, discord_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_server_stats(server_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                 COUNT(*)                                           AS total_matches,
                 SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                 SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
                 SUM(CASE WHEN status IN ('pending','active') THEN 1 ELSE 0 END) AS active
               FROM matches WHERE origin_server = ?""",
            (server_id,),
        ) as cur:
            row = dict(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) AS in_queue FROM queue WHERE server_id = ?", (server_id,)
        ) as cur:
            row["in_queue"] = (await cur.fetchone())["in_queue"]
        async with db.execute(
            "SELECT COUNT(DISTINCT discord_id) AS unique_players FROM queue WHERE server_id = ?",
            (server_id,),
        ) as cur:
            row["unique_players"] = (await cur.fetchone())["unique_players"]
        return row
