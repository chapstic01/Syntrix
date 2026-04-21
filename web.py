import os
import secrets
import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import aiosqlite
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from config import (
    BOT_INVITE_URL, SESSION_SECRET, ADMIN_USER_ID,
    DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI,
    get_rank,
)

DB_PATH = "matchmaking.db"
app = FastAPI()

AUTH_COOKIE = "sx_sess"
STATE_COOKIE = "sx_state"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours

_signer = URLSafeTimedSerializer(SESSION_SECRET)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def query(sql: str, params: tuple = ()):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]
    except Exception:
        return []


async def execute(sql: str, params: tuple = ()):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(sql, params)
            await db.commit()
        return True
    except Exception:
        return False


def get_session(request: Request) -> dict | None:
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        return None
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

def is_logged_in(request: Request) -> bool:
    return get_session(request) is not None

def is_authed(request: Request) -> bool:
    """Owner-only check — kept for all global admin endpoints."""
    sess = get_session(request)
    return sess is not None and str(sess.get("id")) == str(ADMIN_USER_ID)

def is_owner(request: Request) -> bool:
    return is_authed(request)

def get_allowed_guilds(request: Request) -> list[int] | None:
    sess = get_session(request)
    if not sess:
        return []
    if str(sess.get("id")) == str(ADMIN_USER_ID):
        return None  # None = owner, all guilds allowed
    return [int(g) for g in sess.get("guild_ids", [])]

def can_access_guild(request: Request, guild_id: int) -> bool:
    allowed = get_allowed_guilds(request)
    if allowed is None:
        return True
    return guild_id in allowed


# ── Public API ─────────────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
async def api_leaderboard():
    rows = await query(
        """SELECT p.discord_id, p.username, p.elo, p.wins, p.losses,
                  CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
           FROM players p LEFT JOIN premium_users pr ON p.discord_id = pr.discord_id
           WHERE p.wins + p.losses > 0 ORDER BY p.elo DESC LIMIT 20"""
    )
    for r in rows:
        r["rank"] = get_rank(r["elo"])
    return JSONResponse(rows)


@app.get("/api/queue")
async def api_queue():
    rows = await query(
        """SELECT q.discord_id, p.username, q.elo_at_join, q.joined_at, q.mode,
                  CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
           FROM queue q JOIN players p ON q.discord_id = p.discord_id
           LEFT JOIN premium_users pr ON q.discord_id = pr.discord_id
           ORDER BY q.joined_at"""
    )
    return JSONResponse(rows)


@app.get("/api/matches")
async def api_matches():
    rows = await query(
        """SELECT m.match_id, m.status, m.mode, m.created_at, m.completed_at,
                  p1.username as player1, p2.username as player2,
                  COALESCE(pw.username,'') as winner
           FROM matches m
           JOIN players p1 ON m.player1_id = p1.discord_id
           JOIN players p2 ON m.player2_id = p2.discord_id
           LEFT JOIN players pw ON m.winner_id = pw.discord_id
           ORDER BY m.created_at DESC LIMIT 20"""
    )
    return JSONResponse(rows)


@app.get("/api/stats")
async def api_stats():
    players = await query("SELECT COUNT(*) as c FROM players")
    matches = await query("SELECT COUNT(*) as c FROM matches WHERE status='completed'")
    queue = await query("SELECT COUNT(*) as c FROM queue")
    season = await query("SELECT name FROM seasons WHERE active=1 LIMIT 1")
    return JSONResponse({
        "total_players": players[0]["c"] if players else 0,
        "total_matches": matches[0]["c"] if matches else 0,
        "queue_size": queue[0]["c"] if queue else 0,
        "season": season[0]["name"] if season else None,
    })


# ── Dashboard API ──────────────────────────────────────────────────────────────

