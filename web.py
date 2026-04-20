import os
import aiosqlite
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from config import BOT_INVITE_URL, get_rank

DB_PATH = "matchmaking.db"
app = FastAPI()


async def query(sql: str, params: tuple = ()):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/leaderboard")
async def leaderboard():
    rows = await query(
        """SELECT p.discord_id, p.username, p.elo, p.wins, p.losses,
                  CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
           FROM players p
           LEFT JOIN premium_users pr ON p.discord_id = pr.discord_id
           WHERE p.wins + p.losses > 0
           ORDER BY p.elo DESC LIMIT 20"""
    )
    for r in rows:
        r["rank"] = get_rank(r["elo"])
    return JSONResponse(rows)


@app.get("/api/queue")
async def queue():
    rows = await query(
        """SELECT q.discord_id, p.username, q.elo_at_join, q.joined_at, q.mode,
                  CASE WHEN pr.discord_id IS NOT NULL THEN 1 ELSE 0 END as is_premium
           FROM queue q
           JOIN players p ON q.discord_id = p.discord_id
           LEFT JOIN premium_users pr ON q.discord_id = pr.discord_id
           ORDER BY q.joined_at"""
    )
    return JSONResponse(rows)


@app.get("/api/matches")
async def matches():
    rows = await query(
        """SELECT m.match_id, m.status, m.created_at, m.completed_at,
                  p1.username as player1, p2.username as player2,
                  COALESCE(pw.username, '') as winner
           FROM matches m
           JOIN players p1 ON m.player1_id = p1.discord_id
           JOIN players p2 ON m.player2_id = p2.discord_id
           LEFT JOIN players pw ON m.winner_id = pw.discord_id
           ORDER BY m.created_at DESC LIMIT 20"""
    )
    return JSONResponse(rows)


