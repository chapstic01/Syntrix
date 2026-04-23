import asyncio
import discord
from discord.ext import commands
from config import get_rank
import database as db


# ── Persistent queue panel view ───────────────────────────────────────────────

class QueuePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Choose a mode to join the queue...",
        custom_id="panel:queue_select",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label="Loading...", value="ranked")],
    )
    async def join_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        mode_id = select.values[0]
        if mode_id == "_none":
            await interaction.response.send_message("No queue modes are available right now.", ephemeral=True)
            return

        mode_data = await db.get_queue_mode(mode_id)
        if not mode_data:
            await interaction.response.send_message("That queue mode is no longer available.", ephemeral=True)
            return

        player = await db.get_or_create_player(interaction.user.id, str(interaction.user))

        sp = await db.get_server_player(interaction.user.id, interaction.guild_id) if interaction.guild_id else None
        if sp and sp["banned"]:
            await interaction.response.send_message("You are banned from matchmaking in this server.", ephemeral=True)
            return

        if await db.get_active_match_for_player(interaction.user.id):
            await interaction.response.send_message("You already have an active match.", ephemeral=True)
            return

        if await db.get_queue_entry(interaction.user.id):
            await interaction.response.send_message("You are already in the queue.", ephemeral=True)
            return

        premium = await db.is_premium(interaction.user.id)
        server_id = interaction.guild_id or 0
        await db.enqueue(interaction.user.id, server_id, player["elo"], mode=mode_id)
        interaction.client.dispatch("queue_changed")

        rank = get_rank(player["elo"])
        color = discord.Color.purple() if premium else discord.Color.green()
        prefix = "⭐ " if premium else ""
        prem_line = "**Premium:** Wider match range active ✨\n" if premium else ""
        embed = discord.Embed(
            title=f"{prefix}Joined {mode_data['display_name']} Queue",
            description=(
                f"**ELO:** {player['elo']} · **Rank:** {rank}\n"
                f"{prem_line}"
                "Press **Leave Queue** or use `/leave` to exit."
            ),
            color=color,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Leave Queue",
        style=discord.ButtonStyle.danger,
        custom_id="panel:queue_leave",
        emoji="🚪",
    )
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await db.get_queue_entry(interaction.user.id):
            await interaction.response.send_message("You are not in the queue.", ephemeral=True)
            return
        await db.dequeue(interaction.user.id)
        interaction.client.dispatch("queue_changed")
        await interaction.response.send_message("You left the queue.", ephemeral=True)


# ── Embed builders ────────────────────────────────────────────────────────────

async def _build_queue_embed(guild_id: int) -> discord.Embed:
    modes = await db.get_queue_modes()
    lines = []
    for m in modes:
        entries = await db.get_all_queue(mode=m["mode_id"])
        count = len(entries)
        dot = "🟢" if count > 0 else "⚫"
        lines.append(f"{dot} **{m['display_name']}** — {count} player{'s' if count != 1 else ''} waiting")

    embed = discord.Embed(
        title="🎮 Matchmaking Queue",
        description="\n".join(lines) if lines else "No queue modes configured yet.",
        color=discord.Color.from_str("#f59e0b"),
    )
    embed.set_footer(text="Select a mode below to join  •  Syntrix Global Matchmaking")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def _build_queue_view() -> QueuePanelView:
    modes = await db.get_queue_modes()
    view = QueuePanelView()
    if modes:
        options = []
        for m in modes:
            entries = await db.get_all_queue(mode=m["mode_id"])
            count = len(entries)
            options.append(discord.SelectOption(
                label=m["display_name"],
                value=m["mode_id"],
                description=f"{count} player{'s' if count != 1 else ''} waiting",
                emoji="🟢" if count > 0 else "⚫",
            ))
        view.join_select.options = options
    else:
        view.join_select.options = [discord.SelectOption(label="No modes available", value="_none")]
    return view


async def _build_match_log_embed() -> discord.Embed:
    matches = await db.get_recent_matches(15)
    lines = []
    for m in matches:
        status = m["status"]
        p1 = m.get("p1_name", "?")
        p2 = m.get("p2_name", "?")
        mode = m.get("mode", "ranked")
        mid = m["match_id"]
        if status == "active":
            icon = "🟡"
            detail = "In Progress"
        elif status == "completed":
            icon = "✅"
            detail = f"Won by **{m.get('winner_name') or '?'}**"
        elif status == "cancelled":
            icon = "❌"
            detail = "Cancelled"
        else:
            icon = "⏳"
            detail = "Pending"
        lines.append(f"{icon} `#{mid}` **{p1}** vs **{p2}** · `{mode}` · {detail}")

    embed = discord.Embed(
        title="📋 Match Log",
        description="\n".join(lines) if lines else "No matches yet — be the first to play!",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Updates automatically  •  Syntrix Global Matchmaking")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._refresh_task: asyncio.Task | None = None

    async def cog_load(self):
        self.bot.add_view(QueuePanelView())
        self._refresh_task = asyncio.get_event_loop().create_task(self._auto_refresh())

    async def cog_unload(self):
        if self._refresh_task:
            self._refresh_task.cancel()

    async def _auto_refresh(self):
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(30)
            try:
                await self.refresh_all_queue_panels()
                await self.refresh_all_match_logs()
            except Exception as e:
                print(f"[panel] Auto-refresh error: {e}")

    # ── Public refresh methods (called by admin commands and event listeners) ──

    async def post_panel_for_guild(self, guild: discord.Guild, channel: discord.TextChannel) -> discord.Message:
        embed = await _build_queue_embed(guild.id)
        view = await _build_queue_view()
        msg = await channel.send(embed=embed, view=view)
        await db.update_server_config(guild.id, queue_panel_msg_id=msg.id)
        return msg

    async def post_match_log_for_guild(self, guild: discord.Guild, channel: discord.TextChannel) -> discord.Message:
        embed = await _build_match_log_embed()
        msg = await channel.send(embed=embed)
        await db.update_server_config(guild.id, match_log_msg_id=msg.id)
        return msg

    async def refresh_all_queue_panels(self):
        for s in await db.get_servers_with_queue_panels():
            await self._refresh_queue_panel(s["server_id"], s["queue_channel_id"], s["queue_panel_msg_id"])

    async def refresh_all_match_logs(self):
        for s in await db.get_servers_with_match_logs():
            await self._refresh_match_log(s["server_id"], s["results_channel_id"], s["match_log_msg_id"])

    async def _refresh_queue_panel(self, guild_id: int, channel_id: int, msg_id: int):
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=await _build_queue_embed(guild_id), view=await _build_queue_view())
        except discord.NotFound:
            await db.update_server_config(guild_id, queue_panel_msg_id=None)
        except Exception as e:
            print(f"[panel] Queue panel refresh failed ({guild_id}): {e}")

    async def _refresh_match_log(self, guild_id: int, channel_id: int, msg_id: int):
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=await _build_match_log_embed())
        except discord.NotFound:
            await db.update_server_config(guild_id, match_log_msg_id=None)
        except Exception as e:
            print(f"[panel] Match log refresh failed ({guild_id}): {e}")

    # ── Event listeners ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_queue_changed(self):
        await self.refresh_all_queue_panels()

    @commands.Cog.listener()
    async def on_match_found(self, match_id: int, p1_id: int, p2_id: int, mode: str):
        await self.refresh_all_queue_panels()
        await self.refresh_all_match_logs()

    @commands.Cog.listener()
    async def on_match_state_changed(self):
        await self.refresh_all_match_logs()