@app.get("/api/dash/servers")
async def dash_servers(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query("SELECT * FROM guilds ORDER BY name")
    configs = await query("SELECT * FROM server_config")
    cfg_map = {c["server_id"]: c for c in configs}
    for r in rows:
        cfg = cfg_map.get(r["guild_id"], {})
        r["queue_channel_id"] = cfg.get("queue_channel_id")
        r["results_channel_id"] = cfg.get("results_channel_id")
    return JSONResponse(rows)


@app.post("/api/dash/server/{server_id}/config")
async def dash_server_config(server_id: int, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_config (server_id) VALUES (?)", (server_id,)
        )
        for key in ("queue_channel_id", "results_channel_id"):
            if key in body:
                await db.execute(
                    f"UPDATE server_config SET {key}=? WHERE server_id=?",
                    (body[key] or None, server_id),
                )
        await db.commit()
    return JSONResponse({"ok": True})


@app.get("/api/dash/players")
async def dash_players(request: Request, search: str = "", offset: int = 0):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query(
        """SELECT p.discord_id, p.username, p.elo, p.wins, p.losses,
                  CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
           FROM players p LEFT JOIN premium_users pr ON p.discord_id = pr.discord_id
           WHERE p.username LIKE ? ORDER BY p.elo DESC LIMIT 30 OFFSET ?""",
        (f"%{search}%", offset),
    )
    for r in rows:
        r["rank"] = get_rank(r["elo"])
    return JSONResponse(rows)


@app.post("/api/dash/player/{discord_id}/elo")
async def dash_set_elo(discord_id: int, request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    elo = int(body.get("elo", 1000))
    await execute("UPDATE players SET elo=? WHERE discord_id=?", (max(0, elo), discord_id))
    return JSONResponse({"ok": True})


@app.post("/api/dash/player/{discord_id}/reset")
async def dash_reset_stats(discord_id: int, request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    await execute("UPDATE players SET elo=1000, wins=0, losses=0 WHERE discord_id=?", (discord_id,))
    return JSONResponse({"ok": True})


@app.post("/api/dash/player/{discord_id}/premium")
async def dash_toggle_premium(discord_id: int, request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    if body.get("grant"):
        await execute(
            "INSERT OR REPLACE INTO premium_users (discord_id, license_key) VALUES (?, 'admin-granted')",
            (discord_id,),
        )
    else:
        await execute("DELETE FROM premium_users WHERE discord_id=?", (discord_id,))
    return JSONResponse({"ok": True})


@app.post("/api/dash/player/{discord_id}/ban")
async def dash_ban(discord_id: int, request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    server_id = int(body.get("server_id", 0))
    ban = bool(body.get("ban", True))
    reason = body.get("reason", "")
    await execute(
        "INSERT OR IGNORE INTO server_players (discord_id, server_id) VALUES (?,?)",
        (discord_id, server_id),
    )
    await execute(
        "UPDATE server_players SET banned=?, notes=? WHERE discord_id=? AND server_id=?",
        (1 if ban else 0, reason, discord_id, server_id),
    )
    return JSONResponse({"ok": True})


@app.get("/api/dash/premium_users")
async def dash_premium_users(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query(
        """SELECT pr.discord_id, p.username, p.elo, pr.activated_at, pr.granted_by
           FROM premium_users pr JOIN players p ON pr.discord_id = p.discord_id
           ORDER BY pr.activated_at DESC"""
    )
    return JSONResponse(rows)


@app.get("/api/dash/seasons")
async def dash_seasons(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query("SELECT * FROM seasons ORDER BY season_id DESC")
    return JSONResponse(rows)


@app.post("/api/dash/season/start")
async def dash_season_start(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    name = body.get("name", "New Season")
    existing = await query("SELECT 1 FROM seasons WHERE active=1")
    if existing:
        return JSONResponse({"error": "A season is already active"}, status_code=400)
    await execute("UPDATE seasons SET active=0")
    await execute("INSERT INTO seasons (name, active) VALUES (?,1)", (name,))
    return JSONResponse({"ok": True})


@app.post("/api/dash/season/end")
async def dash_season_end(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from database import end_season, get_active_season
    season = await get_active_season()
    if not season:
        return JSONResponse({"error": "No active season"}, status_code=400)
    await end_season(season["season_id"], soft_reset=True)
    return JSONResponse({"ok": True})


@app.get("/api/dash/modes")
async def dash_modes(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query("SELECT * FROM queue_modes ORDER BY mode_id")
    return JSONResponse(rows)


@app.post("/api/dash/modes")
async def dash_add_mode(request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    mode_id = body.get("mode_id", "").lower().replace(" ", "_")
    display_name = body.get("display_name", "")
    description = body.get("description", "")
    if not mode_id or not display_name:
        return JSONResponse({"error": "mode_id and display_name required"}, status_code=400)
    await execute(
        "INSERT OR REPLACE INTO queue_modes (mode_id, display_name, description, enabled) VALUES (?,?,?,1)",
        (mode_id, display_name, description),
    )
    return JSONResponse({"ok": True})


@app.delete("/api/dash/modes/{mode_id}")
async def dash_delete_mode(mode_id: str, request: Request):
    if not is_authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if mode_id in ("ranked", "casual"):
        return JSONResponse({"error": "Cannot remove built-in modes"}, status_code=400)
    await execute("UPDATE queue_modes SET enabled=0 WHERE mode_id=?", (mode_id,))
    return JSONResponse({"ok": True})


@app.get("/api/games")
async def api_games():
    from config import GAMES
    return JSONResponse([
        {"id": k, "name": v["name"], "map_count": len(v["maps"])}
        for k, v in GAMES.items()
    ])


@app.get("/api/dash/server/{server_id}/settings")
async def dash_get_server_settings(server_id: int, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = await query("SELECT * FROM server_config WHERE server_id=?", (server_id,))
    return JSONResponse(rows[0] if rows else {})


@app.post("/api/dash/server/{server_id}/settings")
async def dash_update_server_settings(server_id: int, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    allowed = ("score_mode", "require_evidence", "rounds_per_match", "rematch_cooldown",
               "anonymous_queue", "match_category_id", "update_channel_id", "server_premium")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO server_config (server_id) VALUES (?)", (server_id,))
        for key in allowed:
            if key in body:
                val = body[key]
                if val == "" or val is None:
                    val = None
                await db.execute(
                    f"UPDATE server_config SET {key}=? WHERE server_id=?", (val, server_id)
                )
        await db.commit()
    return JSONResponse({"ok": True})


@app.get("/api/dash/server/{server_id}/games")
async def dash_server_games(server_id: int, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from config import GAMES
    rows = await query(
        "SELECT * FROM server_queue_games WHERE server_id=?", (server_id,)
    )
    for r in rows:
        g = GAMES.get(r["game_id"], {})
        r["game_name"] = g.get("name", r["game_id"])
        r["map_count"] = len(g.get("maps", []))
    return JSONResponse(rows)


@app.post("/api/dash/server/{server_id}/games")
async def dash_set_server_game(server_id: int, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from config import GAMES, MAX_GAMES_FREE, MAX_GAMES_PREMIUM
    body = await request.json()
    mode = body.get("queue_mode", "")
    game_id = body.get("game_id", "")
    if not mode or game_id not in GAMES:
        return JSONResponse({"error": "Invalid mode or game"}, status_code=400)
    premium_rows = await query(
        "SELECT server_premium FROM server_config WHERE server_id=?", (server_id,)
    )
    is_premium = bool(premium_rows and premium_rows[0].get("server_premium"))
    limit = MAX_GAMES_PREMIUM if is_premium else MAX_GAMES_FREE
    existing = await query(
        "SELECT queue_mode FROM server_queue_games WHERE server_id=?", (server_id,)
    )
    already_this_mode = any(r["queue_mode"] == mode for r in existing)
    if not already_this_mode and len(existing) >= limit:
        return JSONResponse({"error": f"Limit of {limit} game(s) reached"}, status_code=400)
    await execute(
        "INSERT OR REPLACE INTO server_queue_games (server_id, queue_mode, game_id) VALUES (?,?,?)",
        (server_id, mode, game_id),
    )
    return JSONResponse({"ok": True})


@app.delete("/api/dash/server/{server_id}/games/{mode}")
async def dash_delete_server_game(server_id: int, mode: str, request: Request):
    if not can_access_guild(request, server_id):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    await execute(
        "DELETE FROM server_queue_games WHERE server_id=? AND queue_mode=?", (server_id, mode)
    )
    return JSONResponse({"ok": True})


# ── Me + My Servers ────────────────────────────────────────────────────────────

@app.get("/api/me")
async def api_me(request: Request):
    sess = get_session(request)
    if not sess:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({
        "id": str(sess.get("id")),
        "username": sess.get("username"),
        "is_owner": str(sess.get("id")) == str(ADMIN_USER_ID),
        "guild_ids": sess.get("guild_ids", []),
    })


@app.get("/api/dash/myservers")
async def dash_my_servers(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    allowed = get_allowed_guilds(request)
    rows = await query("SELECT * FROM guilds ORDER BY name")
    configs = await query("SELECT * FROM server_config")
    cfg_map = {c["server_id"]: c for c in configs}
    if allowed is not None:
        rows = [r for r in rows if r["guild_id"] in allowed]
    for r in rows:
        cfg = cfg_map.get(r["guild_id"], {})
        r["queue_channel_id"] = cfg.get("queue_channel_id")
        r["results_channel_id"] = cfg.get("results_channel_id")
    return JSONResponse(rows)


# ── Console (owner only) ────────────────────────────────────────────────────────

@app.get("/api/console/guilds")
async def console_guilds(request: Request):
    if not is_owner(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return JSONResponse({"error": "Bot not connected"}, status_code=503)
    return JSONResponse([
        {"id": str(g.id), "name": g.name, "member_count": g.member_count,
         "icon": str(g.icon.url) if g.icon else None}
        for g in sorted(bot.guilds, key=lambda g: g.name)
    ])


@app.get("/api/console/guild/{guild_id}/channels")
async def console_channels(guild_id: int, request: Request):
    if not is_owner(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return JSONResponse({"error": "Bot not connected"}, status_code=503)
    guild = bot.get_guild(guild_id)
    if not guild:
        return JSONResponse({"error": "Guild not found"}, status_code=404)
    return JSONResponse([
        {"id": str(ch.id), "name": ch.name,
         "category": ch.category.name if ch.category else None}
        for ch in sorted(guild.text_channels,
                         key=lambda c: (c.category.position if c.category else 0, c.position))
    ])


@app.get("/api/console/guild/{guild_id}/channel/{channel_id}/messages")
async def console_messages(guild_id: int, channel_id: int, request: Request, limit: int = 50, before: str = ""):
    if not is_owner(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return JSONResponse({"error": "Bot not connected"}, status_code=503)
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not channel:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    import discord as _discord
    kwargs: dict = {"limit": min(limit, 100)}
    if before:
        try:
            kwargs["before"] = _discord.Object(id=int(before))
        except ValueError:
            pass
    result = []
    async for msg in channel.history(**kwargs):
        result.append({
            "id": str(msg.id),
            "content": msg.content or "",
            "author": {"id": str(msg.author.id), "name": msg.author.display_name,
                       "avatar": str(msg.author.display_avatar.url), "bot": msg.author.bot},
            "timestamp": msg.created_at.isoformat(),
            "embeds": len(msg.embeds),
            "attachments": [{"filename": a.filename, "url": a.url} for a in msg.attachments],
        })
    return JSONResponse(result)


@app.post("/api/console/guild/{guild_id}/channel/{channel_id}/send")
async def console_send(guild_id: int, channel_id: int, request: Request):
    if not is_owner(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return JSONResponse({"error": "Bot not connected"}, status_code=503)
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "Content required"}, status_code=400)
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not channel:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    await channel.send(content)
    return JSONResponse({"ok": True})


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request, error: str = ""):
    if is_logged_in(request):
        return RedirectResponse("/dashboard", status_code=302)
    return HTMLResponse(LOGIN_HTML.replace("{{ERROR}}", error))


@app.get("/dashboard/oauth")
async def dashboard_oauth(request: Request):
    state = secrets.token_urlsafe(16)
    resp = RedirectResponse(
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20guilds"
        f"&state={state}",
        status_code=302,
    )
    resp.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax", max_age=300)
    return resp


@app.get("/dashboard/callback")
async def dashboard_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/dashboard/login?error={error}", status_code=302)

    stored_state = request.cookies.get(STATE_COOKIE)
    if not state or state != stored_state:
        return RedirectResponse("/dashboard/login?error=Invalid+state", status_code=302)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            return RedirectResponse("/dashboard/login?error=Token+exchange+failed", status_code=302)

        access_token = token_resp.json().get("access_token")
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user = user_resp.json()

    user_id = int(user.get("id", 0))
    guild_ids: list[int] = []

    if user_id != ADMIN_USER_ID:
        guilds_resp = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_guilds = guilds_resp.json() if guilds_resp.status_code == 200 else []
        bot = getattr(request.app.state, "bot", None)
        bot_guild_ids = {g.id for g in bot.guilds} if bot else None
        for g in user_guilds:
            perms = int(g.get("permissions", "0"))
            if perms & 0x20 or perms & 0x8:  # MANAGE_GUILD or ADMINISTRATOR
                gid = int(g["id"])
                if bot_guild_ids is None or gid in bot_guild_ids:
                    guild_ids.append(gid)
        if not guild_ids:
            return RedirectResponse("/dashboard/login?error=No+managed+servers+with+Syntrix+found", status_code=302)

    token = _signer.dumps({"id": user_id, "username": user.get("username", ""), "guild_ids": guild_ids})
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie(AUTH_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
    resp.delete_cookie(STATE_COOKIE)
    return resp


@app.get("/dashboard/logout")
async def dashboard_logout():
    resp = RedirectResponse("/dashboard/login", status_code=302)
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/{path:path}", response_class=HTMLResponse)
async def dashboard(request: Request, path: str = ""):
    if not is_logged_in(request):
        return RedirectResponse("/dashboard/login", status_code=302)
    return HTMLResponse(DASHBOARD_HTML)


# ── Public site ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(PUBLIC_HTML.replace("{{INVITE_URL}}", BOT_INVITE_URL))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC SITE HTML
# ══════════════════════════════════════════════════════════════════════════════

PUBLIC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Syntrix — Competitive Matchmaking</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{--bg:#07070f;--card:#0e0e1c;--border:rgba(139,92,246,.15);--border-h:rgba(139,92,246,.4);--accent:#7c3aed;--accent2:#a855f7;--glow:rgba(124,58,237,.25);--text:#f1f5f9;--muted:#64748b;--sub:#94a3b8;--green:#10b981;--red:#ef4444;--gold:#f59e0b;--silver:#94a3b8;--bronze:#b45309;--r:16px}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:Inter,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}
.orb{position:fixed;border-radius:50%;filter:blur(120px);pointer-events:none;z-index:0;opacity:.3}
.o1{width:600px;height:600px;background:#4c1d95;top:-200px;left:-200px}
.o2{width:500px;height:500px;background:#1e1b4b;bottom:-150px;right:-100px}
.o3{width:300px;height:300px;background:#6d28d9;top:40%;left:50%;transform:translateX(-50%);opacity:.12}
.page{position:relative;z-index:1}
.wrap{max-width:1100px;margin:0 auto;padding:0 24px}
nav{border-bottom:1px solid var(--border);backdrop-filter:blur(20px);background:rgba(7,7,15,.85);position:sticky;top:0;z-index:100}
.nav-i{display:flex;align-items:center;justify-content:space-between;height:64px}
.logo{font-size:22px;font-weight:900;letter-spacing:3px;background:linear-gradient(135deg,#a855f7,#7c3aed,#6366f1);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.nav-links{display:flex;gap:8px;align-items:center}
.nl{color:var(--sub);text-decoration:none;font-size:14px;font-weight:500;padding:8px 14px;border-radius:8px;transition:all .2s}
.nl:hover{color:var(--text);background:rgba(255,255,255,.05)}
.btn-inv{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;text-decoration:none;font-size:14px;font-weight:600;padding:9px 20px;border-radius:10px;transition:all .2s;box-shadow:0 0 20px var(--glow)}
.btn-inv:hover{transform:translateY(-1px);box-shadow:0 0 30px var(--glow)}
.hero{text-align:center;padding:100px 0 70px}
.badge{display:inline-flex;align-items:center;gap:8px;background:rgba(124,58,237,.12);border:1px solid var(--border);border-radius:100px;padding:6px 16px;font-size:13px;color:var(--accent2);font-weight:500;margin-bottom:28px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
h1{font-size:clamp(52px,8vw,96px);font-weight:900;letter-spacing:-2px;line-height:1;background:linear-gradient(135deg,#fff 0%,#a855f7 50%,#7c3aed 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:20px}
.hero p{font-size:18px;color:var(--sub);max-width:480px;margin:0 auto 40px;line-height:1.6}
.actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.btn-p{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;text-decoration:none;font-size:15px;font-weight:600;padding:13px 28px;border-radius:12px;transition:all .2s;box-shadow:0 0 30px var(--glow);border:none;cursor:pointer}
.btn-p:hover{transform:translateY(-2px);box-shadow:0 0 50px var(--glow)}
.btn-s{background:rgba(255,255,255,.05);border:1px solid var(--border);color:var(--text);text-decoration:none;font-size:15px;font-weight:600;padding:13px 28px;border-radius:12px;transition:all .2s}
.btn-s:hover{background:rgba(255,255,255,.08);border-color:var(--border-h)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:60px}
.sc{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:28px 24px;text-align:center;transition:all .3s}
.sc:hover{border-color:var(--border-h);transform:translateY(-2px)}
.sv{font-size:42px;font-weight:800;line-height:1;background:linear-gradient(135deg,#fff,var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
.sl{font-size:13px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:1px}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin-bottom:60px}
.feat{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:28px;transition:all .3s}
.feat:hover{border-color:var(--border-h);transform:translateY(-2px)}
.feat-icon{font-size:32px;margin-bottom:14px}
.feat-title{font-size:16px;font-weight:700;margin-bottom:8px}
.feat-desc{font-size:14px;color:var(--sub);line-height:1.6}
.sec{margin-bottom:60px}
.sh{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.st{font-size:18px;font-weight:700;display:flex;align-items:center;gap:10px}
.si{width:32px;height:32px;border-radius:8px;background:rgba(124,58,237,.2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:16px}
.rfbtn{background:none;border:1px solid var(--border);color:var(--muted);font-size:12px;padding:6px 12px;border-radius:8px;cursor:pointer;font-family:inherit;transition:all .2s;font-weight:500}
.rfbtn:hover{border-color:var(--border-h);color:var(--text)}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
table{width:100%;border-collapse:collapse}
th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px;padding:14px 20px;text-align:left;border-bottom:1px solid var(--border);background:rgba(255,255,255,.02)}
td{padding:14px 20px;font-size:14px;border-bottom:1px solid rgba(255,255,255,.04)}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.rank{font-weight:700;font-size:15px;width:40px}
.rank-1{color:var(--gold)}.rank-2{color:var(--silver)}.rank-3{color:var(--bronze)}.rank-other{color:var(--muted)}
.elo-b{display:inline-flex;align-items:center;background:rgba(124,58,237,.12);border:1px solid rgba(124,58,237,.25);color:var(--accent2);font-weight:700;font-size:13px;padding:3px 10px;border-radius:6px}
.wl{font-size:13px}.wins{color:var(--green);font-weight:600}.losses{color:var(--red);font-weight:600}.sep{color:var(--muted);margin:0 3px}
.bdg{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;padding:3px 10px;border-radius:100px;text-transform:uppercase;letter-spacing:.5px}
.bdg-a{background:rgba(16,185,129,.12);color:var(--green);border:1px solid rgba(16,185,129,.25)}
.bdg-c{background:rgba(99,102,241,.12);color:#818cf8;border:1px solid rgba(99,102,241,.25)}
.bdg-x{background:rgba(239,68,68,.08);color:#f87171;border:1px solid rgba(239,68,68,.2)}
.bdg-dot{width:5px;height:5px;border-radius:50%;background:currentColor}
.qg{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.qc{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;display:flex;align-items:center;gap:12px;transition:all .2s}
.qc:hover{border-color:var(--border-h);transform:translateY(-1px)}
.qa{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px;flex-shrink:0}
.qn{font-weight:600;font-size:14px;margin-bottom:3px}
.qe{font-size:12px;color:var(--muted)}
.empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:14px}
.ei{font-size:40px;margin-bottom:12px;opacity:.4}
.wr-bar{height:4px;background:rgba(255,255,255,.06);border-radius:2px;margin-top:4px;overflow:hidden}
.wr-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:2px;transition:width .6s ease}
.premium-cta{background:linear-gradient(135deg,rgba(124,58,237,.15),rgba(168,85,247,.1));border:1px solid var(--border-h);border-radius:var(--r);padding:48px 40px;text-align:center;margin-bottom:60px}
.premium-cta h2{font-size:32px;font-weight:800;margin-bottom:12px;background:linear-gradient(135deg,#fff,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.premium-cta p{color:var(--sub);margin-bottom:28px;font-size:16px}
.perks{display:flex;gap:20px;justify-content:center;flex-wrap:wrap;margin-bottom:28px}
.perk{font-size:14px;color:var(--sub)}
footer{border-top:1px solid var(--border);margin-top:80px;padding:40px 0;text-align:center;color:var(--muted);font-size:13px}
.fl{font-size:18px;font-weight:900;letter-spacing:3px;background:linear-gradient(135deg,#a855f7,#7c3aed);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:10px}
.cmd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.cmd-group{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px 22px}
.cg-label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.2px;margin-bottom:12px}
.cmd-list{display:flex;flex-direction:column;gap:8px}
.cmd-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;font-size:13px}
.cmd-row code{background:rgba(124,58,237,.12);color:#a855f7;padding:2px 8px;border-radius:5px;font-size:12px;white-space:nowrap;flex-shrink:0}
.cmd-row span{color:var(--muted);text-align:right;font-size:12px}
@media(max-width:640px){.stats{grid-template-columns:1fr}.hero{padding:60px 0 40px}th,td{padding:12px 14px}.nav-links .nl{display:none}.cmd-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div>
<div class="page">
<nav><div class="wrap"><div class="nav-i">
  <div class="logo">SYNTRIX</div>
  <div class="nav-links">
    <a href="#commands" class="nl">Commands</a>
    <a href="#leaderboard" class="nl">Leaderboard</a>
    <a href="#queue" class="nl">Queue</a>
    <a href="#matches" class="nl">Matches</a>
    <a href="{{INVITE_URL}}" class="btn-inv" target="_blank">+ Add to Discord</a>
  </div>
</div></div></nav>

<div class="wrap">
  <div class="hero">
    <div class="badge"><span class="dot"></span>Global Matchmaking — Live</div>
    <h1>SYNTRIX</h1>
    <p>Cross-server competitive matchmaking. ELO-ranked, fair, and always on.</p>
    <div class="actions">
      <a href="{{INVITE_URL}}" class="btn-p" target="_blank">Add to Your Server</a>
      <a href="#leaderboard" class="btn-s">View Leaderboard</a>
    </div>
  </div>

  <div class="stats">
    <div class="sc"><div class="sv" id="s-players">—</div><div class="sl">Registered Players</div></div>
    <div class="sc"><div class="sv" id="s-matches">—</div><div class="sl">Matches Completed</div></div>
    <div class="sc"><div class="sv" id="s-queue">—</div><div class="sl">In Queue Now</div></div>
  </div>

  <div class="features">
    <div class="feat"><div class="feat-icon">🌍</div><div class="feat-title">Cross-Server Queue</div><div class="feat-desc">One global queue connects players across every server the bot is in.</div></div>
    <div class="feat"><div class="feat-icon">📊</div><div class="feat-title">ELO Ranking</div><div class="feat-desc">Standard chess ELO with Iron → Master rank tiers. Fair matchmaking always.</div></div>
    <div class="feat"><div class="feat-icon">🎮</div><div class="feat-title">Multiple Modes</div><div class="feat-desc">Ranked, Casual, and custom modes. ELO only changes in ranked.</div></div>
    <div class="feat"><div class="feat-icon">🗺️</div><div class="feat-title">Map Voting</div><div class="feat-desc">Admins assign a game per queue. Players vote on the map before each match — ties are broken randomly.</div></div>
    <div class="feat"><div class="feat-icon">🔊</div><div class="feat-title">Auto Match Channels</div><div class="feat-desc">Private voice + text channels are created for each match and deleted when it ends.</div></div>
    <div class="feat"><div class="feat-icon">📸</div><div class="feat-title">Score Reporting</div><div class="feat-desc">Players submit final scores via a modal. Evidence screenshots can be required by admins.</div></div>
    <div class="feat"><div class="feat-icon">🏆</div><div class="feat-title">Season System</div><div class="feat-desc">Competitive seasons with soft ELO resets and full stat history.</div></div>
    <div class="feat"><div class="feat-icon">⭐</div><div class="feat-title">Premium</div><div class="feat-desc">Wider match range, priority queue, up to 3 game queues, and exclusive badges.</div></div>
    <div class="feat"><div class="feat-icon">🛡️</div><div class="feat-title">Admin Tools</div><div class="feat-desc">Full ban, ELO control, result override, rematch cooldown, and per-server configuration.</div></div>
  </div>

  <div class="sec" id="commands">
    <div class="sh"><div class="st"><div class="si">📋</div>Commands</div></div>
    <div class="cmd-grid">
      <div class="cmd-group">
        <div class="cg-label">Queue</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/join [mode]</code><span>Enter the matchmaking queue</span></div>
          <div class="cmd-row"><code>/leave</code><span>Exit the queue</span></div>
          <div class="cmd-row"><code>/queue [mode]</code><span>See who is waiting</span></div>
          <div class="cmd-row"><code>/modes</code><span>List available game modes</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Match</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/match</code><span>View your active match</span></div>
          <div class="cmd-row"><code>/cancel</code><span>Cancel your active match</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Stats</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/profile [user]</code><span>ELO, rank, wins &amp; losses</span></div>
          <div class="cmd-row"><code>/leaderboard</code><span>Top 10 players by ELO</span></div>
          <div class="cmd-row"><code>/history [user]</code><span>Recent match results</span></div>
          <div class="cmd-row"><code>/stats</code><span>Server match activity</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Seasons</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/season info</code><span>Current season details</span></div>
          <div class="cmd-row"><code>/season history</code><span>Your past season records</span></div>
          <div class="cmd-row"><code>/season list</code><span>All seasons</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Premium</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/premium</code><span>Check or activate premium</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Utility</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/welcome</code><span>Post an intro embed</span></div>
          <div class="cmd-row"><code>/help</code><span>Full command reference</span></div>
          <div class="cmd-row"><code>/update</code><span>Broadcast update to all servers (owner)</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Admin — Channels</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/admin setup</code><span>Set queue &amp; results channels</span></div>
          <div class="cmd-row"><code>/admin matchcategory</code><span>Category for auto voice/text channels</span></div>
          <div class="cmd-row"><code>/admin setupdate</code><span>Channel for /update broadcasts</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Admin — Match Rules</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/admin setgame</code><span>Assign game to a queue mode</span></div>
          <div class="cmd-row"><code>/admin removegame</code><span>Remove a game assignment</span></div>
          <div class="cmd-row"><code>/admin scoremode</code><span>Toggle score-based reporting</span></div>
          <div class="cmd-row"><code>/admin requireevidence</code><span>Require screenshot evidence</span></div>
          <div class="cmd-row"><code>/admin setrounds</code><span>Set rounds per match</span></div>
          <div class="cmd-row"><code>/admin rematchcooldown</code><span>Rematch cooldown in minutes</span></div>
          <div class="cmd-row"><code>/admin anonymous</code><span>Hide player names until ready</span></div>
          <div class="cmd-row"><code>/admin serverpremium</code><span>Enable premium features for server</span></div>
          <div class="cmd-row"><code>/admin serversettings</code><span>View all current settings</span></div>
        </div>
      </div>
      <div class="cmd-group">
        <div class="cg-label">Admin — Players</div>
        <div class="cmd-list">
          <div class="cmd-row"><code>/admin setelo</code><span>Override a player's ELO</span></div>
          <div class="cmd-row"><code>/admin resetstats</code><span>Reset ELO, wins &amp; losses</span></div>
          <div class="cmd-row"><code>/admin ban</code><span>Ban player from this server's queue</span></div>
          <div class="cmd-row"><code>/admin unban</code><span>Lift a server ban</span></div>
          <div class="cmd-row"><code>/admin forcewinner</code><span>Force a match result</span></div>
          <div class="cmd-row"><code>/admin removequeue</code><span>Remove someone from queue</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="premium-cta">
    <h2>⭐ Syntrix Premium</h2>
    <p>Get matched faster and stand out on the leaderboard.</p>
    <div class="perks">
      <span class="perk">✨ Wider matchmaking range</span>
      <span class="perk">🏆 Priority queue position</span>
      <span class="perk">⭐ Premium badge on leaderboard</span>
      <span class="perk">📊 Full season history</span>
    </div>
    <a href="{{INVITE_URL}}" class="btn-p" target="_blank">Get Premium via /premium</a>
  </div>

  <div class="sec" id="leaderboard">
    <div class="sh"><div class="st"><div class="si">🏆</div>Leaderboard</div><button class="rfbtn" onclick="loadAll()">↻ Refresh</button></div>
    <div class="card"><table>
      <thead><tr><th>#</th><th>Player</th><th>ELO</th><th>Rank</th><th>Record</th><th>Win Rate</th></tr></thead>
      <tbody id="lb-body"><tr><td colspan="6"><div class="empty"><div class="ei">⏳</div>Loading…</div></td></tr></tbody>
    </table></div>
  </div>

  <div class="sec" id="queue">
    <div class="sh"><div class="st"><div class="si">⚡</div>Live Queue</div><button class="rfbtn" onclick="loadQueue()">↻ Refresh</button></div>
    <div id="queue-wrap"><div class="empty"><div class="ei">⏳</div>Loading…</div></div>
  </div>

  <div class="sec" id="matches">
    <div class="sh"><div class="st"><div class="si">⚔️</div>Recent Matches</div></div>
    <div class="card"><table>
      <thead><tr><th>Match</th><th>Mode</th><th>Players</th><th>Winner</th><th>Status</th><th>Date</th></tr></thead>
      <tbody id="m-body"><tr><td colspan="6"><div class="empty"><div class="ei">⏳</div>Loading…</div></td></tr></tbody>
    </table></div>
  </div>
</div>

<footer><div class="fl">SYNTRIX</div><div>Cross-server competitive matchmaking for Discord</div></footer>
</div>

<script>
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const ini=n=>n?n[0].toUpperCase():'?';
function fmtDate(iso){if(!iso)return'—';const d=new Date(iso+(iso.endsWith('Z')?'':'Z'));return d.toLocaleDateString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}
async function jf(url){try{return await(await fetch(url)).json()}catch{return null}}
function anim(id,t){const el=document.getElementById(id);if(!el)return;let s=0;const step=Math.ceil(t/40);const tm=setInterval(()=>{s=Math.min(s+step,t);el.textContent=s.toLocaleString();if(s>=t)clearInterval(tm)},30)}
async function loadStats(){const d=await jf('/api/stats');if(!d)return;anim('s-players',d.total_players);anim('s-matches',d.total_matches);anim('s-queue',d.queue_size)}
async function loadLeaderboard(){
  const d=await jf('/api/leaderboard');const tb=document.getElementById('lb-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="6"><div class="empty"><div class="ei">🏅</div>No ranked players yet.</div></td></tr>';return}
  const medals={1:'🥇',2:'🥈',3:'🥉'};
  tb.innerHTML=d.map((p,i)=>{
    const pos=i+1,total=p.wins+p.losses,wr=total>0?Math.round(p.wins/total*100):0;
    const pc=pos<=3?`rank-${pos}`:'rank-other';
    const pm=p.is_premium?'<span style="color:#a855f7;font-size:12px;margin-left:4px">⭐</span>':'';
    return`<tr><td><span class="rank ${pc}">${medals[pos]||pos}</span></td><td><strong>${esc(p.username)}</strong>${pm}</td><td><span class="elo-b">${p.elo}</span></td><td style="font-size:13px;color:var(--sub)">${esc(p.rank||'')}</td><td class="wl"><span class="wins">${p.wins}W</span><span class="sep">/</span><span class="losses">${p.losses}L</span></td><td><span style="font-size:13px;font-weight:600;color:var(--sub)">${wr}%</span><div class="wr-bar"><div class="wr-fill" style="width:${wr}%"></div></div></td></tr>`;
  }).join('');
}
async function loadQueue(){
  const d=await jf('/api/queue');const c=document.getElementById('queue-wrap');
  if(!d||!d.length){c.innerHTML='<div class="empty"><div class="ei">🎯</div>Queue is empty — join with /join!</div>';return}
  c.innerHTML='<div class="qg">'+d.map(p=>{
    const star=p.is_premium?'<span style="color:#a855f7">⭐</span> ':'';
    const mb=`<span style="font-size:10px;background:rgba(124,58,237,.15);border:1px solid rgba(124,58,237,.3);color:#a855f7;padding:1px 6px;border-radius:4px;margin-left:4px">${esc(p.mode||'ranked')}</span>`;
    return`<div class="qc"><div class="qa">${esc(ini(p.username))}</div><div><div class="qn">${star}${esc(p.username)}${mb}</div><div class="qe">ELO ${p.elo_at_join}</div></div></div>`;
  }).join('')+'</div>';
}
async function loadMatches(){
  const d=await jf('/api/matches');const tb=document.getElementById('m-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="6"><div class="empty"><div class="ei">⚔️</div>No matches yet.</div></td></tr>';return}
  tb.innerHTML=d.map(m=>{
    const sc={completed:'bdg-c',cancelled:'bdg-x',pending:'bdg-a',active:'bdg-a'}[m.status]||'bdg-a';
    return`<tr><td style="color:var(--muted);font-weight:600">#${m.match_id}</td><td><span style="font-size:12px;color:var(--accent2)">${esc(m.mode||'ranked')}</span></td><td><strong>${esc(m.player1)}</strong> <span style="color:var(--muted)">vs</span> <strong>${esc(m.player2)}</strong></td><td>${m.winner?`<span style="color:var(--accent2);font-weight:600">${esc(m.winner)}</span>`:'<span style="color:var(--muted)">—</span>'}</td><td><span class="bdg ${sc}"><span class="bdg-dot"></span>${m.status}</span></td><td style="color:var(--muted);font-size:13px">${fmtDate(m.created_at)}</td></tr>`;
  }).join('');
}
async function loadAll(){await Promise.all([loadStats(),loadLeaderboard(),loadQueue(),loadMatches()])}
loadAll();setInterval(()=>{loadQueue();loadStats()},30000);
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD LOGIN HTML
# ══════════════════════════════════════════════════════════════════════════════

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Syntrix Dashboard — Login</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,sans-serif;background:#07070f;color:#f1f5f9;min-height:100vh;display:flex;align-items:center;justify-content:center}
.orb{position:fixed;border-radius:50%;filter:blur(120px);pointer-events:none;opacity:.3}
.o1{width:500px;height:500px;background:#4c1d95;top:-200px;left:-200px}
.o2{width:400px;height:400px;background:#1e1b4b;bottom:-100px;right:-100px}
.box{position:relative;z-index:1;background:#0e0e1c;border:1px solid rgba(139,92,246,.2);border-radius:20px;padding:48px 40px;width:100%;max-width:400px;text-align:center}
.logo{font-size:24px;font-weight:900;letter-spacing:3px;background:linear-gradient(135deg,#a855f7,#7c3aed);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
.sub{font-size:14px;color:#64748b;margin-bottom:8px}
.notice{font-size:12px;color:#475569;margin-bottom:32px;line-height:1.5}
.btn-discord{display:flex;align-items:center;justify-content:center;gap:12px;width:100%;background:#5865f2;color:#fff;border:none;border-radius:12px;padding:14px;font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .2s;text-decoration:none;box-shadow:0 0 24px rgba(88,101,242,.35)}
.btn-discord:hover{background:#4752c4;transform:translateY(-1px);box-shadow:0 0 36px rgba(88,101,242,.5)}
.discord-icon{width:22px;height:22px;fill:#fff;flex-shrink:0}
.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);border-radius:8px;padding:10px 14px;font-size:13px;color:#f87171;margin-bottom:20px}
.lock{font-size:36px;margin-bottom:16px;opacity:.6}
</style>
</head>
<body>
<div class="orb o1"></div><div class="orb o2"></div>
<div class="box">
  <div class="lock">🛡️</div>
  <div class="logo">SYNTRIX</div>
  <div class="sub">Admin Dashboard</div>
  <div class="notice">Only the bot owner can access this dashboard.<br>Sign in with your Discord account to continue.</div>
  <div class="err" id="err" style="display:none">{{ERROR}}</div>
  <a href="/dashboard/oauth" class="btn-discord">
    <svg class="discord-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/>
    </svg>
    Login with Discord
  </a>
</div>
<script>
const err=document.getElementById('err');
const msg='{{ERROR}}';
if(msg&&msg.trim()){err.textContent=msg;err.style.display='block'}
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML  (Aegixa-inspired: horizontal tabs, inline cards, toast UX)
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Syntrix Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:#1e1f22;--bg2:#2b2d31;--bg3:#313338;
  --accent:#7c3aed;--accent2:#a855f7;--accent-glow:rgba(124,58,237,.25);
  --border:#3f4248;--border-h:rgba(124,58,237,.5);
  --text:#dbdee1;--text2:#b5bac1;--muted:#80848e;
  --green:#23a55a;--green-bg:rgba(35,165,90,.12);--green-border:rgba(35,165,90,.3);
  --red:#f23f43;--red-bg:rgba(242,63,67,.1);--red-border:rgba(242,63,67,.25);
  --gold:#f0b232;
  --r:8px;--r2:12px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}

/* ── Navbar ── */
nav{
  background:var(--bg2);border-bottom:1px solid var(--border);
  padding:0 28px;height:56px;display:flex;align-items:center;
  justify-content:space-between;flex-shrink:0;position:sticky;top:0;z-index:200;
}
.logo{
  font-size:15px;font-weight:900;letter-spacing:4px;
  background:linear-gradient(135deg,#c084fc,#7c3aed);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.logo-badge{
  font-size:10px;font-weight:700;letter-spacing:1.5px;
  background:rgba(124,58,237,.18);border:1px solid rgba(124,58,237,.35);
  color:#a78bfa;padding:2px 7px;border-radius:4px;
  -webkit-text-fill-color:#a78bfa;margin-left:10px;
}
.nav-actions{display:flex;align-items:center;gap:6px}
.na{
  color:var(--text2);text-decoration:none;font-size:13px;font-weight:500;
  padding:6px 12px;border-radius:var(--r);transition:all .15s;
  border:none;background:none;cursor:pointer;font-family:inherit;
}
.na:hover{background:rgba(255,255,255,.06);color:var(--text)}
.na-danger{color:#f87171}
.na-danger:hover{background:var(--red-bg)!important;color:var(--red)}

/* ── Tab bar ── */
.tabbar{
  background:var(--bg2);border-bottom:1px solid var(--border);
  padding:0 28px;display:flex;gap:2px;flex-shrink:0;
}
.tb{
  display:flex;align-items:center;gap:7px;
  padding:0 14px;height:44px;font-size:13px;font-weight:500;
  color:var(--muted);border:none;background:none;cursor:pointer;
  font-family:inherit;position:relative;transition:color .15s;white-space:nowrap;
}
.tb:hover{color:var(--text2)}
.tb.active{color:var(--text)}
.tb.active::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:2px 2px 0 0;
}
.tb-ico{font-size:14px}

/* ── Main ── */
.main{flex:1;padding:28px 32px;overflow-y:auto;max-width:1200px;width:100%}
.page{display:none}.page.active{display:block}
h2{font-size:17px;font-weight:700;color:var(--text);margin-bottom:20px;letter-spacing:-.2px}
.page-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.page-head h2{margin-bottom:0}

/* ── Stats grid ── */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.stat-card{
  background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);
  padding:18px 20px;transition:border-color .2s;
}
.stat-card:hover{border-color:rgba(124,58,237,.35)}
.stat-val{font-size:28px;font-weight:800;color:var(--text);line-height:1}
.stat-val.accent{background:linear-gradient(135deg,var(--text),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-lbl{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-top:6px}

/* ── Cards ── */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);overflow:hidden;margin-bottom:16px}
.card-head{
  padding:14px 20px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;
}
.card-title{font-size:13px;font-weight:600;color:var(--text)}
.card-body{padding:16px 20px}

/* ── Table ── */
table{width:100%;border-collapse:collapse}
th{
  font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;
  letter-spacing:.8px;padding:10px 16px;text-align:left;
  border-bottom:1px solid var(--border);background:rgba(0,0,0,.12);
}
td{padding:11px 16px;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04);color:var(--text)}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.025)}
.cell-sub{font-size:11px;color:var(--muted);margin-top:2px}

/* ── Inline form row (inside cards) ── */
.inline-form{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;padding:14px 20px;border-top:1px solid var(--border)}
.fg{display:flex;flex-direction:column;gap:5px}
.fg label{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}

/* ── Inputs ── */
input,select{
  background:var(--bg3);border:1px solid var(--border);border-radius:var(--r);
  padding:7px 11px;font-size:13px;color:var(--text);font-family:inherit;
  outline:none;transition:border-color .15s;
}
input::placeholder{color:var(--muted)}
input:focus,select:focus{border-color:var(--border-h);box-shadow:0 0 0 3px var(--accent-glow)}
.search-bar{display:flex;gap:10px;margin-bottom:14px}
.search-bar input{flex:1}

/* ── Buttons ── */
.btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:7px 14px;border-radius:var(--r);font-size:13px;font-weight:600;
  cursor:pointer;font-family:inherit;border:none;transition:all .15s;white-space:nowrap;
}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;box-shadow:0 2px 8px var(--accent-glow)}
.btn-primary:hover{opacity:.88;transform:translateY(-1px)}
.btn-ghost{background:rgba(255,255,255,.05);color:var(--text2);border:1px solid var(--border)}
.btn-ghost:hover{background:rgba(255,255,255,.09);color:var(--text)}
.btn-danger{background:var(--red-bg);color:var(--red);border:1px solid var(--red-border)}
.btn-danger:hover{background:rgba(242,63,67,.18)}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-xs{padding:3px 8px;font-size:11px}

/* ── Tags ── */
.tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px}
.tag-green{background:var(--green-bg);color:var(--green);border:1px solid var(--green-border)}
.tag-red{background:var(--red-bg);color:var(--red);border:1px solid var(--red-border)}
.tag-purple{background:rgba(124,58,237,.14);color:#c084fc;border:1px solid rgba(124,58,237,.3)}
.tag-gold{background:rgba(240,178,50,.12);color:var(--gold);border:1px solid rgba(240,178,50,.3)}

/* ── Empty state ── */
.empty{padding:40px 16px;text-align:center;color:var(--muted);font-size:13px}

/* ── Toast ── */
.toast{
  position:fixed;bottom:24px;right:24px;
  background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--r2);padding:11px 18px;font-size:13px;font-weight:500;
  z-index:9999;transform:translateY(80px);opacity:0;transition:all .25s cubic-bezier(.34,1.56,.64,1);
  display:flex;align-items:center;gap:8px;min-width:200px;
}
.toast.show{transform:translateY(0);opacity:1}
.toast.ok{border-color:var(--green-border);color:var(--green)}
.toast.err{border-color:var(--red-border);color:var(--red)}

/* ── Modal ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:500;display:none;align-items:center;justify-content:center;backdrop-filter:blur(2px)}
.modal-overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border-h);border-radius:16px;padding:28px;min-width:360px;max-width:460px;width:92%;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.modal-title{font-size:15px;font-weight:700;color:var(--text);margin-bottom:20px}
.modal .fg{margin-bottom:14px;width:100%}
.modal .fg input,.modal .fg select{width:100%}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)}

/* ── Code pill ── */
code.pill{background:rgba(124,58,237,.12);color:#c084fc;padding:2px 7px;border-radius:4px;font-size:12px;font-family:monospace}
</style>
</head>
<body>

<!-- Navbar -->
<nav>
  <div style="display:flex;align-items:center">
    <span class="logo">SYNTRIX</span>
    <span class="logo-badge">DASHBOARD</span>
  </div>
  <div class="nav-actions">
    <a href="/" class="na" target="_blank">↗ Public Site</a>
    <a href="/dashboard/logout" class="na na-danger">Sign Out</a>
  </div>
</nav>

<!-- Tab bar -->
<div class="tabbar">
  <button class="tb active" id="tab-overview" data-tab="overview" onclick="showTab('overview',this)"><span class="tb-ico">🏠</span>Overview</button>
  <button class="tb" id="tab-servers" data-tab="servers" onclick="showTab('servers',this)"><span class="tb-ico">🖥️</span>Servers</button>
  <button class="tb" id="tab-players" data-tab="players" onclick="showTab('players',this)"><span class="tb-ico">👥</span>Players</button>
  <button class="tb" id="tab-premium" data-tab="premium" onclick="showTab('premium',this)"><span class="tb-ico">⭐</span>Premium</button>
  <button class="tb" id="tab-seasons" data-tab="seasons" onclick="showTab('seasons',this)"><span class="tb-ico">🏆</span>Seasons</button>
  <button class="tb" id="tab-modes" data-tab="modes" onclick="showTab('modes',this)"><span class="tb-ico">🎮</span>Queue Modes</button>
  <button class="tb" id="tab-games" data-tab="games" onclick="showTab('games',this)"><span class="tb-ico">🗺️</span>Games</button>
  <button class="tb" id="tab-myservers" data-tab="myservers" onclick="showTab('myservers',this)" style="display:none"><span class="tb-ico">🖥️</span>My Servers</button>
  <button class="tb" id="tab-console" data-tab="console" onclick="showTab('console',this)" style="display:none"><span class="tb-ico">⌨️</span>Console</button>
</div>

<div class="main">

  <!-- OVERVIEW -->
  <div class="page active" id="page-overview">
    <h2>Overview</h2>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-val accent" id="ov-players">—</div><div class="stat-lbl">Total Players</div></div>
      <div class="stat-card"><div class="stat-val accent" id="ov-matches">—</div><div class="stat-lbl">Matches Played</div></div>
      <div class="stat-card"><div class="stat-val accent" id="ov-queue">—</div><div class="stat-lbl">In Queue</div></div>
      <div class="stat-card"><div class="stat-val accent" id="ov-servers">—</div><div class="stat-lbl">Servers</div></div>
    </div>
    <div class="card">
      <div class="card-head"><div class="card-title">Active Season</div></div>
      <div class="card-body"><div id="ov-season" style="font-size:15px;font-weight:600">None</div></div>
    </div>
  </div>

  <!-- SERVERS -->
  <div class="page" id="page-servers">
    <div class="page-head">
      <h2>Servers</h2>
      <button class="btn btn-ghost btn-sm" onclick="loadServers()">↻ Refresh</button>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Server</th><th>Members</th><th>Queue Channel</th><th>Results Channel</th><th></th></tr></thead>
        <tbody id="servers-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- PLAYERS -->
  <div class="page" id="page-players">
    <div class="page-head"><h2>Players</h2></div>
    <div class="search-bar">
      <input id="player-search" placeholder="Search by username…" oninput="searchPlayers()"/>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Player</th><th>ELO</th><th>Rank</th><th>Record</th><th>Premium</th><th></th></tr></thead>
        <tbody id="players-body"><tr><td colspan="6"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- PREMIUM -->
  <div class="page" id="page-premium">
    <div class="page-head">
      <h2>Premium Members</h2>
      <button class="btn btn-ghost btn-sm" onclick="loadPremium()">↻ Refresh</button>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Player</th><th>ELO</th><th>Activated</th><th>Source</th><th></th></tr></thead>
        <tbody id="premium-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- SEASONS -->
  <div class="page" id="page-seasons">
    <h2>Seasons</h2>
    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><div class="card-title">Start New Season</div></div>
      <div class="inline-form">
        <div class="fg"><label>Season Name</label><input id="season-name" placeholder="e.g. Season 1" style="width:220px"/></div>
        <button class="btn btn-primary" onclick="startSeason()">Start Season</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">Season History</div>
        <button class="btn btn-danger btn-sm" onclick="endSeason()">End Active Season</button>
      </div>
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Status</th><th>Started</th><th>Ended</th></tr></thead>
        <tbody id="seasons-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- GAMES -->
  <div class="page" id="page-games">
    <div class="page-head"><h2>Game Assignments</h2></div>
    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><div class="card-title">Server</div></div>
      <div class="inline-form">
        <div class="fg"><label>Select Server</label><select id="games-server-sel" onchange="loadGames(this.value)" style="width:260px"><option value="">— choose a server —</option></select></div>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px" id="games-assign-card" style="display:none">
      <div class="card-head"><div class="card-title">Assign Game to Mode</div></div>
      <div class="inline-form">
        <div class="fg"><label>Queue Mode</label><select id="ga-mode" style="width:140px"></select></div>
        <div class="fg"><label>Game</label><select id="ga-game" style="width:200px"></select></div>
        <button class="btn btn-primary" onclick="addGame()">Assign</button>
      </div>
    </div>
    <div class="card" id="games-list-card">
      <div class="card-head"><div class="card-title">Current Assignments</div><button class="btn btn-ghost btn-sm" onclick="loadGames(document.getElementById('games-server-sel').value)">↻ Refresh</button></div>
      <table>
        <thead><tr><th>Queue Mode</th><th>Game</th><th>Maps</th><th></th></tr></thead>
        <tbody id="games-body"><tr><td colspan="4"><div class="empty">Select a server above to view its game assignments.</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- MODES -->
  <div class="page" id="page-modes">
    <h2>Queue Modes</h2>
    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><div class="card-title">Add New Mode</div></div>
      <div class="inline-form">
        <div class="fg"><label>Mode ID</label><input id="mode-id" placeholder="e.g. 2v2" style="width:110px"/></div>
        <div class="fg"><label>Display Name</label><input id="mode-name" placeholder="e.g. 2v2 Teams" style="width:160px"/></div>
        <div class="fg"><label>Description</label><input id="mode-desc" placeholder="Optional" style="width:200px"/></div>
        <button class="btn btn-primary" onclick="addMode()">Add Mode</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">Active Modes</div>
        <button class="btn btn-ghost btn-sm" onclick="loadModes()">↻ Refresh</button>
      </div>
      <table>
        <thead><tr><th>Mode ID</th><th>Display Name</th><th>Description</th><th>Status</th><th></th></tr></thead>
        <tbody id="modes-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- MY SERVERS (server admins + owner) -->
  <div class="page" id="page-myservers">
    <div class="page-head">
      <div>
        <h2 id="ms-title">My Servers</h2>
        <div id="ms-breadcrumb" style="display:none;font-size:13px;color:var(--muted);margin-top:2px"></div>
      </div>
      <div style="display:flex;gap:8px">
        <button id="ms-back-btn" class="btn btn-ghost btn-sm" onclick="backToMyServers()" style="display:none">← Back</button>
        <button class="btn btn-ghost btn-sm" onclick="loadMyServers()">↻ Refresh</button>
      </div>
    </div>
    <!-- Server grid -->
    <div id="ms-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px"></div>
    <!-- Per-server management -->
    <div id="ms-mgmt" style="display:none">
      <div style="display:flex;gap:2px;margin-bottom:20px;border-bottom:1px solid var(--border)">
        <button class="tb active" data-sub="channels" onclick="showSubTab('channels',this)"><span class="tb-ico">📡</span>Channels</button>
        <button class="tb" data-sub="mssettings" onclick="showSubTab('mssettings',this)"><span class="tb-ico">⚙️</span>Settings</button>
        <button class="tb" data-sub="msgames" onclick="showSubTab('msgames',this)"><span class="tb-ico">🗺️</span>Games</button>
      </div>
      <!-- Channels -->
      <div id="sub-channels" class="sub-page">
        <div class="card"><div class="card-head"><div class="card-title">Channel Configuration</div></div>
          <div class="inline-form">
            <div class="fg"><label>Queue Channel ID</label><input id="sub-queue-ch" placeholder="Discord channel ID" style="width:210px"/></div>
            <div class="fg"><label>Results Channel ID</label><input id="sub-results-ch" placeholder="Discord channel ID" style="width:210px"/></div>
            <button class="btn btn-primary" onclick="saveSubChannels()">Save</button>
          </div>
        </div>
      </div>
      <!-- Settings -->
      <div id="sub-mssettings" class="sub-page" style="display:none">
        <div class="card"><div class="card-head"><div class="card-title">Match Settings</div></div>
          <div class="card-body" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
            <div class="fg"><label>Score Mode</label><select id="sub-score-mode" style="width:100%"><option value="0">Off (Win/Lose buttons)</option><option value="1">On (Enter final score)</option></select></div>
            <div class="fg"><label>Require Evidence</label><select id="sub-req-ev" style="width:100%"><option value="0">No</option><option value="1">Yes (screenshot URL)</option></select></div>
            <div class="fg"><label>Rounds per Match</label><input id="sub-rounds" type="number" min="1" placeholder="e.g. 3" style="width:100%"/></div>
            <div class="fg"><label>Rematch Cooldown (min)</label><input id="sub-cooldown" type="number" min="0" placeholder="0 = off" style="width:100%"/></div>
            <div class="fg"><label>Anonymous Queue</label><select id="sub-anon" style="width:100%"><option value="0">Off</option><option value="1">On</option></select></div>
          </div>
          <div style="padding:0 20px 16px"><button class="btn btn-primary" onclick="saveSubSettings()">Save Settings</button></div>
        </div>
      </div>
      <!-- Games -->
      <div id="sub-msgames" class="sub-page" style="display:none">
        <div class="card" style="margin-bottom:16px"><div class="card-head"><div class="card-title">Assign Game to Mode</div></div>
          <div class="inline-form">
            <div class="fg"><label>Queue Mode</label><select id="sub-ga-mode" style="width:140px"></select></div>
            <div class="fg"><label>Game</label><select id="sub-ga-game" style="width:200px"></select></div>
            <button class="btn btn-primary" onclick="addSubGame()">Assign</button>
          </div>
        </div>
        <div class="card"><div class="card-head"><div class="card-title">Current Assignments</div></div>
          <table><thead><tr><th>Mode</th><th>Game</th><th>Maps</th><th></th></tr></thead>
          <tbody id="sub-games-body"><tr><td colspan="4"><div class="empty">No games assigned.</div></td></tr></tbody></table>
        </div>
      </div>
    </div>
  </div>

  <!-- CONSOLE (owner only) -->
  <div class="page" id="page-console">
    <div class="page-head">
      <h2>Console</h2>
      <select id="con-guild-sel" onchange="loadConChannels(this.value)" style="width:240px;background:var(--bg3);border:1px solid var(--border);border-radius:var(--r);padding:7px 11px;font-size:13px;color:var(--text);font-family:inherit;outline:none;transition:border-color .15s">
        <option value="">Select server…</option>
      </select>
    </div>
    <div style="display:flex;gap:12px;height:calc(100vh - 210px);min-height:420px">
      <div style="width:196px;flex-shrink:0;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);overflow-y:auto;padding:8px 0">
        <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;padding:8px 14px 6px">Channels</div>
        <div id="con-channel-list"><div style="padding:8px 14px;font-size:12px;color:var(--muted)">Select a server</div></div>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);overflow:hidden">
        <div id="con-channel-label" style="padding:12px 16px;border-bottom:1px solid var(--border);font-size:13px;font-weight:600;color:var(--muted)"># select a channel</div>
        <div id="con-messages" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px">
          <div class="empty">Select a server and channel to view messages.</div>
        </div>
        <div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px">
          <input id="con-input" placeholder="Send as Syntrix…" style="flex:1" onkeydown="if(event.key==='Enter')consoleSend()"/>
          <button class="btn btn-primary" onclick="consoleSend()">Send</button>
        </div>
      </div>
    </div>
  </div>

</div>

<!-- Server Settings Modal -->
<div class="modal-overlay" id="settings-modal">
  <div class="modal" style="max-width:520px">
    <div class="modal-title">Server Settings — <span id="sm-name" style="color:var(--accent2)"></span></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="fg"><label>Score Mode</label><select id="sm-score-mode" style="width:100%"><option value="0">Off (Win/Lose buttons)</option><option value="1">On (Enter final score)</option></select></div>
      <div class="fg"><label>Require Evidence</label><select id="sm-require-evidence" style="width:100%"><option value="0">No</option><option value="1">Yes (screenshot URL)</option></select></div>
      <div class="fg"><label>Rounds per Match</label><input id="sm-rounds" type="number" min="1" max="99" placeholder="e.g. 3" style="width:100%"/></div>
      <div class="fg"><label>Rematch Cooldown (min)</label><input id="sm-cooldown" type="number" min="0" placeholder="0 = disabled" style="width:100%"/></div>
      <div class="fg"><label>Anonymous Queue</label><select id="sm-anon" style="width:100%"><option value="0">Off</option><option value="1">On (hide names until ready)</option></select></div>
      <div class="fg"><label>Server Premium</label><select id="sm-server-premium" style="width:100%"><option value="0">Off</option><option value="1">On (up to 3 games)</option></select></div>
    </div>
    <div class="fg" style="margin-top:14px"><label>Match Category ID</label><input id="sm-category" placeholder="Discord category channel ID (for auto-channels)" style="width:100%"/></div>
    <div class="fg" style="margin-top:14px"><label>Update Channel ID</label><input id="sm-update-ch" placeholder="Discord channel ID for /update broadcasts" style="width:100%"/></div>
    <input type="hidden" id="sm-server-id"/>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveServerSettings()">Save Settings</button>
    </div>
  </div>
</div>

<!-- Server Config Modal -->
<div class="modal-overlay" id="server-modal">
  <div class="modal">
    <div class="modal-title">Configure Server</div>
    <div class="fg"><label>Queue Channel ID</label><input id="m-queue-ch" placeholder="Discord channel ID"/></div>
    <div class="fg"><label>Results Channel ID</label><input id="m-results-ch" placeholder="Discord channel ID"/></div>
    <input type="hidden" id="m-server-id"/>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveServerConfig()">Save Changes</button>
    </div>
  </div>
</div>

<!-- ELO Edit Modal -->
<div class="modal-overlay" id="elo-modal">
  <div class="modal">
    <div class="modal-title">Edit ELO</div>
    <div class="fg" style="margin-bottom:8px"><label>Player</label><div id="m-player-name" style="font-size:14px;font-weight:600;color:var(--text);padding:4px 0"></div></div>
    <div class="fg"><label>New ELO</label><input id="m-elo" type="number" min="0" max="9999"/></div>
    <input type="hidden" id="m-player-id"/>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveElo()">Save</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function fmtDate(iso){if(!iso)return'—';return new Date(iso+(iso.endsWith('Z')?'':'Z')).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}
async function api(url,opts={}){
  const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});
  return r.json();
}
function toast(msg,ok=true){
  const t=document.getElementById('toast');
  t.innerHTML=(ok?'<span>✓</span>':'<span>✕</span>')+' '+esc(msg);
  t.className='toast '+(ok?'ok':'err');t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3200);
}
function showTab(name,el){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tb').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  el.classList.add('active');
  if(name==='overview')loadOverview();
  if(name==='servers')loadServers();
  if(name==='players')loadPlayers();
  if(name==='premium')loadPremium();
  if(name==='seasons')loadSeasons();
  if(name==='modes')loadModes();
  if(name==='games')initGamesTab();
  if(name==='myservers')loadMyServers();
  if(name==='console')initConsole();
}
function showSubTab(name,el){
  document.querySelectorAll('.sub-page').forEach(p=>p.style.display='none');
  document.querySelectorAll('[data-sub]').forEach(b=>b.classList.remove('active'));
  const pg=document.getElementById('sub-'+name);
  if(pg)pg.style.display='';
  el.classList.add('active');
}
function closeModal(){document.querySelectorAll('.modal-overlay').forEach(m=>m.classList.remove('open'))}
async function loadOverview(){
  const [stats,servers]=await Promise.all([api('/api/stats'),api('/api/dash/servers')]);
  document.getElementById('ov-players').textContent=stats.total_players||0;
  document.getElementById('ov-matches').textContent=stats.total_matches||0;
  document.getElementById('ov-queue').textContent=stats.queue_size||0;
  document.getElementById('ov-servers').textContent=Array.isArray(servers)?servers.length:'—';
  document.getElementById('ov-season').textContent=stats.season||'No active season';
}
async function loadServers(){
  const d=await api('/api/dash/servers');
  const tb=document.getElementById('servers-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No servers connected yet.</div></td></tr>';return}
  tb.innerHTML=d.map(s=>`<tr>
    <td><div style="font-weight:600">${esc(s.name)}</div><div class="cell-sub">${s.guild_id}</div></td>
    <td>${s.member_count.toLocaleString()}</td>
    <td><span style="font-size:12px;color:var(--muted)">${s.queue_channel_id||'—'}</span></td>
    <td><span style="font-size:12px;color:var(--muted)">${s.results_channel_id||'—'}</span></td>
    <td><div style="display:flex;gap:5px"><button class="btn btn-ghost btn-xs" onclick="openServerModal(${s.guild_id},'${esc(s.name)}',${s.queue_channel_id||"''"},${s.results_channel_id||"''"})">Channels</button><button class="btn btn-xs" style="background:rgba(124,58,237,.12);color:#c084fc;border:1px solid rgba(124,58,237,.25)" onclick="openSettingsModal(${s.guild_id},'${esc(s.name)}')">Settings</button></div></td>
  </tr>`).join('');
}
function openServerModal(id,name,qch,rch){
  document.getElementById('m-server-id').value=id;
  document.getElementById('m-queue-ch').value=qch||'';
  document.getElementById('m-results-ch').value=rch||'';
  document.getElementById('server-modal').classList.add('open');
}
async function saveServerConfig(){
  const id=document.getElementById('m-server-id').value;
  const qch=document.getElementById('m-queue-ch').value||null;
  const rch=document.getElementById('m-results-ch').value||null;
  const r=await api(`/api/dash/server/${id}/config`,{method:'POST',body:JSON.stringify({queue_channel_id:qch,results_channel_id:rch})});
  if(r.ok){toast('Server config saved');closeModal();loadServers()}else toast('Error saving config',false);
}
let playerOffset=0;
async function loadPlayers(reset=true){
  if(reset)playerOffset=0;
  const search=document.getElementById('player-search').value;
  const d=await api(`/api/dash/players?search=${encodeURIComponent(search)}&offset=${playerOffset}`);
  const tb=document.getElementById('players-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="6"><div class="empty">No players found.</div></td></tr>';return}
  tb.innerHTML=d.map(p=>`<tr>
    <td><div style="font-weight:600">${esc(p.username)}</div><div class="cell-sub">${p.discord_id}</div></td>
    <td style="font-weight:600">${p.elo}</td>
    <td style="font-size:12px;color:var(--muted)">${esc(p.rank||'')}</td>
    <td><span style="color:var(--green);font-weight:600">${p.wins}W</span><span style="color:var(--muted)"> / </span><span style="color:var(--red);font-weight:600">${p.losses}L</span></td>
    <td>${p.is_premium?'<span class="tag tag-purple">⭐ Premium</span>':'<span style="color:var(--muted);font-size:12px">—</span>'}</td>
    <td><div style="display:flex;gap:5px;flex-wrap:wrap">
      <button class="btn btn-ghost btn-xs" onclick="openEloModal(${p.discord_id},'${esc(p.username)}',${p.elo})">Edit ELO</button>
      <button class="btn btn-ghost btn-xs" onclick="resetStats(${p.discord_id},'${esc(p.username)}')" style="color:var(--muted)">Reset</button>
      ${p.is_premium
        ?`<button class="btn btn-danger btn-xs" onclick="togglePremium(${p.discord_id},false,'${esc(p.username)}')">Revoke ⭐</button>`
        :`<button class="btn btn-xs" style="background:rgba(124,58,237,.12);color:#c084fc;border:1px solid rgba(124,58,237,.25)" onclick="togglePremium(${p.discord_id},true,'${esc(p.username)}')">Grant ⭐</button>`
      }
    </div></td>
  </tr>`).join('');
}
let searchTimer;
function searchPlayers(){clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadPlayers(),300)}
function openEloModal(id,name,elo){
  document.getElementById('m-player-id').value=id;
  document.getElementById('m-player-name').textContent=name;
  document.getElementById('m-elo').value=elo;
  document.getElementById('elo-modal').classList.add('open');
}
async function saveElo(){
  const id=document.getElementById('m-player-id').value;
  const elo=parseInt(document.getElementById('m-elo').value);
  const r=await api(`/api/dash/player/${id}/elo`,{method:'POST',body:JSON.stringify({elo})});
  if(r.ok){toast('ELO updated');closeModal();loadPlayers()}else toast('Error',false);
}
async function resetStats(id,name){
  if(!confirm(`Reset all stats for ${name}?`))return;
  const r=await api(`/api/dash/player/${id}/reset`,{method:'POST'});
  if(r.ok){toast('Stats reset');loadPlayers()}else toast('Error',false);
}
async function togglePremium(id,grant,name){
  const msg=grant?`Grant premium to ${name}?`:`Revoke premium from ${name}?`;
  if(!confirm(msg))return;
  const r=await api(`/api/dash/player/${id}/premium`,{method:'POST',body:JSON.stringify({grant})});
  if(r.ok){toast(grant?'Premium granted':'Premium revoked');loadPlayers()}else toast('Error',false);
}
async function loadPremium(){
  const d=await api('/api/dash/premium_users');
  const tb=document.getElementById('premium-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No premium users yet.</div></td></tr>';return}
  tb.innerHTML=d.map(p=>`<tr>
    <td><div style="font-weight:600">${esc(p.username)}</div><div class="cell-sub">${p.discord_id}</div></td>
    <td style="font-weight:600">${p.elo}</td>
    <td style="font-size:12px;color:var(--muted)">${fmtDate(p.activated_at)}</td>
    <td><span class="tag ${p.granted_by?'tag-purple':'tag-green'}">${p.granted_by?'Admin':'Gumroad'}</span></td>
    <td><button class="btn btn-danger btn-xs" onclick="togglePremium(${p.discord_id},false,'${esc(p.username)}')">Revoke</button></td>
  </tr>`).join('');
}
async function loadSeasons(){
  const d=await api('/api/dash/seasons');
  const tb=document.getElementById('seasons-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No seasons yet.</div></td></tr>';return}
  tb.innerHTML=d.map(s=>`<tr>
    <td style="color:var(--muted);font-weight:600">#${s.season_id}</td>
    <td style="font-weight:600">${esc(s.name)}</td>
    <td>${s.active?'<span class="tag tag-green">● Active</span>':'<span class="tag tag-red">Ended</span>'}</td>
    <td style="font-size:12px;color:var(--muted)">${fmtDate(s.started_at)}</td>
    <td style="font-size:12px;color:var(--muted)">${fmtDate(s.ended_at)}</td>
  </tr>`).join('');
}
async function startSeason(){
  const name=document.getElementById('season-name').value.trim();
  if(!name)return toast('Enter a season name',false);
  const r=await api('/api/dash/season/start',{method:'POST',body:JSON.stringify({name})});
  if(r.ok){toast('Season started!');document.getElementById('season-name').value='';loadSeasons();loadOverview()}
  else toast(r.error||'Error',false);
}
async function endSeason(){
  if(!confirm('End the current season? This will archive all stats and soft-reset ELO.'))return;
  const r=await api('/api/dash/season/end',{method:'POST',body:JSON.stringify({})});
  if(r.ok){toast('Season ended and ELO reset');loadSeasons();loadOverview()}
  else toast(r.error||'Error',false);
}
async function loadModes(){
  const d=await api('/api/dash/modes');
  const tb=document.getElementById('modes-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No modes.</div></td></tr>';return}
  tb.innerHTML=d.map(m=>`<tr>
    <td><code class="pill">${esc(m.mode_id)}</code></td>
    <td style="font-weight:600">${esc(m.display_name)}</td>
    <td style="font-size:12px;color:var(--muted)">${esc(m.description||'—')}</td>
    <td><span class="tag ${m.enabled?'tag-green':'tag-red'}">${m.enabled?'Active':'Disabled'}</span></td>
    <td>${['ranked','casual'].includes(m.mode_id)?'<span style="font-size:11px;color:var(--muted)">Built-in</span>':`<button class="btn btn-danger btn-xs" onclick="deleteMode('${esc(m.mode_id)}')">Remove</button>`}</td>
  </tr>`).join('');
}
async function addMode(){
  const id=document.getElementById('mode-id').value.trim();
  const name=document.getElementById('mode-name').value.trim();
  const desc=document.getElementById('mode-desc').value.trim();
  if(!id||!name)return toast('Mode ID and name required',false);
  const r=await api('/api/dash/modes',{method:'POST',body:JSON.stringify({mode_id:id,display_name:name,description:desc})});
  if(r.ok){toast('Mode added');document.getElementById('mode-id').value='';document.getElementById('mode-name').value='';document.getElementById('mode-desc').value='';loadModes()}
  else toast(r.error||'Error',false);
}
async function deleteMode(id){
  if(!confirm(`Remove mode "${id}"?`))return;
  const r=await api(`/api/dash/modes/${id}`,{method:'DELETE'});
  if(r.ok){toast('Mode removed');loadModes()}else toast(r.error||'Error',false);
}
// ── Server Settings ──
async function openSettingsModal(id,name){
  document.getElementById('sm-server-id').value=id;
  document.getElementById('sm-name').textContent=name;
  const d=await api(`/api/dash/server/${id}/settings`);
  document.getElementById('sm-score-mode').value=d.score_mode?1:0;
  document.getElementById('sm-require-evidence').value=d.require_evidence?1:0;
  document.getElementById('sm-rounds').value=d.rounds_per_match||'';
  document.getElementById('sm-cooldown').value=d.rematch_cooldown!=null?d.rematch_cooldown:'';
  document.getElementById('sm-anon').value=d.anonymous_queue?1:0;
  document.getElementById('sm-server-premium').value=d.server_premium?1:0;
  document.getElementById('sm-category').value=d.match_category_id||'';
  document.getElementById('sm-update-ch').value=d.update_channel_id||'';
  document.getElementById('settings-modal').classList.add('open');
}
async function saveServerSettings(){
  const id=document.getElementById('sm-server-id').value;
  const payload={
    score_mode:parseInt(document.getElementById('sm-score-mode').value),
    require_evidence:parseInt(document.getElementById('sm-require-evidence').value),
    rounds_per_match:document.getElementById('sm-rounds').value||null,
    rematch_cooldown:document.getElementById('sm-cooldown').value||null,
    anonymous_queue:parseInt(document.getElementById('sm-anon').value),
    server_premium:parseInt(document.getElementById('sm-server-premium').value),
    match_category_id:document.getElementById('sm-category').value||null,
    update_channel_id:document.getElementById('sm-update-ch').value||null,
  };
  const r=await api(`/api/dash/server/${id}/settings`,{method:'POST',body:JSON.stringify(payload)});
  if(r.ok){toast('Settings saved');closeModal()}else toast('Error saving settings',false);
}

// ── Games Tab ──
let _gamesList=[];
let _modesList=[];
async function initGamesTab(){
  const servers=await api('/api/dash/servers');
  const sel=document.getElementById('games-server-sel');
  const cur=sel.value;
  sel.innerHTML='<option value="">— choose a server —</option>'+
    (Array.isArray(servers)?servers.map(s=>`<option value="${s.guild_id}">${esc(s.name)}</option>`).join(''):'');
  if(cur)sel.value=cur;
  const [games,modes]=await Promise.all([api('/api/games'),api('/api/dash/modes')]);
  _gamesList=Array.isArray(games)?games:[];
  _modesList=Array.isArray(modes)?modes.filter(m=>m.enabled):[];
  document.getElementById('ga-game').innerHTML=_gamesList.map(g=>`<option value="${g.id}">${esc(g.name)} (${g.map_count} maps)</option>`).join('');
  document.getElementById('ga-mode').innerHTML=_modesList.map(m=>`<option value="${m.mode_id}">${esc(m.display_name)}</option>`).join('');
  if(sel.value)loadGames(sel.value);
}
async function loadGames(serverId){
  const tb=document.getElementById('games-body');
  if(!serverId){tb.innerHTML='<tr><td colspan="4"><div class="empty">Select a server above.</div></td></tr>';return}
  const d=await api(`/api/dash/server/${serverId}/games`);
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="4"><div class="empty">No games assigned. Add one above.</div></td></tr>';return}
  tb.innerHTML=d.map(g=>`<tr>
    <td><code class="pill">${esc(g.queue_mode)}</code></td>
    <td style="font-weight:600">${esc(g.game_name)}</td>
    <td style="color:var(--muted);font-size:12px">${g.map_count} maps</td>
    <td><button class="btn btn-danger btn-xs" onclick="removeGame(${serverId},'${esc(g.queue_mode)}')">Remove</button></td>
  </tr>`).join('');
}
async function addGame(){
  const serverId=document.getElementById('games-server-sel').value;
  if(!serverId)return toast('Select a server first',false);
  const mode=document.getElementById('ga-mode').value;
  const game_id=document.getElementById('ga-game').value;
  const r=await api(`/api/dash/server/${serverId}/games`,{method:'POST',body:JSON.stringify({queue_mode:mode,game_id})});
  if(r.ok){toast('Game assigned');loadGames(serverId)}else toast(r.error||'Error',false);
}
async function removeGame(serverId,mode){
  if(!confirm(`Remove game assignment for mode "${mode}"?`))return;
  const r=await api(`/api/dash/server/${serverId}/games/${mode}`,{method:'DELETE'});
  if(r.ok){toast('Assignment removed');loadGames(serverId)}else toast('Error',false);
}

// ── Init (role-based) ──
let _me = null;
let _msServerId = null;
async function initDashboard(){
  _me = await api('/api/me');
  if(_me.error){window.location='/dashboard/login';return}
  if(_me.is_owner){
    document.getElementById('tab-console').style.display='';
    loadOverview();
  } else {
    // Hide all owner-only tabs, show My Servers
    ['overview','servers','players','premium','seasons','modes','games'].forEach(t=>{
      const el=document.getElementById('tab-'+t);
      if(el)el.style.display='none';
    });
    document.getElementById('tab-myservers').style.display='';
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.querySelectorAll('.tb').forEach(b=>b.classList.remove('active'));
    document.getElementById('page-myservers').classList.add('active');
    document.getElementById('tab-myservers').classList.add('active');
    loadMyServers();
  }
}

// ── My Servers ──
async function loadMyServers(){
  const grid=document.getElementById('ms-grid');
  document.getElementById('ms-mgmt').style.display='none';
  document.getElementById('ms-grid').style.display='grid';
  document.getElementById('ms-back-btn').style.display='none';
  document.getElementById('ms-breadcrumb').style.display='none';
  document.getElementById('ms-title').textContent='My Servers';
  grid.innerHTML='<div class="empty">Loading…</div>';
  const d=await api('/api/dash/myservers');
  if(!d||!d.length){grid.innerHTML='<div class="empty">No servers found. Make sure Syntrix is in your server and you have Manage Server permission.</div>';return}
  grid.innerHTML=d.map(s=>{
    const ini=s.name?s.name[0].toUpperCase():'?';
    return`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:20px;display:flex;flex-direction:column;gap:12px;transition:border-color .2s" onmouseenter="this.style.borderColor='rgba(124,58,237,.5)'" onmouseleave="this.style.borderColor='var(--border)'">
      <div style="display:flex;align-items:center;gap:12px">
        <div style="width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,#7c3aed,#a855f7);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:18px;flex-shrink:0">${esc(ini)}</div>
        <div><div style="font-weight:700;font-size:14px">${esc(s.name)}</div><div style="font-size:11px;color:var(--muted)">${(s.member_count||0).toLocaleString()} members</div></div>
      </div>
      <button class="btn btn-primary btn-sm" onclick="openMyServer(${s.guild_id},'${esc(s.name)}')">Manage →</button>
    </div>`;
  }).join('');
}
async function openMyServer(id,name){
  _msServerId=id;
  document.getElementById('ms-grid').style.display='none';
  document.getElementById('ms-mgmt').style.display='';
  document.getElementById('ms-back-btn').style.display='';
  document.getElementById('ms-title').textContent=name;
  document.getElementById('ms-breadcrumb').textContent='My Servers / '+name;
  document.getElementById('ms-breadcrumb').style.display='';
  // Reset sub-tabs to channels
  document.querySelectorAll('.sub-page').forEach(p=>p.style.display='none');
  document.querySelectorAll('[data-sub]').forEach(b=>b.classList.remove('active'));
  document.getElementById('sub-channels').style.display='';
  document.querySelector('[data-sub="channels"]').classList.add('active');
  // Load channel config
  const d=await api(`/api/dash/server/${id}/settings`);
  document.getElementById('sub-queue-ch').value=d.queue_channel_id||'';
  document.getElementById('sub-results-ch').value=d.results_channel_id||'';
  document.getElementById('sub-score-mode').value=d.score_mode?1:0;
  document.getElementById('sub-req-ev').value=d.require_evidence?1:0;
  document.getElementById('sub-rounds').value=d.rounds_per_match||'';
  document.getElementById('sub-cooldown').value=d.rematch_cooldown!=null?d.rematch_cooldown:'';
  document.getElementById('sub-anon').value=d.anonymous_queue?1:0;
  // Load games selectors
  const [games,modes]=await Promise.all([api('/api/games'),api('/api/dash/modes')]);
  const glist=Array.isArray(games)?games:[];
  const mlist=Array.isArray(modes)?modes.filter(m=>m.enabled):[];
  document.getElementById('sub-ga-game').innerHTML=glist.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('');
  document.getElementById('sub-ga-mode').innerHTML=mlist.map(m=>`<option value="${m.mode_id}">${esc(m.display_name)}</option>`).join('');
  loadSubGames();
}
function backToMyServers(){_msServerId=null;loadMyServers()}
async function saveSubChannels(){
  const id=_msServerId;
  const r=await api(`/api/dash/server/${id}/config`,{method:'POST',body:JSON.stringify({queue_channel_id:document.getElementById('sub-queue-ch').value||null,results_channel_id:document.getElementById('sub-results-ch').value||null})});
  r.ok?toast('Channels saved'):toast('Error',false);
}
async function saveSubSettings(){
  const id=_msServerId;
  const r=await api(`/api/dash/server/${id}/settings`,{method:'POST',body:JSON.stringify({
    score_mode:parseInt(document.getElementById('sub-score-mode').value),
    require_evidence:parseInt(document.getElementById('sub-req-ev').value),
    rounds_per_match:document.getElementById('sub-rounds').value||null,
    rematch_cooldown:document.getElementById('sub-cooldown').value||null,
    anonymous_queue:parseInt(document.getElementById('sub-anon').value),
  })});
  r.ok?toast('Settings saved'):toast('Error',false);
}
async function loadSubGames(){
  const id=_msServerId;if(!id)return;
  const d=await api(`/api/dash/server/${id}/games`);
  const tb=document.getElementById('sub-games-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="4"><div class="empty">No games assigned.</div></td></tr>';return}
  tb.innerHTML=d.map(g=>`<tr><td><code class="pill">${esc(g.queue_mode)}</code></td><td style="font-weight:600">${esc(g.game_name)}</td><td style="color:var(--muted);font-size:12px">${g.map_count} maps</td><td><button class="btn btn-danger btn-xs" onclick="removeSubGame('${esc(g.queue_mode)}')">Remove</button></td></tr>`).join('');
}
async function addSubGame(){
  const id=_msServerId;if(!id)return toast('No server selected',false);
  const mode=document.getElementById('sub-ga-mode').value;
  const game_id=document.getElementById('sub-ga-game').value;
  const r=await api(`/api/dash/server/${id}/games`,{method:'POST',body:JSON.stringify({queue_mode:mode,game_id})});
  r.ok?toast('Game assigned')&&loadSubGames():toast(r.error||'Error',false);
  if(r.ok)loadSubGames();
}
async function removeSubGame(mode){
  const id=_msServerId;if(!id)return;
  if(!confirm(`Remove game for mode "${mode}"?`))return;
  const r=await api(`/api/dash/server/${id}/games/${mode}`,{method:'DELETE'});
  r.ok?toast('Removed')&&loadSubGames():toast('Error',false);
  if(r.ok)loadSubGames();
}

// ── Console ──
let _conGuildId=null,_conChannelId=null,_conChannelName='';
async function initConsole(){
  const d=await api('/api/console/guilds');
  const sel=document.getElementById('con-guild-sel');
  sel.innerHTML='<option value="">Select server…</option>'+(Array.isArray(d)?d.map(g=>`<option value="${g.id}">${esc(g.name)} (${g.member_count})</option>`).join(''):'');
}
async function loadConChannels(guildId){
  _conGuildId=guildId;_conChannelId=null;
  document.getElementById('con-channel-label').textContent='# select a channel';
  document.getElementById('con-messages').innerHTML='<div class="empty">Select a channel.</div>';
  const list=document.getElementById('con-channel-list');
  if(!guildId){list.innerHTML='<div style="padding:8px 14px;font-size:12px;color:var(--muted)">Select a server</div>';return}
  const d=await api(`/api/console/guild/${guildId}/channels`);
  if(!d||!d.length){list.innerHTML='<div style="padding:8px 14px;font-size:12px;color:var(--muted)">No channels found</div>';return}
  let cat='';
  list.innerHTML=d.map(ch=>{
    let html='';
    if(ch.category&&ch.category!==cat){cat=ch.category;html+=`<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;padding:10px 14px 4px">${esc(cat)}</div>`}
    html+=`<div onclick="loadConMessages('${ch.id}','${esc(ch.name)}')" style="padding:5px 14px;font-size:13px;color:var(--text2);cursor:pointer;border-radius:var(--r);margin:0 4px;transition:all .1s" id="conch-${ch.id}" onmouseenter="this.style.background='rgba(255,255,255,.06)'" onmouseleave="this.style.background=_conChannelId===this.dataset.id?'rgba(124,58,237,.15)':''" data-id="${ch.id}"><span style="color:var(--muted);margin-right:4px">#</span>${esc(ch.name)}</div>`;
    return html;
  }).join('');
}
async function loadConMessages(channelId,channelName){
  _conChannelId=channelId;_conChannelName=channelName;
  document.getElementById('con-channel-label').textContent='# '+channelName;
  const feed=document.getElementById('con-messages');
  feed.innerHTML='<div class="empty">Loading…</div>';
  const d=await api(`/api/console/guild/${_conGuildId}/channel/${channelId}/messages?limit=50`);
  if(!d||!Array.isArray(d)||!d.length){feed.innerHTML='<div class="empty">No messages.</div>';return}
  const msgs=[...d].reverse();
  feed.innerHTML=msgs.map(m=>{
    const ini=m.author.name?m.author.name[0].toUpperCase():'?';
    const t=new Date(m.timestamp+(m.timestamp.endsWith('Z')?'':'Z')).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
    const botBadge=m.author.bot?'<span style="font-size:9px;background:#5865f2;color:#fff;padding:1px 5px;border-radius:3px;font-weight:700;margin-left:4px">BOT</span>':'';
    const aColor=m.author.bot?'#5865f2':'var(--accent)';
    const att=m.attachments.length?`<div style="font-size:11px;color:var(--muted);margin-top:2px">📎 ${m.attachments.map(a=>`<a href="${esc(a.url)}" target="_blank" style="color:var(--accent2)">${esc(a.filename)}</a>`).join(', ')}</div>`:'';
    const embeds=m.embeds?`<div style="font-size:11px;color:var(--muted);margin-top:2px">[${m.embeds} embed${m.embeds!==1?'s':''}]</div>`:'';
    return`<div style="display:flex;gap:10px;padding:2px 0">
      <div style="width:34px;height:34px;border-radius:50%;background:${aColor};display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0">${esc(ini)}</div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:baseline;gap:6px">
          <span style="font-weight:600;font-size:13px">${esc(m.author.name)}</span>${botBadge}
          <span style="font-size:11px;color:var(--muted)">${t}</span>
        </div>
        <div style="font-size:13px;color:var(--text2);margin-top:1px;word-break:break-word;white-space:pre-wrap">${esc(m.content)||'<span style="color:var(--muted);font-style:italic">no text content</span>'}</div>
        ${att}${m.embeds?embeds:''}
      </div>
    </div>`;
  }).join('');
  feed.scrollTop=feed.scrollHeight;
}
async function consoleSend(){
  const content=document.getElementById('con-input').value.trim();
  if(!content||!_conGuildId||!_conChannelId)return toast('Select a channel first',false);
  const r=await api(`/api/console/guild/${_conGuildId}/channel/${_conChannelId}/send`,{method:'POST',body:JSON.stringify({content})});
  if(r.ok){document.getElementById('con-input').value='';toast('Sent');loadConMessages(_conChannelId,_conChannelName)}
  else toast(r.error||'Error',false);
}

// Init
initDashboard();
</script>
</body></html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