@app.get("/api/stats")
async def stats():
    players = await query("SELECT COUNT(*) as c FROM players")
    matches = await query("SELECT COUNT(*) as c FROM matches WHERE status = 'completed'")
    queue = await query("SELECT COUNT(*) as c FROM queue")
    return JSONResponse({
        "total_players": players[0]["c"] if players else 0,
        "total_matches": matches[0]["c"] if matches else 0,
        "queue_size": queue[0]["c"] if queue else 0,
    })


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML.replace("{{INVITE_URL}}", BOT_INVITE_URL))


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Syntrix — Matchmaking</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
  :root {
    --bg: #07070f;
    --bg-card: #0e0e1c;
    --bg-card2: #12122200;
    --border: rgba(139,92,246,0.15);
    --border-hover: rgba(139,92,246,0.4);
    --accent: #7c3aed;
    --accent2: #a855f7;
    --accent-glow: rgba(124,58,237,0.25);
    --text: #f1f5f9;
    --text-muted: #64748b;
    --text-sub: #94a3b8;
    --gold: #f59e0b;
    --silver: #94a3b8;
    --bronze: #b45309;
    --green: #10b981;
    --red: #ef4444;
    --radius: 16px;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    font-family: 'Inter', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Background orbs */
  .orb {
    position: fixed;
    border-radius: 50%;
    filter: blur(120px);
    pointer-events: none;
    z-index: 0;
    opacity: 0.35;
  }
  .orb1 { width: 600px; height: 600px; background: #4c1d95; top: -200px; left: -200px; }
  .orb2 { width: 500px; height: 500px; background: #1e1b4b; bottom: -150px; right: -100px; }
  .orb3 { width: 300px; height: 300px; background: #6d28d9; top: 40%; left: 50%; transform: translateX(-50%); opacity: 0.15; }

  /* Layout */
  .page { position: relative; z-index: 1; }
  .container { max-width: 1100px; margin: 0 auto; padding: 0 24px; }

  /* Nav */
  nav {
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(20px);
    background: rgba(7,7,15,0.8);
    position: sticky; top: 0; z-index: 100;
  }
  .nav-inner {
    display: flex; align-items: center; justify-content: space-between;
    height: 64px;
  }
  .logo {
    font-size: 22px; font-weight: 900; letter-spacing: 3px;
    background: linear-gradient(135deg, #a855f7, #7c3aed, #6366f1);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .nav-links { display: flex; gap: 8px; align-items: center; }
  .nav-link {
    color: var(--text-sub); text-decoration: none; font-size: 14px;
    font-weight: 500; padding: 8px 14px; border-radius: 8px;
    transition: all 0.2s;
  }
  .nav-link:hover { color: var(--text); background: rgba(255,255,255,0.05); }
  .btn-invite {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #fff; text-decoration: none; font-size: 14px; font-weight: 600;
    padding: 9px 20px; border-radius: 10px;
    transition: all 0.2s; box-shadow: 0 0 20px var(--accent-glow);
  }
  .btn-invite:hover { transform: translateY(-1px); box-shadow: 0 0 30px var(--accent-glow); }

  /* Hero */
  .hero {
    text-align: center; padding: 100px 0 70px;
  }
  .hero-badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(124,58,237,0.12); border: 1px solid var(--border);
    border-radius: 100px; padding: 6px 16px; font-size: 13px;
    color: var(--accent2); font-weight: 500; margin-bottom: 28px;
  }
  .hero-badge .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.4; }
  }
  .hero h1 {
    font-size: clamp(52px, 8vw, 96px); font-weight: 900;
    letter-spacing: -2px; line-height: 1;
    background: linear-gradient(135deg, #fff 0%, #a855f7 50%, #7c3aed 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 20px;
  }
  .hero p {
    font-size: 18px; color: var(--text-sub); max-width: 480px;
    margin: 0 auto 40px; line-height: 1.6; font-weight: 400;
  }
  .hero-actions { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #fff; text-decoration: none; font-size: 15px; font-weight: 600;
    padding: 13px 28px; border-radius: 12px;
    transition: all 0.2s; box-shadow: 0 0 30px var(--accent-glow);
    border: none; cursor: pointer;
  }
  .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 0 50px var(--accent-glow); }
  .btn-secondary {
    background: rgba(255,255,255,0.05); border: 1px solid var(--border);
    color: var(--text); text-decoration: none; font-size: 15px; font-weight: 600;
    padding: 13px 28px; border-radius: 12px; transition: all 0.2s;
  }
  .btn-secondary:hover { background: rgba(255,255,255,0.08); border-color: var(--border-hover); }

  /* Stats bar */
  .stats-bar {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 16px; margin-bottom: 60px;
  }
  .stat-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 28px 24px; text-align: center;
    transition: all 0.3s;
  }
  .stat-card:hover { border-color: var(--border-hover); transform: translateY(-2px); }
  .stat-value {
    font-size: 42px; font-weight: 800; line-height: 1;
    background: linear-gradient(135deg, #fff, var(--accent2));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .stat-label { font-size: 13px; color: var(--text-muted); font-weight: 500; text-transform: uppercase; letter-spacing: 1px; }

  /* Section */
  .section { margin-bottom: 60px; }
  .section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 20px;
  }
  .section-title {
    font-size: 18px; font-weight: 700; letter-spacing: 0.5px;
    display: flex; align-items: center; gap: 10px;
  }
  .section-title .icon {
    width: 32px; height: 32px; border-radius: 8px;
    background: rgba(124,58,237,0.2); border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center; font-size: 16px;
  }
  .refresh-btn {
    background: none; border: 1px solid var(--border); color: var(--text-muted);
    font-size: 12px; padding: 6px 12px; border-radius: 8px; cursor: pointer;
    font-family: inherit; transition: all 0.2s; font-weight: 500;
  }
  .refresh-btn:hover { border-color: var(--border-hover); color: var(--text); }

  /* Card */
  .card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius); overflow: hidden;
  }

  /* Table */
  table { width: 100%; border-collapse: collapse; }
  th {
    font-size: 11px; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 1px;
    padding: 14px 20px; text-align: left;
    border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02);
  }
  td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.02); }

  /* Rank */
  .rank { font-weight: 700; font-size: 15px; width: 40px; }
  .rank-1 { color: var(--gold); }
  .rank-2 { color: var(--silver); }
  .rank-3 { color: var(--bronze); }
  .rank-other { color: var(--text-muted); }

  /* ELO badge */
  .elo-badge {
    display: inline-flex; align-items: center;
    background: rgba(124,58,237,0.12); border: 1px solid rgba(124,58,237,0.25);
    color: var(--accent2); font-weight: 700; font-size: 13px;
    padding: 3px 10px; border-radius: 6px;
  }

  /* W/L */
  .wl { font-size: 13px; }
  .wins { color: var(--green); font-weight: 600; }
  .losses { color: var(--red); font-weight: 600; }
  .sep { color: var(--text-muted); margin: 0 3px; }

  /* Status badge */
  .badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; padding: 3px 10px;
    border-radius: 100px; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .badge-active { background: rgba(16,185,129,0.12); color: var(--green); border: 1px solid rgba(16,185,129,0.25); }
  .badge-completed { background: rgba(99,102,241,0.12); color: #818cf8; border: 1px solid rgba(99,102,241,0.25); }
  .badge-cancelled { background: rgba(239,68,68,0.08); color: #f87171; border: 1px solid rgba(239,68,68,0.2); }
  .badge-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }

  /* Queue */
  .queue-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .queue-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px;
    display: flex; align-items: center; gap: 12px;
    transition: all 0.2s;
  }
  .queue-card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
  .queue-avatar {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 16px; flex-shrink: 0;
  }
  .queue-name { font-weight: 600; font-size: 14px; margin-bottom: 3px; }
  .queue-elo { font-size: 12px; color: var(--text-muted); }

  /* Empty state */
  .empty {
    text-align: center; padding: 60px 20px;
    color: var(--text-muted); font-size: 14px;
  }
  .empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.4; }

  /* Footer */
  footer {
    border-top: 1px solid var(--border); margin-top: 80px;
    padding: 40px 0; text-align: center;
    color: var(--text-muted); font-size: 13px;
  }
  .footer-logo {
    font-size: 18px; font-weight: 900; letter-spacing: 3px;
    background: linear-gradient(135deg, #a855f7, #7c3aed);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 10px;
  }

  /* Winrate bar */
  .wr-bar { height: 4px; background: rgba(255,255,255,0.06); border-radius: 2px; margin-top: 4px; overflow: hidden; }
  .wr-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent2)); border-radius: 2px; transition: width 0.6s ease; }

  /* Responsive */
  @media (max-width: 640px) {
    .stats-bar { grid-template-columns: 1fr; }
    .hero { padding: 60px 0 40px; }
    th, td { padding: 12px 14px; }
    .nav-links .nav-link { display: none; }
  }
