import os
import aiosqlite
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from config import BOT_INVITE_URL, DASHBOARD_SECRET, get_rank

DB_PATH = "matchmaking.db"
app = FastAPI()

AUTH_COOKIE = "sx_dash"


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


def is_authed(request: Request) -> bool:
    if not DASHBOARD_SECRET:
        return False
    return request.cookies.get(AUTH_COOKIE) == DASHBOARD_SECRET


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
    if not is_authed(request):
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


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/dashboard/login")
async def dashboard_login(response: Response, secret: str = Form(...)):
    if DASHBOARD_SECRET and secret == DASHBOARD_SECRET:
        resp = RedirectResponse("/dashboard", status_code=302)
        resp.set_cookie(AUTH_COOKIE, DASHBOARD_SECRET, httponly=True, samesite="lax")
        return resp
    return RedirectResponse("/dashboard/login?error=1", status_code=302)


@app.get("/dashboard/logout")
async def dashboard_logout():
    resp = RedirectResponse("/dashboard/login", status_code=302)
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request, error: int = 0):
    msg = "Invalid password. Try again." if error else ""
    html = LOGIN_HTML.replace("{{ERROR}}", msg).replace("{{ERROR_SHOW}}", "block" if error else "none")
    return HTMLResponse(html)


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/{path:path}", response_class=HTMLResponse)
async def dashboard(request: Request, path: str = ""):
    if not is_authed(request):
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
@media(max-width:640px){.stats{grid-template-columns:1fr}.hero{padding:60px 0 40px}th,td{padding:12px 14px}.nav-links .nl{display:none}}
</style>
</head>
<body>
<div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div>
<div class="page">
<nav><div class="wrap"><div class="nav-i">
  <div class="logo">SYNTRIX</div>
  <div class="nav-links">
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
    <div class="feat"><div class="feat-icon">🏆</div><div class="feat-title">Season System</div><div class="feat-desc">Competitive seasons with soft ELO resets and full stat history.</div></div>
    <div class="feat"><div class="feat-icon">⭐</div><div class="feat-title">Premium</div><div class="feat-desc">Wider match range, priority queue, and exclusive badges.</div></div>
    <div class="feat"><div class="feat-icon">🛡️</div><div class="feat-title">Admin Tools</div><div class="feat-desc">Full ban, ELO control, result override, and server configuration.</div></div>
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
.sub{font-size:14px;color:#64748b;margin-bottom:36px}
label{display:block;text-align:left;font-size:13px;font-weight:500;color:#94a3b8;margin-bottom:8px}
input[type=password]{width:100%;background:#13131f;border:1px solid rgba(139,92,246,.2);border-radius:10px;padding:12px 16px;font-size:14px;color:#f1f5f9;font-family:inherit;outline:none;transition:border-color .2s;margin-bottom:20px}
input[type=password]:focus{border-color:rgba(139,92,246,.5)}
button{width:100%;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;border:none;border-radius:10px;padding:13px;font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .2s;box-shadow:0 0 20px rgba(124,58,237,.3)}
button:hover{transform:translateY(-1px);box-shadow:0 0 30px rgba(124,58,237,.4)}
.err{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);border-radius:8px;padding:10px 14px;font-size:13px;color:#f87171;margin-bottom:16px}
</style>
</head>
<body>
<div class="orb o1"></div><div class="orb o2"></div>
<div class="box">
  <div class="logo">SYNTRIX</div>
  <div class="sub">Admin Dashboard</div>
  <div class="err" id="err" style="display:{{ERROR_SHOW}}">{{ERROR}}</div>
  <form method="POST" action="/dashboard/login">
    <label>Dashboard Password</label>
    <input type="password" name="secret" placeholder="Enter your secret key" autofocus/>
    <button type="submit">Sign In →</button>
  </form>
</div>
<script>
const err=document.getElementById('err');
if(err.textContent.trim()==='')err.style.display='none';
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Syntrix Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{--bg:#07070f;--card:#0e0e1c;--card2:#13131f;--border:rgba(139,92,246,.15);--border-h:rgba(139,92,246,.4);--accent:#7c3aed;--accent2:#a855f7;--text:#f1f5f9;--muted:#64748b;--sub:#94a3b8;--green:#10b981;--red:#ef4444;--r:12px}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}
nav{background:var(--card);border-bottom:1px solid var(--border);padding:0 24px;height:60px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;position:sticky;top:0;z-index:100}
.logo{font-size:18px;font-weight:900;letter-spacing:3px;background:linear-gradient(135deg,#a855f7,#7c3aed);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo span{font-size:12px;background:rgba(124,58,237,.2);border:1px solid var(--border);padding:2px 8px;border-radius:4px;-webkit-text-fill-color:#a855f7;letter-spacing:1px;margin-left:8px;font-weight:600}
.nav-right{display:flex;align-items:center;gap:8px}
.nb{color:var(--sub);text-decoration:none;font-size:13px;font-weight:500;padding:7px 14px;border-radius:8px;transition:all .2s;border:none;background:none;cursor:pointer;font-family:inherit}
.nb:hover,.nb.active{color:var(--text);background:rgba(255,255,255,.06)}
.nb.active{color:var(--accent2)}
.logout{color:#f87171!important}
.layout{display:flex;flex:1;min-height:0}
.sidebar{width:220px;background:var(--card);border-right:1px solid var(--border);padding:20px 12px;flex-shrink:0}
.sb-label{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px;padding:0 10px;margin-bottom:8px;margin-top:20px}
.sb-label:first-child{margin-top:0}
.sbtn{display:flex;align-items:center;gap:10px;width:100%;padding:9px 12px;border-radius:8px;border:none;background:none;color:var(--sub);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;transition:all .2s;text-align:left}
.sbtn:hover{background:rgba(255,255,255,.05);color:var(--text)}
.sbtn.active{background:rgba(124,58,237,.15);color:var(--accent2);border:1px solid rgba(124,58,237,.2)}
.sbtn .ico{font-size:16px;width:20px;text-align:center}
.main{flex:1;padding:28px 32px;overflow-y:auto}
h2{font-size:20px;font-weight:700;margin-bottom:20px}
.tab{display:none}.tab.active{display:block}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
.kv{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px}
.kv-val{font-size:32px;font-weight:800;background:linear-gradient(135deg,#fff,var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.kv-lbl{font-size:12px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.5px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);overflow:hidden;margin-bottom:20px}
.card-head{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.card-title{font-size:14px;font-weight:600}
table{width:100%;border-collapse:collapse}
th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;padding:12px 16px;text-align:left;border-bottom:1px solid var(--border);background:rgba(255,255,255,.02)}
td{padding:12px 16px;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04)}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px}
.tag-green{background:rgba(16,185,129,.12);color:var(--green);border:1px solid rgba(16,185,129,.25)}
.tag-red{background:rgba(239,68,68,.1);color:#f87171;border:1px solid rgba(239,68,68,.2)}
.tag-purple{background:rgba(124,58,237,.12);color:var(--accent2);border:1px solid var(--border)}
input,select{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:13px;color:var(--text);font-family:inherit;outline:none;transition:border-color .2s}
input:focus,select:focus{border-color:var(--border-h)}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;border:none;transition:all .2s}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.btn-primary:hover{opacity:.9;transform:translateY(-1px)}
.btn-ghost{background:rgba(255,255,255,.05);color:var(--sub);border:1px solid var(--border)}
.btn-ghost:hover{background:rgba(255,255,255,.08);color:var(--text)}
.btn-danger{background:rgba(239,68,68,.1);color:#f87171;border:1px solid rgba(239,68,68,.2)}
.btn-danger:hover{background:rgba(239,68,68,.2)}
.btn-sm{padding:5px 10px;font-size:12px}
.form-row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:20px}
.form-group{display:flex;flex-direction:column;gap:6px}
.form-group label{font-size:12px;color:var(--sub);font-weight:500}
.search-row{display:flex;gap:10px;margin-bottom:16px}
.search-row input{flex:1}
.empty{padding:40px;text-align:center;color:var(--muted);font-size:13px}
.toast{position:fixed;bottom:24px;right:24px;background:#1a1a2e;border:1px solid var(--border-h);border-radius:10px;padding:12px 20px;font-size:13px;font-weight:500;z-index:9999;transform:translateY(80px);opacity:0;transition:all .3s}
.toast.show{transform:translateY(0);opacity:1}
.toast.ok{border-color:rgba(16,185,129,.4);color:var(--green)}
.toast.err{border-color:rgba(239,68,68,.3);color:#f87171}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:500;display:none;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--card);border:1px solid var(--border-h);border-radius:16px;padding:28px;min-width:360px;max-width:480px;width:90%}
.modal h3{font-size:16px;font-weight:700;margin-bottom:20px}
.modal .form-group{margin-bottom:14px;display:flex;flex-direction:column;gap:6px;width:100%}
.modal label{font-size:12px;color:var(--sub);font-weight:500}
.modal input,.modal select{width:100%}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}
</style>
</head>
<body>
<nav>
  <div style="display:flex;align-items:center;gap:16px">
    <div class="logo">SYNTRIX <span>DASHBOARD</span></div>
  </div>
  <div class="nav-right">
    <a href="/" class="nb" target="_blank">↗ Public Site</a>
    <a href="/dashboard/logout" class="nb logout">Sign Out</a>
  </div>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sb-label">Overview</div>
    <button class="sbtn active" onclick="showTab('overview')"><span class="ico">🏠</span>Overview</button>
    <div class="sb-label">Management</div>
    <button class="sbtn" onclick="showTab('servers')"><span class="ico">🖥️</span>Servers</button>
    <button class="sbtn" onclick="showTab('players')"><span class="ico">👥</span>Players</button>
    <button class="sbtn" onclick="showTab('premium')"><span class="ico">⭐</span>Premium</button>
    <div class="sb-label">System</div>
    <button class="sbtn" onclick="showTab('seasons')"><span class="ico">🏆</span>Seasons</button>
    <button class="sbtn" onclick="showTab('modes')"><span class="ico">🎮</span>Queue Modes</button>
  </div>

  <div class="main">

    <!-- OVERVIEW -->
    <div class="tab active" id="tab-overview">
      <h2>Overview</h2>
      <div class="grid3">
        <div class="kv"><div class="kv-val" id="ov-players">—</div><div class="kv-lbl">Total Players</div></div>
        <div class="kv"><div class="kv-val" id="ov-matches">—</div><div class="kv-lbl">Matches Completed</div></div>
        <div class="kv"><div class="kv-val" id="ov-queue">—</div><div class="kv-lbl">In Queue</div></div>
      </div>
      <div class="grid2">
        <div class="kv"><div class="kv-val" id="ov-servers">—</div><div class="kv-lbl">Connected Servers</div></div>
        <div class="kv" id="ov-season-card"><div class="kv-val" id="ov-season" style="font-size:18px">—</div><div class="kv-lbl">Active Season</div></div>
      </div>
    </div>

    <!-- SERVERS -->
    <div class="tab" id="tab-servers">
      <h2>Servers</h2>
      <div class="card">
        <div class="card-head"><div class="card-title">Connected Servers</div><button class="btn btn-ghost btn-sm" onclick="loadServers()">↻ Refresh</button></div>
        <table>
          <thead><tr><th>Server</th><th>Members</th><th>Queue Channel ID</th><th>Results Channel ID</th><th>Actions</th></tr></thead>
          <tbody id="servers-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- PLAYERS -->
    <div class="tab" id="tab-players">
      <h2>Players</h2>
      <div class="search-row">
        <input id="player-search" placeholder="Search by username…" oninput="searchPlayers()"/>
      </div>
      <div class="card">
        <table>
          <thead><tr><th>Player</th><th>ELO</th><th>Rank</th><th>Record</th><th>Premium</th><th>Actions</th></tr></thead>
          <tbody id="players-body"><tr><td colspan="6"><div class="empty">Loading…</div></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- PREMIUM -->
    <div class="tab" id="tab-premium">
      <h2>Premium Members</h2>
      <div class="card">
        <div class="card-head"><div class="card-title">Active Premium Users</div><button class="btn btn-ghost btn-sm" onclick="loadPremium()">↻ Refresh</button></div>
        <table>
          <thead><tr><th>Player</th><th>ELO</th><th>Activated</th><th>Source</th><th>Actions</th></tr></thead>
          <tbody id="premium-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- SEASONS -->
    <div class="tab" id="tab-seasons">
      <h2>Seasons</h2>
      <div class="card" style="margin-bottom:20px">
        <div class="card-head"><div class="card-title">Start New Season</div></div>
        <div style="padding:16px 20px;display:flex;gap:12px;align-items:flex-end">
          <div class="form-group"><label>Season Name</label><input id="season-name" placeholder="e.g. Season 1"/></div>
          <button class="btn btn-primary" onclick="startSeason()">Start Season</button>
        </div>
      </div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">Season History</div>
          <button class="btn btn-danger btn-sm" onclick="endSeason()">End Active Season</button>
        </div>
        <table>
          <thead><tr><th>ID</th><th>Name</th><th>Status</th><th>Started</th><th>Ended</th></tr></thead>
          <tbody id="seasons-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- MODES -->
    <div class="tab" id="tab-modes">
      <h2>Queue Modes</h2>
      <div class="card" style="margin-bottom:20px">
        <div class="card-head"><div class="card-title">Add New Mode</div></div>
        <div style="padding:16px 20px;display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap">
          <div class="form-group"><label>Mode ID</label><input id="mode-id" placeholder="e.g. 2v2"/></div>
          <div class="form-group"><label>Display Name</label><input id="mode-name" placeholder="e.g. 2v2 Teams"/></div>
          <div class="form-group"><label>Description</label><input id="mode-desc" placeholder="Optional description"/></div>
          <button class="btn btn-primary" onclick="addMode()">Add Mode</button>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><div class="card-title">Active Modes</div><button class="btn btn-ghost btn-sm" onclick="loadModes()">↻ Refresh</button></div>
        <table>
          <thead><tr><th>Mode ID</th><th>Display Name</th><th>Description</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody id="modes-body"><tr><td colspan="5"><div class="empty">Loading…</div></td></tr></tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<!-- Server Edit Modal -->
<div class="modal-overlay" id="server-modal">
  <div class="modal">
    <h3>Configure Server</h3>
    <div class="form-group"><label>Queue Channel ID</label><input id="m-queue-ch" placeholder="Discord channel ID"/></div>
    <div class="form-group"><label>Results Channel ID</label><input id="m-results-ch" placeholder="Discord channel ID"/></div>
    <input type="hidden" id="m-server-id"/>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveServerConfig()">Save</button>
    </div>
  </div>
</div>

<!-- ELO Edit Modal -->
<div class="modal-overlay" id="elo-modal">
  <div class="modal">
    <h3>Set ELO</h3>
    <div class="form-group"><label>Player</label><div id="m-player-name" style="font-weight:600;font-size:14px;padding:4px 0"></div></div>
    <div class="form-group"><label>New ELO</label><input id="m-elo" type="number" min="0" max="9999"/></div>
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
  const t=document.getElementById('toast');t.textContent=msg;
  t.className='toast '+(ok?'ok':'err');t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}
function showTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sbtn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.currentTarget.classList.add('active');
  if(name==='overview')loadOverview();
  if(name==='servers')loadServers();
  if(name==='players')loadPlayers();
  if(name==='premium')loadPremium();
  if(name==='seasons')loadSeasons();
  if(name==='modes')loadModes();
}
function closeModal(){document.querySelectorAll('.modal-overlay').forEach(m=>m.classList.remove('open'))}
async function loadOverview(){
  const [stats,servers]=await Promise.all([api('/api/stats'),api('/api/dash/servers')]);
  document.getElementById('ov-players').textContent=stats.total_players||0;
  document.getElementById('ov-matches').textContent=stats.total_matches||0;
  document.getElementById('ov-queue').textContent=stats.queue_size||0;
  document.getElementById('ov-servers').textContent=Array.isArray(servers)?servers.length:'—';
  document.getElementById('ov-season').textContent=stats.season||'None';
}
async function loadServers(){
  const d=await api('/api/dash/servers');
  const tb=document.getElementById('servers-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No servers connected yet.</div></td></tr>';return}
  tb.innerHTML=d.map(s=>`<tr>
    <td><strong>${esc(s.name)}</strong><br><span style="font-size:11px;color:var(--muted)">${s.guild_id}</span></td>
    <td>${s.member_count.toLocaleString()}</td>
    <td><span style="font-size:12px;color:var(--sub)">${s.queue_channel_id||'—'}</span></td>
    <td><span style="font-size:12px;color:var(--sub)">${s.results_channel_id||'—'}</span></td>
    <td><button class="btn btn-ghost btn-sm" onclick="openServerModal(${s.guild_id},'${esc(s.name)}',${s.queue_channel_id||"''"},${s.results_channel_id||"''"})">Configure</button></td>
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
    <td><strong>${esc(p.username)}</strong><br><span style="font-size:11px;color:var(--muted)">${p.discord_id}</span></td>
    <td>${p.elo}</td>
    <td style="font-size:12px;color:var(--sub)">${esc(p.rank||'')}</td>
    <td style="font-size:12px"><span style="color:var(--green)">${p.wins}W</span>/<span style="color:var(--red)">${p.losses}L</span></td>
    <td>${p.is_premium?'<span class="tag tag-purple">⭐ Premium</span>':'<span style="color:var(--muted);font-size:12px">—</span>'}</td>
    <td style="display:flex;gap:6px;flex-wrap:wrap">
      <button class="btn btn-ghost btn-sm" onclick="openEloModal(${p.discord_id},'${esc(p.username)}',${p.elo})">ELO</button>
      <button class="btn btn-ghost btn-sm" onclick="resetStats(${p.discord_id},'${esc(p.username)}')" style="color:var(--muted)">Reset</button>
      ${p.is_premium
        ?`<button class="btn btn-danger btn-sm" onclick="togglePremium(${p.discord_id},false,'${esc(p.username)}')">Revoke ⭐</button>`
        :`<button class="btn btn-ghost btn-sm" style="color:var(--accent2)" onclick="togglePremium(${p.discord_id},true,'${esc(p.username)}')">Grant ⭐</button>`
      }
    </td>
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
    <td><strong>${esc(p.username)}</strong><br><span style="font-size:11px;color:var(--muted)">${p.discord_id}</span></td>
    <td>${p.elo}</td>
    <td style="font-size:12px;color:var(--sub)">${fmtDate(p.activated_at)}</td>
    <td><span class="tag ${p.granted_by?'tag-purple':'tag-green'}">${p.granted_by?'Admin':'Gumroad'}</span></td>
    <td><button class="btn btn-danger btn-sm" onclick="togglePremium(${p.discord_id},false,'${esc(p.username)}')">Revoke</button></td>
  </tr>`).join('');
}
async function loadSeasons(){
  const d=await api('/api/dash/seasons');
  const tb=document.getElementById('seasons-body');
  if(!d||!d.length){tb.innerHTML='<tr><td colspan="5"><div class="empty">No seasons yet.</div></td></tr>';return}
  tb.innerHTML=d.map(s=>`<tr>
    <td style="color:var(--muted)">#${s.season_id}</td>
    <td><strong>${esc(s.name)}</strong></td>
    <td>${s.active?'<span class="tag tag-green">🟢 Active</span>':'<span class="tag tag-red">Ended</span>'}</td>
    <td style="font-size:12px;color:var(--sub)">${fmtDate(s.started_at)}</td>
    <td style="font-size:12px;color:var(--sub)">${fmtDate(s.ended_at)}</td>
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
    <td><code style="background:rgba(124,58,237,.12);padding:2px 7px;border-radius:4px;font-size:12px">${esc(m.mode_id)}</code></td>
    <td><strong>${esc(m.display_name)}</strong></td>
    <td style="font-size:12px;color:var(--sub)">${esc(m.description||'—')}</td>
    <td><span class="tag ${m.enabled?'tag-green':'tag-red'}">${m.enabled?'Active':'Disabled'}</span></td>
    <td>${['ranked','casual'].includes(m.mode_id)?'<span style="font-size:12px;color:var(--muted)">Built-in</span>':`<button class="btn btn-danger btn-sm" onclick="deleteMode('${esc(m.mode_id)}')">Remove</button>`}</td>
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
// Init
loadOverview();
</script>
</body></html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
