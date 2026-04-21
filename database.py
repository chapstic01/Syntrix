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

        await db.executescript("""
            CREATE TABLE IF NOT EXISTS server_queue_games (
                server_id   INTEGER NOT NULL,
                queue_mode  TEXT NOT NULL,
                game_id     TEXT NOT NULL,
                PRIMARY KEY (server_id, queue_mode)
            );
            CREATE TABLE IF NOT EXISTS map_votes (
                match_id    INTEGER NOT NULL,
                player_id   INTEGER NOT NULL,
                map_choice  TEXT NOT NULL,
                PRIMARY KEY (match_id, player_id)
            );
            CREATE TABLE IF NOT EXISTS server_premium_grants (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL,
                granted_by  INTEGER NOT NULL,
                granted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                months      INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS server_ranks (
                server_id   INTEGER NOT NULL,
                min_elo     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                emoji       TEXT DEFAULT '',
                PRIMARY KEY (server_id, min_elo)
            );
        """)

        # Schema migrations — safe to run repeatedly
        _migrations = [
            "ALTER TABLE server_config ADD COLUMN require_evidence INTEGER DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN score_mode INTEGER DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN rematch_cooldown INTEGER DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN anonymous_queue INTEGER DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN update_channel_id INTEGER",
            "ALTER TABLE server_config ADD COLUMN match_category_id INTEGER",
            "ALTER TABLE server_config ADD COLUMN server_premium INTEGER DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN rounds_per_match INTEGER DEFAULT 0",
            "ALTER TABLE matches ADD COLUMN voice_channel_id INTEGER",
            "ALTER TABLE matches ADD COLUMN text_channel_id INTEGER",
            "ALTER TABLE matches ADD COLUMN p1_score INTEGER",
            "ALTER TABLE matches ADD COLUMN p2_score INTEGER",
            "ALTER TABLE matches ADD COLUMN map_played TEXT",
        ]
        for sql in _migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass

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


# ── Server queue games ────────────────────────────────────────────────────────

async def get_server_queue_game(server_id: int, queue_mode: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT game_id FROM server_queue_games WHERE server_id=? AND queue_mode=?",
            (server_id, queue_mode),
        ) as cur:
            row = await cur.fetchone()
            return row["game_id"] if row else None


async def set_server_queue_game(server_id: int, queue_mode: str, game_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO server_queue_games (server_id, queue_mode, game_id) VALUES (?,?,?)",
            (server_id, queue_mode, game_id),
        )
        await db.commit()


async def remove_server_queue_game(server_id: int, queue_mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM server_queue_games WHERE server_id=? AND queue_mode=?",
            (server_id, queue_mode),
        )
        await db.commit()


async def get_server_queue_games(server_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_queue_games WHERE server_id=?", (server_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def is_server_premium(server_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        # Check manual flag
        async with db.execute(
            "SELECT server_premium FROM server_config WHERE server_id=?", (server_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                return True
        # Check active timed grant
        async with db.execute(
            "SELECT 1 FROM server_premium_grants WHERE server_id=? AND expires_at > CURRENT_TIMESTAMP LIMIT 1",
            (server_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def grant_server_premium(server_id: int, granted_by: int, months: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO server_premium_grants (server_id, granted_by, months, expires_at)
               VALUES (?, ?, ?, datetime('now', '+' || ? || ' months'))""",
            (server_id, granted_by, months, months),
        )
        await db.commit()


async def get_server_premium_grant(server_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM server_premium_grants WHERE server_id=? AND expires_at > CURRENT_TIMESTAMP
               ORDER BY expires_at DESC LIMIT 1""",
            (server_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── Map votes ─────────────────────────────────────────────────────────────────

async def submit_map_vote(match_id: int, player_id: int, map_choice: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO map_votes (match_id, player_id, map_choice) VALUES (?,?,?)",
            (match_id, player_id, map_choice),
        )
        await db.commit()


async def get_map_votes(match_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM map_votes WHERE match_id=?", (match_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_match_channels(match_id: int, voice_id: int, text_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET voice_channel_id=?, text_channel_id=? WHERE match_id=?",
            (voice_id, text_id, match_id),
        )
        await db.commit()


async def set_match_map(match_id: int, map_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE matches SET map_played=? WHERE match_id=?", (map_name, match_id)
        )
        await db.commit()


async def set_match_score(match_id: int, player_id: int, score: int, p1_id: int):
    col = "p1_score" if player_id == p1_id else "p2_score"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE matches SET {col}=? WHERE match_id=?", (score, match_id)
        )
        await db.commit()


async def check_rematch_cooldown(p1_id: int, p2_id: int, cooldown_seconds: int) -> bool:
    """Returns True if the pair can be matched (no active cooldown)."""
    if cooldown_seconds <= 0:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT completed_at FROM matches
               WHERE ((player1_id=? AND player2_id=?) OR (player1_id=? AND player2_id=?))
                 AND status='completed'
               ORDER BY completed_at DESC LIMIT 1""",
            (p1_id, p2_id, p2_id, p1_id),
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return True
    from datetime import datetime, timezone
    try:
        last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= cooldown_seconds
    except Exception:
        return True


async def get_server_ranks(server_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_ranks WHERE server_id=? ORDER BY min_elo DESC", (server_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_server_rank(server_id: int, min_elo: int, name: str, emoji: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO server_ranks (server_id, min_elo, name, emoji) VALUES (?,?,?,?)",
            (server_id, min_elo, name, emoji),
        )
        await db.commit()


async def remove_server_rank(server_id: int, min_elo: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM server_ranks WHERE server_id=? AND min_elo=?", (server_id, min_elo)
        )
        await db.commit()


async def get_rank_for_server(elo: int, server_id: int) -> str:
    """Returns server-custom rank name if set, otherwise falls back to global tiers."""
    from config import get_rank
    ranks = await get_server_ranks(server_id)
    if not ranks:
        return get_rank(elo)
    for r in ranks:  # already sorted DESC by min_elo
        if elo >= r["min_elo"]:
            prefix = r["emoji"] + " " if r["emoji"] else ""
            return prefix + r["name"]
    # Below all tiers — use lowest tier
    lowest = ranks[-1]
    prefix = lowest["emoji"] + " " if lowest["emoji"] else ""
    return prefix + lowest["name"]


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