</style>
</head>
<body>
<div class="orb orb1"></div>
<div class="orb orb2"></div>
<div class="orb orb3"></div>

<div class="page">
<nav>
  <div class="container">
    <div class="nav-inner">
      <div class="logo">SYNTRIX</div>
      <div class="nav-links">
        <a href="#leaderboard" class="nav-link">Leaderboard</a>
        <a href="#queue" class="nav-link">Queue</a>
        <a href="#matches" class="nav-link">Matches</a>
        <a href="{{INVITE_URL}}" class="btn-invite" target="_blank">+ Add to Discord</a>
      </div>
    </div>
  </div>
</nav>

<div class="container">
  <!-- Hero -->
  <div class="hero">
    <div class="hero-badge">
      <span class="dot"></span>
      Global Matchmaking — Live
    </div>
    <h1>SYNTRIX</h1>
    <p>Competitive cross-server matchmaking. Find your match, prove your rank.</p>
    <div class="hero-actions">
      <a href="{{INVITE_URL}}" class="btn-primary" target="_blank">Add to Your Server</a>
      <a href="#leaderboard" class="btn-secondary">View Leaderboard</a>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-bar">
    <div class="stat-card">
      <div class="stat-value" id="stat-players">—</div>
      <div class="stat-label">Registered Players</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="stat-matches">—</div>
      <div class="stat-label">Matches Completed</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="stat-queue">—</div>
      <div class="stat-label">In Queue Now</div>
    </div>
  </div>

  <!-- Leaderboard -->
  <div class="section" id="leaderboard">
    <div class="section-header">
      <div class="section-title">
        <div class="icon">🏆</div>
        Leaderboard
      </div>
      <button class="refresh-btn" onclick="loadAll()">↻ Refresh</button>
    </div>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Player</th>
            <th>ELO</th>
            <th>Rank</th>
            <th>Record</th>
            <th>Win Rate</th>
          </tr>
        </thead>
        <tbody id="leaderboard-body">
          <tr><td colspan="6" class="empty"><div class="empty-icon">⏳</div>Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Queue -->
  <div class="section" id="queue">
    <div class="section-header">
      <div class="section-title">
        <div class="icon">⚡</div>
        Live Queue
      </div>
      <button class="refresh-btn" onclick="loadQueue()">↻ Refresh</button>
    </div>
    <div id="queue-container">
      <div class="empty"><div class="empty-icon">⏳</div>Loading…</div>
    </div>
  </div>

  <!-- Matches -->
  <div class="section" id="matches">
    <div class="section-header">
      <div class="section-title">
        <div class="icon">⚔️</div>
        Recent Matches
      </div>
    </div>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Match</th>
            <th>Players</th>
            <th>Winner</th>
            <th>Status</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody id="matches-body">
          <tr><td colspan="5" class="empty"><div class="empty-icon">⏳</div>Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<footer>
  <div class="footer-logo">SYNTRIX</div>
  <div>Cross-server competitive matchmaking for Discord</div>
</footer>
</div>

<script>
async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    return await r.json();
  } catch { return null; }
}

function initials(name) {
  return name ? name[0].toUpperCase() : '?';
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

async function loadStats() {
  const data = await fetchJSON('/api/stats');
  if (!data) return;
  animateCount('stat-players', data.total_players);
  animateCount('stat-matches', data.total_matches);
  animateCount('stat-queue', data.queue_size);
}

function animateCount(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  let start = 0;
  const step = Math.ceil(target / 40);
  const timer = setInterval(() => {
    start = Math.min(start + step, target);
    el.textContent = start.toLocaleString();
    if (start >= target) clearInterval(timer);
  }, 30);
}

async function loadLeaderboard() {
  const data = await fetchJSON('/api/leaderboard');
  const tbody = document.getElementById('leaderboard-body');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6"><div class="empty"><div class="empty-icon">🏅</div>No ranked players yet. Start playing!</div></td></tr>';
    return;
  }
  const medals = { 1: '🥇', 2: '🥈', 3: '🥉' };
  tbody.innerHTML = data.map((p, i) => {
    const pos = i + 1;
    const total = p.wins + p.losses;
    const wr = total > 0 ? Math.round(p.wins / total * 100) : 0;
    const posClass = pos <= 3 ? `rank-${pos}` : 'rank-other';
    const posDisplay = medals[pos] || pos;
    const premiumBadge = p.is_premium ? '<span style="color:#a855f7;font-size:12px;margin-left:4px">⭐</span>' : '';
    return `<tr>
      <td><span class="rank ${posClass}">${posDisplay}</span></td>
      <td><strong>${escHtml(p.username)}</strong>${premiumBadge}</td>
      <td><span class="elo-badge">${p.elo}</span></td>
      <td style="font-size:13px;color:var(--text-sub)">${escHtml(p.rank || '')}</td>
      <td class="wl"><span class="wins">${p.wins}W</span><span class="sep">/</span><span class="losses">${p.losses}L</span></td>
      <td>
        <span style="font-size:13px;font-weight:600;color:var(--text-sub)">${wr}%</span>
        <div class="wr-bar"><div class="wr-fill" style="width:${wr}%"></div></div>
      </td>
    </tr>`;
  }).join('');
}

async function loadQueue() {
  const data = await fetchJSON('/api/queue');
  const container = document.getElementById('queue-container');
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty"><div class="empty-icon">🎯</div>Queue is empty — be the first to join with /join!</div>';
    return;
  }
  container.innerHTML = '<div class="queue-grid">' + data.map(p => {
    const star = p.is_premium ? '<span style="color:#a855f7">⭐</span> ' : '';
    const modeBadge = `<span style="font-size:10px;background:rgba(124,58,237,0.15);border:1px solid rgba(124,58,237,0.3);color:#a855f7;padding:1px 6px;border-radius:4px;margin-left:4px">${escHtml(p.mode||'ranked')}</span>`;
    return `<div class="queue-card">
      <div class="queue-avatar">${escHtml(initials(p.username))}</div>
      <div>
        <div class="queue-name">${star}${escHtml(p.username)}${modeBadge}</div>
        <div class="queue-elo">ELO ${p.elo_at_join}</div>
      </div>
    </div>`;
  }).join('') + '</div>';
}

async function loadMatches() {
  const data = await fetchJSON('/api/matches');
  const tbody = document.getElementById('matches-body');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5"><div class="empty"><div class="empty-icon">⚔️</div>No matches played yet.</div></td></tr>';
    return;
  }
  tbody.innerHTML = data.map(m => {
    const statusClass = { completed: 'badge-completed', cancelled: 'badge-cancelled', pending: 'badge-active', active: 'badge-active' }[m.status] || 'badge-active';
    return `<tr>
      <td style="color:var(--text-muted);font-weight:600">#${m.match_id}</td>
      <td><strong>${escHtml(m.player1)}</strong> <span style="color:var(--text-muted)">vs</span> <strong>${escHtml(m.player2)}</strong></td>
      <td>${m.winner ? `<span style="color:var(--accent2);font-weight:600">${escHtml(m.winner)}</span>` : '<span style="color:var(--text-muted)">—</span>'}</td>
      <td><span class="badge ${statusClass}"><span class="badge-dot"></span>${m.status}</span></td>
      <td style="color:var(--text-muted);font-size:13px">${formatDate(m.created_at)}</td>
    </tr>`;
  }).join('');
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function loadAll() {
  await Promise.all([loadStats(), loadLeaderboard(), loadQueue(), loadMatches()]);
}

loadAll();
setInterval(loadQueue, 30000);
setInterval(loadStats, 30000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
