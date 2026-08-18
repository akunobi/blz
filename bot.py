# bot.py - BLZ-T Bot: Matchmaking + Blazing Lock ELO System
import discord
from discord import app_commands
from discord.ext import commands
import os
import re
import random
import threading
import asyncio
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

# --- LOGGING SETUP ---
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('blz-bot')

TOKEN = os.getenv("DISCORD_TOKEN")

# --- CORE CONFIG ---
GUILD_ID = 1538589344368164905          # Server
QUEUE_CHANNEL_ID = 1539158063116984361  # Channel where the permanent matchmaking embed lives
DUEL_CATEGORY_ID = 1539157638925918238  # Category where private duel channels are created
RESULTS_CHANNEL_ID = 1538589354790887452  # Channel where ranked/friendly results are posted
ELO_COMMAND_CHANNEL_ID = 1538589353800900626  # Only channel where /elo can be used

# --- ELO / ANTI-FARMING TUNING ---
FARMING_LOOKBACK_HOURS = 24     # Window used to detect repeated dueling between the same 2 players
LARGE_ELO_GAP_UNRANKED = 500    # ELO gap above which a ranked duel is voided (no ELO change)

# --- MINIMAL FLASK (just to keep the Render service alive) ---
app = Flask(__name__)


@app.route("/")
def health():
    return jsonify({"status": "ok", "bot": "BLZ-T Matchmaking"}), 200


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to resolve players by ID when matching / reporting

client = commands.Bot(command_prefix="!", intents=intents)
bot_ready_event = threading.Event()

# =====================================================================================
# DATABASE (SQLite) — player ELO, records, and duel history for anti-farming checks
# NOTE: on Render's free tier the filesystem is ephemeral, so this file is wiped on
# every deploy/restart unless you attach a persistent disk. See README for details.
# =====================================================================================

DB_PATH = os.path.join(os.path.dirname(__file__), "elo.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                elo INTEGER NOT NULL DEFAULT 1000,
                ranked_wins INTEGER NOT NULL DEFAULT 0,
                ranked_losses INTEGER NOT NULL DEFAULT 0,
                ranked_draws INTEGER NOT NULL DEFAULT 0,
                friendly_wins INTEGER NOT NULL DEFAULT 0,
                friendly_losses INTEGER NOT NULL DEFAULT 0,
                friendly_draws INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS duel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_low INTEGER NOT NULL,
                player_high INTEGER NOT NULL,
                mode TEXT NOT NULL,
                counted_for_elo INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


_init_db()


@dataclass
class PlayerRow:
    user_id: int
    username: str
    elo: int
    ranked_wins: int
    ranked_losses: int
    ranked_draws: int
    friendly_wins: int
    friendly_losses: int
    friendly_draws: int


def _get_or_create_player_sync(user_id: int, username: str) -> PlayerRow:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO players (user_id, username, elo) VALUES (?, ?, 1000)", (user_id, username))
            conn.commit()
            return PlayerRow(user_id, username, 1000, 0, 0, 0, 0, 0, 0)
        if row["username"] != username:
            conn.execute("UPDATE players SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
        return PlayerRow(
            row["user_id"], username, row["elo"],
            row["ranked_wins"], row["ranked_losses"], row["ranked_draws"],
            row["friendly_wins"], row["friendly_losses"], row["friendly_draws"],
        )
    finally:
        conn.close()


async def get_or_create_player(user: discord.abc.User) -> PlayerRow:
    return await asyncio.to_thread(_get_or_create_player_sync, user.id, str(user))


def _apply_result_sync(user_id: int, elo_delta: int, mode: str, win_inc: int, loss_inc: int, draw_inc: int) -> int:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT elo FROM players WHERE user_id = ?", (user_id,)).fetchone()
        current = row["elo"] if row else 1000
        new_elo = max(0, current + elo_delta)
        if mode == "ranked":
            conn.execute(
                "UPDATE players SET elo = ?, ranked_wins = ranked_wins + ?, "
                "ranked_losses = ranked_losses + ?, ranked_draws = ranked_draws + ? WHERE user_id = ?",
                (new_elo, win_inc, loss_inc, draw_inc, user_id)
            )
        else:
            conn.execute(
                "UPDATE players SET elo = ?, friendly_wins = friendly_wins + ?, "
                "friendly_losses = friendly_losses + ?, friendly_draws = friendly_draws + ? WHERE user_id = ?",
                (new_elo, win_inc, loss_inc, draw_inc, user_id)
            )
        conn.commit()
        return new_elo
    finally:
        conn.close()


async def apply_result(user_id: int, elo_delta: int, mode: str, win_inc=0, loss_inc=0, draw_inc=0) -> int:
    return await asyncio.to_thread(_apply_result_sync, user_id, elo_delta, mode, win_inc, loss_inc, draw_inc)


def _get_ranked_record_sync(user_id: int):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT ranked_wins, ranked_losses FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return 0, 0
        return row["ranked_wins"], row["ranked_losses"]
    finally:
        conn.close()


async def get_ranked_record(user_id: int):
    return await asyncio.to_thread(_get_ranked_record_sync, user_id)


def _count_recent_ranked_duels_sync(id_a: int, id_b: int, hours: int) -> int:
    lo, hi = sorted((id_a, id_b))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM duel_history "
            "WHERE player_low = ? AND player_high = ? AND mode = 'ranked' AND created_at >= ?",
            (lo, hi, cutoff)
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


async def count_recent_ranked_duels(id_a: int, id_b: int, hours: int = FARMING_LOOKBACK_HOURS) -> int:
    return await asyncio.to_thread(_count_recent_ranked_duels_sync, id_a, id_b, hours)


def _record_duel_sync(id_a: int, id_b: int, mode: str, counted: bool):
    lo, hi = sorted((id_a, id_b))
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO duel_history (player_low, player_high, mode, counted_for_elo, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lo, hi, mode, 1 if counted else 0, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()


async def record_duel(id_a: int, id_b: int, mode: str, counted: bool):
    await asyncio.to_thread(_record_duel_sync, id_a, id_b, mode, counted)


# =====================================================================================
# ELO MATH
#
# elo.txt gives *ranges* rather than a fixed formula, so within each tier the change is
# scaled linearly by how far apart the two players are (bigger gap -> value closer to the
# high end of the range). Tiers are: "similar" within 100 ELO of each other, "higher"/
# "lower" beyond that. These thresholds (100 / 400 scaling window / 500 unranked gap) are
# reasonable defaults — tune the constants above/below if you want different behavior.
# =====================================================================================

def _lerp(a: float, b: float, frac: float) -> int:
    frac = max(0.0, min(1.0, frac))
    return round(a + (b - a) * frac)


def _tier_and_frac(self_elo: int, opp_elo: int):
    diff = opp_elo - self_elo
    if diff >= 100:
        return 'higher', min(abs(diff) / 400, 1.0)
    elif diff <= -100:
        return 'lower', min(abs(diff) / 400, 1.0)
    else:
        return 'similar', min(abs(diff) / 100, 1.0)


def elo_gain_for_win(self_elo: int, opp_elo: int) -> int:
    tier, frac = _tier_and_frac(self_elo, opp_elo)
    if tier == 'higher':
        return _lerp(25, 35, frac)
    elif tier == 'lower':
        return _lerp(12, 8, frac)
    return _lerp(18, 25, frac)


def elo_loss_for_loss(self_elo: int, opp_elo: int) -> int:
    tier, frac = _tier_and_frac(self_elo, opp_elo)
    if tier == 'higher':
        return _lerp(12, 8, frac)
    elif tier == 'lower':
        return _lerp(25, 35, frac)
    return _lerp(18, 25, frac)


def elo_draw_change(self_elo: int, opp_elo: int) -> int:
    diff = opp_elo - self_elo
    frac = min(abs(diff) / 100, 1.0)
    magnitude = _lerp(0, 5, frac)
    if diff > 0:
        return magnitude
    elif diff < 0:
        return -magnitude
    return 0


def elo_type_label(self_elo: int, opp_elo: int) -> str:
    tier, _ = _tier_and_frac(self_elo, opp_elo)
    return {"higher": "Higher-rated opponent", "lower": "Lower-rated opponent", "similar": "Similar-rated opponent"}[tier]


def farming_multiplier(recent_count: int) -> float:
    if recent_count <= 0:
        return 1.0
    elif recent_count == 1:
        return 0.5
    elif recent_count == 2:
        return 0.25
    return 0.0


RANK_TIERS = [
    (1700, "Blazing", "🔥"),
    (1600, "Master", "👑"),
    (1500, "Elite", "🌟"),
    (1400, "Diamond", "💎"),
    (1300, "Platinum", "🔷"),
    (1200, "Gold", "🥇"),
    (1100, "Silver", "🥈"),
    (1000, "Bronze", "🥉"),
]


def get_rank(elo: int):
    for threshold, name, emoji in RANK_TIERS:
        if elo >= threshold:
            return name, emoji
    return "Below Bronze", ""


def _rank_display(elo: int) -> str:
    name, emoji = get_rank(elo)
    return f"{emoji} **{name}**" if emoji else f"**{name}**"


# =====================================================================================
# RESULT TEXT FORMATTING (matches the eloresult.txt template)
# =====================================================================================

CLOSING_FLAVOR = [
    "The grind begins.",
    "Keep climbing.",
    "Another step up the ladder.",
    "Momentum is building.",
    "Onwards and upwards.",
]

WIN_FLAVOR_CLOSE = [
    "A close and competitive duel down to the wire.",
    "Neck and neck the whole way — a real nail-biter.",
    "Both sides traded blows until the very end.",
]
WIN_FLAVOR_MID = [
    "A hard-fought, back-and-forth battle.",
    "A solid, well-earned victory.",
]
WIN_FLAVOR_BLOWOUT = [
    "A dominant, one-sided performance.",
    "A commanding win from start to finish.",
]


def _parse_score_margin(score: str):
    nums = re.findall(r'\d+', score)
    if len(nums) >= 2:
        return abs(int(nums[0]) - int(nums[1]))
    return None


def generate_win_summary(winner_name: str, loser_name: str, score: str, tier_label: str, first_ranked_win: bool) -> str:
    margin = _parse_score_margin(score)
    if margin is not None and margin <= 1:
        opener = random.choice(WIN_FLAVOR_CLOSE)
    elif margin is not None and margin >= 5:
        opener = random.choice(WIN_FLAVOR_BLOWOUT)
    else:
        opener = random.choice(WIN_FLAVOR_MID)

    pieces = [opener, f"**{winner_name}** defeated **{loser_name}** ({tier_label.lower()})."]
    if first_ranked_win:
        pieces.append(f"This marks **{winner_name}**'s first ranked duel win.")
    return " ".join(pieces)


def generate_draw_summary(p1_name: str, p2_name: str, score: str) -> str:
    return f"**{p1_name}** and **{p2_name}** couldn't be separated — a hard-fought draw ({score})."


def build_ranked_win_result_text(winner, loser, rounds, score, w_start, w_new, gain,
                                  l_start, l_new, loss, tier_label, status_line, summary, ranked_record):
    wins, losses = ranked_record
    lines = [
        "# 🔥 BLAZING LOCK — RANKED DUEL RESULT",
        "",
        f"**Duel:** {winner.mention} 🆚 {loser.mention}",
        f"**Rounds:** {rounds}",
        f"**Final Score:** **{score}**",
        f"**Winner:** 🏆 **{winner.display_name}**",
        "",
        "### 📊 ELO RESULT",
        "",
        f"**{winner.display_name}**",
        "",
        f"* Starting ELO: **{w_start}**",
        f"* ELO Gain: **+{gain}**",
        f"* **New ELO: {w_new}**",
        f"* Rank: {_rank_display(w_new)}",
        "",
        f"**{loser.display_name}**",
        "",
        f"* Starting ELO: **{l_start}**",
        f"* ELO Loss: **-{loss}**",
        f"* **New ELO: {l_new}**",
        f"* Rank: {_rank_display(l_new)}",
        "",
        "### ⚔️ MATCH RESULT",
        "",
        f"**Result:** **WIN — {score}**",
        f"**ELO Type:** {tier_label}",
        f"**Status:** {status_line}",
        "",
        "**Summary:**",
        summary,
        "",
        f"**{wins}–{losses} in ranked duels. {random.choice(CLOSING_FLAVOR)}** 🔥",
    ]
    return "\n".join(lines)


def build_ranked_draw_result_text(p1, p2, rounds, score, p1_start, p1_new, p1_change,
                                   p2_start, p2_new, p2_change, tier_label, status_line, summary):
    lines = [
        "# 🔥 BLAZING LOCK — RANKED DUEL RESULT",
        "",
        f"**Duel:** {p1.mention} 🆚 {p2.mention}",
        f"**Rounds:** {rounds}",
        f"**Final Score:** **{score}**",
        "**Result:** 🤝 **DRAW**",
        "",
        "### 📊 ELO RESULT",
        "",
        f"**{p1.display_name}**",
        "",
        f"* Starting ELO: **{p1_start}**",
        f"* ELO Change: **{'+' if p1_change >= 0 else ''}{p1_change}**",
        f"* **New ELO: {p1_new}**",
        f"* Rank: {_rank_display(p1_new)}",
        "",
        f"**{p2.display_name}**",
        "",
        f"* Starting ELO: **{p2_start}**",
        f"* ELO Change: **{'+' if p2_change >= 0 else ''}{p2_change}**",
        f"* **New ELO: {p2_new}**",
        f"* Rank: {_rank_display(p2_new)}",
        "",
        "### ⚔️ MATCH RESULT",
        "",
        f"**Result:** **DRAW — {score}**",
        f"**ELO Type:** {tier_label}",
        f"**Status:** {status_line}",
        "",
        "**Summary:**",
        summary,
    ]
    return "\n".join(lines)


def build_friendly_result_text(winner, loser, rounds, score, summary):
    lines = [
        "# 🤝 BLAZING LOCK — FRIENDLY DUEL RESULT",
        "",
        f"**Duel:** {winner.mention} 🆚 {loser.mention}",
        f"**Rounds:** {rounds}",
        f"**Final Score:** **{score}**",
        f"**Winner:** 🏆 **{winner.display_name}**",
        "",
        "### ⚔️ MATCH RESULT",
        "",
        f"**Result:** **WIN — {score}**",
        "**Status:** 🤝 **FRIENDLY DUEL — no ELO applied**",
        "",
        "**Summary:**",
        summary,
        "",
        "**Good game!** 🤝",
    ]
    return "\n".join(lines)


def build_friendly_draw_result_text(p1, p2, rounds, score, summary):
    lines = [
        "# 🤝 BLAZING LOCK — FRIENDLY DUEL RESULT",
        "",
        f"**Duel:** {p1.mention} 🆚 {p2.mention}",
        f"**Rounds:** {rounds}",
        f"**Final Score:** **{score}**",
        "**Result:** 🤝 **DRAW**",
        "",
        "### ⚔️ MATCH RESULT",
        "",
        f"**Result:** **DRAW — {score}**",
        "**Status:** 🤝 **FRIENDLY DUEL — no ELO applied**",
        "",
        "**Summary:**",
        summary,
        "",
        "**Good game!** 🤝",
    ]
    return "\n".join(lines)


async def post_result(guild: discord.Guild, text: str):
    channel = guild.get_channel(RESULTS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(RESULTS_CHANNEL_ID)
        except Exception as e:
            logger.error(f"!!! [RESULTS CHANNEL] {RESULTS_CHANNEL_ID} not found: {e}")
            return
    try:
        await channel.send(content=text)
    except Exception as e:
        logger.error(f"!!! [RESULTS POST ERROR]: {e}")


# =====================================================================================
# RESULT PROCESSING
# =====================================================================================

async def process_win_result(guild: discord.Guild, channel: discord.TextChannel,
                              winner: discord.Member, loser: discord.Member,
                              mode: str, rounds: str, score: str):
    winner_row = await get_or_create_player(winner)
    loser_row = await get_or_create_player(loser)
    tier_label = elo_type_label(winner_row.elo, loser_row.elo)

    if mode == "friendly":
        await apply_result(winner.id, 0, "friendly", win_inc=1)
        await apply_result(loser.id, 0, "friendly", loss_inc=1)
        await record_duel(winner.id, loser.id, "friendly", counted=False)
        summary = generate_win_summary(winner.display_name, loser.display_name, score, tier_label, first_ranked_win=False)
        text = build_friendly_result_text(winner, loser, rounds, score, summary)
        await post_result(guild, text)
        try:
            await channel.send(f"📊 Friendly result recorded: **{winner.display_name}** won {score}.")
        except Exception:
            pass
        return

    gap = abs(winner_row.elo - loser_row.elo)
    recent = await count_recent_ranked_duels(winner.id, loser.id)
    mult = farming_multiplier(recent)

    if gap > LARGE_ELO_GAP_UNRANKED:
        gain, loss = 0, 0
        status_line = f"⚠️ **UNRANKED DUEL** — ELO gap of {gap} exceeds the {LARGE_ELO_GAP_UNRANKED} limit"
    elif mult <= 0.0:
        gain, loss = 0, 0
        status_line = f"⚠️ **UNRANKED DUEL** — too many recent duels vs this opponent ({recent} in {FARMING_LOOKBACK_HOURS}h)"
    else:
        gain = round(elo_gain_for_win(winner_row.elo, loser_row.elo) * mult)
        loss = round(elo_loss_for_loss(loser_row.elo, winner_row.elo) * mult)
        if mult < 1.0:
            status_line = f"⚠️ **REDUCED RANKED DUEL** — {int(mult * 100)}% ELO (duel #{recent + 1} vs this opponent in {FARMING_LOOKBACK_HOURS}h)"
        else:
            status_line = "✅ **VERIFIED RANKED DUEL**"

    new_winner_elo = await apply_result(winner.id, gain, "ranked", win_inc=1)
    new_loser_elo = await apply_result(loser.id, -loss, "ranked", loss_inc=1)
    await record_duel(winner.id, loser.id, "ranked", counted=(gain > 0 or loss > 0))

    ranked_record = await get_ranked_record(winner.id)
    first_ranked_win = ranked_record == (1, 0)
    summary = generate_win_summary(winner.display_name, loser.display_name, score, tier_label, first_ranked_win)

    text = build_ranked_win_result_text(
        winner, loser, rounds, score,
        winner_row.elo, new_winner_elo, gain,
        loser_row.elo, new_loser_elo, loss,
        tier_label, status_line, summary, ranked_record
    )
    await post_result(guild, text)
    try:
        await channel.send(
            f"📊 Result recorded: **{winner.display_name}** won (+{gain} ELO) vs "
            f"**{loser.display_name}** (-{loss} ELO)."
        )
    except Exception:
        pass


async def process_draw_result(guild: discord.Guild, channel: discord.TextChannel,
                               p1: discord.Member, p2: discord.Member,
                               mode: str, rounds: str, score: str):
    row1 = await get_or_create_player(p1)
    row2 = await get_or_create_player(p2)
    tier_label = elo_type_label(row1.elo, row2.elo)

    if mode == "friendly":
        await apply_result(p1.id, 0, "friendly", draw_inc=1)
        await apply_result(p2.id, 0, "friendly", draw_inc=1)
        await record_duel(p1.id, p2.id, "friendly", counted=False)
        summary = generate_draw_summary(p1.display_name, p2.display_name, score)
        text = build_friendly_draw_result_text(p1, p2, rounds, score, summary)
        await post_result(guild, text)
        try:
            await channel.send(f"📊 Friendly result recorded: draw between **{p1.display_name}** and **{p2.display_name}**.")
        except Exception:
            pass
        return

    gap = abs(row1.elo - row2.elo)
    recent = await count_recent_ranked_duels(p1.id, p2.id)
    mult = farming_multiplier(recent)

    if gap > LARGE_ELO_GAP_UNRANKED:
        change1, change2 = 0, 0
        status_line = f"⚠️ **UNRANKED DUEL** — ELO gap of {gap} exceeds the {LARGE_ELO_GAP_UNRANKED} limit"
    elif mult <= 0.0:
        change1, change2 = 0, 0
        status_line = f"⚠️ **UNRANKED DUEL** — too many recent duels vs this opponent ({recent} in {FARMING_LOOKBACK_HOURS}h)"
    else:
        change1 = round(elo_draw_change(row1.elo, row2.elo) * mult)
        change2 = round(elo_draw_change(row2.elo, row1.elo) * mult)
        if mult < 1.0:
            status_line = f"⚠️ **REDUCED RANKED DUEL** — {int(mult * 100)}% ELO (duel #{recent + 1} vs this opponent in {FARMING_LOOKBACK_HOURS}h)"
        else:
            status_line = "✅ **VERIFIED RANKED DUEL**"

    new_elo1 = await apply_result(p1.id, change1, "ranked", draw_inc=1)
    new_elo2 = await apply_result(p2.id, change2, "ranked", draw_inc=1)
    await record_duel(p1.id, p2.id, "ranked", counted=(change1 != 0 or change2 != 0))

    summary = generate_draw_summary(p1.display_name, p2.display_name, score)
    text = build_ranked_draw_result_text(
        p1, p2, rounds, score,
        row1.elo, new_elo1, change1,
        row2.elo, new_elo2, change2,
        tier_label, status_line, summary
    )
    await post_result(guild, text)
    try:
        await channel.send(f"📊 Result recorded: draw between **{p1.display_name}** and **{p2.display_name}**.")
    except Exception:
        pass


# =====================================================================================
# MATCHMAKING QUEUES (Ranked + Friendly)
# =====================================================================================

QUEUES: dict[str, list[int]] = {"ranked": [], "friendly": []}
QUEUE_LOCKS: dict[str, asyncio.Lock] = {"ranked": asyncio.Lock(), "friendly": asyncio.Lock()}

matchmaking_panel_message: discord.Message | None = None

DUEL_TOPIC_RE = re.compile(r'^duel-participants:(\d+):(\d+):(ranked|friendly)(:reported)?$')


def _safe_channel_part(name: str) -> str:
    s = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')
    return s or 'player'


def build_matchmaking_embed() -> discord.Embed:
    r_count = len(QUEUES["ranked"])
    f_count = len(QUEUES["friendly"])

    def status(count):
        return f"**{count}/2** — " + ("🟡 waiting for an opponent..." if count else "⚪ queue is empty")

    embed = discord.Embed(
        title="⚔️ Matchmaking",
        description=(
            "Choose a mode below to join a queue.\n"
            "Once 2 players are queued for the same mode, a private duel channel "
            "is created automatically."
        ),
        color=0xE63946
    )
    embed.add_field(name="🏆 Ranked", value=status(r_count), inline=True)
    embed.add_field(name="🤝 Friendly", value=status(f_count), inline=True)
    embed.set_footer(text="BLZ-T · Matchmaking")
    return embed


async def update_queue_panel():
    global matchmaking_panel_message
    if matchmaking_panel_message is None:
        return
    try:
        await matchmaking_panel_message.edit(embed=build_matchmaking_embed(), view=MatchmakingView())
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL UPDATE]: {e}")


# --- Report / Confirm flow -----------------------------------------------------------

class MatchDetailsModal(discord.ui.Modal, title="Match Details"):
    rounds_input = discord.ui.TextInput(label="Rounds played", placeholder="e.g. 1", max_length=5)
    score_input = discord.ui.TextInput(label="Final score", placeholder="e.g. 6-5", max_length=20)

    def __init__(self, reporter: discord.Member, opponent: discord.Member,
                 p1: discord.Member, p2: discord.Member, winner_marker: str, mode: str):
        super().__init__()
        self.reporter = reporter
        self.opponent = opponent
        self.p1 = p1
        self.p2 = p2
        self.winner_marker = winner_marker
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        rounds_val = self.rounds_input.value.strip()
        score_val = self.score_input.value.strip()

        if self.winner_marker == "draw":
            claim_desc = "**Draw**"
        else:
            winner = self.p1 if self.winner_marker == str(self.p1.id) else self.p2
            claim_desc = f"**{winner.display_name}** won"

        await interaction.response.send_message(
            f"✅ Report submitted. Waiting for {self.opponent.mention} to confirm...", ephemeral=True
        )

        pending_embed = discord.Embed(
            title="⏳ Pending Result Confirmation",
            description=(
                f"{self.reporter.mention} reports: {claim_desc}\n\n"
                f"**Mode:** {self.mode.capitalize()}\n"
                f"**Rounds:** {rounds_val}\n"
                f"**Score:** {score_val}\n\n"
                f"{self.opponent.mention}, do you confirm this result?"
            ),
            color=0xF1C40F
        )

        view = ConfirmResultView(
            reporter_id=self.reporter.id, winner_marker=self.winner_marker,
            p1=self.p1, p2=self.p2, mode=self.mode, rounds=rounds_val, score=score_val
        )
        try:
            msg = await interaction.channel.send(
                content=self.opponent.mention, embed=pending_embed, view=view
            )
            view.message = msg
        except Exception as e:
            logger.error(f"!!! [DUEL REPORT SEND ERROR]: {e}")


class WinnerSelect(discord.ui.Select):
    def __init__(self, p1: discord.Member, p2: discord.Member, mode: str):
        options = [
            discord.SelectOption(label=f"{p1.display_name} won", value=str(p1.id), emoji="🏆"),
            discord.SelectOption(label=f"{p2.display_name} won", value=str(p2.id), emoji="🏆"),
            discord.SelectOption(label="Draw", value="draw", emoji="🤝"),
        ]
        super().__init__(placeholder="Choose the result...", options=options, min_values=1, max_values=1)
        self.p1 = p1
        self.p2 = p2
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        reporter = interaction.user
        if reporter.id not in (self.p1.id, self.p2.id):
            await interaction.response.send_message("❌ Only the two duelists can report a result.", ephemeral=True)
            return

        winner_marker = self.values[0]
        opponent = self.p2 if reporter.id == self.p1.id else self.p1
        modal = MatchDetailsModal(reporter, opponent, self.p1, self.p2, winner_marker, self.mode)
        await interaction.response.send_modal(modal)


class SelectResultView(discord.ui.View):
    def __init__(self, p1: discord.Member, p2: discord.Member, mode: str):
        super().__init__(timeout=120)
        self.add_item(WinnerSelect(p1, p2, mode))


class ConfirmResultView(discord.ui.View):
    def __init__(self, reporter_id: int, winner_marker: str, p1: discord.Member, p2: discord.Member,
                 mode: str, rounds: str, score: str):
        super().__init__(timeout=300)
        self.reporter_id = reporter_id
        self.winner_marker = winner_marker
        self.p1 = p1
        self.p2 = p2
        self.mode = mode
        self.rounds = rounds
        self.score = score
        self.message: discord.Message | None = None

    def _confirmer_id(self) -> int:
        return self.p2.id if self.reporter_id == self.p1.id else self.p1.id

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(content="⌛ This result report expired without confirmation.", view=self)
            except Exception:
                pass

    async def _mark_reported(self, channel: discord.TextChannel):
        topic = channel.topic or ''
        match = DUEL_TOPIC_RE.match(topic)
        if match:
            try:
                await channel.edit(topic=f"duel-participants:{match.group(1)}:{match.group(2)}:{match.group(3)}:reported")
            except Exception as e:
                logger.error(f"!!! [DUEL TOPIC UPDATE ERROR]: {e}")

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self._confirmer_id():
            await interaction.response.send_message("Only the other duelist can confirm this result.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        channel = interaction.channel
        guild = interaction.guild

        if self.winner_marker == "draw":
            await process_draw_result(guild, channel, self.p1, self.p2, self.mode, self.rounds, self.score)
        else:
            winner = self.p1 if self.winner_marker == str(self.p1.id) else self.p2
            loser = self.p2 if winner.id == self.p1.id else self.p1
            await process_win_result(guild, channel, winner, loser, self.mode, self.rounds, self.score)

        await self._mark_reported(channel)

    @discord.ui.button(label="Dispute", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def dispute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self._confirmer_id():
            await interaction.response.send_message("Only the other duelist can dispute this result.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⚠️ Result disputed. No ELO was applied — report again once you agree, or resolve manually.",
            embed=None, view=self
        )


class DuelControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Report Result", style=discord.ButtonStyle.success, emoji="📊", custom_id="blz_duel_report")
    async def report_result(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        topic = getattr(channel, 'topic', '') or ''
        match = DUEL_TOPIC_RE.match(topic)
        if not match:
            await interaction.response.send_message("This isn't a valid duel channel.", ephemeral=True)
            return

        p1_id, p2_id, mode, reported = int(match.group(1)), int(match.group(2)), match.group(3), match.group(4)
        if interaction.user.id not in (p1_id, p2_id):
            await interaction.response.send_message("❌ Only the two duelists can report a result.", ephemeral=True)
            return
        if reported:
            await interaction.response.send_message("This duel's result has already been reported.", ephemeral=True)
            return

        guild = interaction.guild
        p1 = guild.get_member(p1_id)
        p2 = guild.get_member(p2_id)
        if p1 is None or p2 is None:
            await interaction.response.send_message(
                "Couldn't resolve both duelists (one may have left the server).", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Who won this duel?", view=SelectResultView(p1, p2, mode), ephemeral=True
        )

    @discord.ui.button(label="Close Duel", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="blz_duel_close")
    async def close_duel(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        user = interaction.user

        topic = getattr(channel, 'topic', '') or ''
        match = DUEL_TOPIC_RE.match(topic)
        if not match:
            await interaction.response.send_message("This isn't a valid duel channel.", ephemeral=True)
            return

        p1, p2 = int(match.group(1)), int(match.group(2))
        if user.id not in (p1, p2):
            await interaction.response.send_message("❌ You don't have permission to close this duel.", ephemeral=True)
            return

        button.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        try:
            await channel.send(f"🔒 Duel closed by {user.mention}. This channel will be deleted in 5 seconds...")
        except Exception:
            pass

        logger.info(f">>> [DUEL] Closed by {user.name} ({user.id}) in #{channel.name}")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Duel closed by {user.name}")
        except Exception as e:
            logger.error(f"!!! [DUEL DELETE ERROR]: {e}")


# --- Queue join / duel creation -------------------------------------------------------

class MatchmakingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Find Ranked Duel", style=discord.ButtonStyle.primary, emoji="🏆", custom_id="blz_queue_ranked")
    async def join_ranked(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_queue_join(interaction, "ranked")

    @discord.ui.button(label="Find Friendly Duel", style=discord.ButtonStyle.secondary, emoji="🤝", custom_id="blz_queue_friendly")
    async def join_friendly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_queue_join(interaction, "friendly")


async def create_duel_channel(guild: discord.Guild, user1: discord.Member, user2: discord.Member, mode: str):
    category = guild.get_channel(DUEL_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(DUEL_CATEGORY_ID)
        except Exception as e:
            logger.error(f"!!! [DUEL] Category {DUEL_CATEGORY_ID} not found: {e}")
            return None

    base_name = f"{mode}-{_safe_channel_part(user1.name)}-{_safe_channel_part(user2.name)}"[:90]
    channel_name = base_name
    existing_names = {c.name for c in category.channels}
    suffix = 1
    while channel_name in existing_names:
        suffix += 1
        channel_name = f"{base_name}-{suffix}"[:90]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True,
            manage_channels=True, manage_messages=True, read_message_history=True,
        ),
    }
    for u in (user1, user2):
        overwrites[u] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=True, embed_links=True, attach_files=True,
        )

    try:
        channel = await guild.create_text_channel(
            name=channel_name, category=category, overwrites=overwrites,
            topic=f"duel-participants:{user1.id}:{user2.id}:{mode}",
            reason=f"{mode.capitalize()} matchmaking duel between {user1.name} and {user2.name}"
        )
    except discord.Forbidden:
        logger.error("!!! [DUEL] Missing permissions to create the duel channel")
        return None
    except Exception as e:
        logger.error(f"!!! [DUEL CREATE ERROR]: {e}")
        return None

    is_ranked = mode == "ranked"
    embed = discord.Embed(
        title="🏆 Ranked Duel" if is_ranked else "🤝 Friendly Duel",
        description=(
            f"{user1.mention} vs {user2.mention}\n\n"
            f"Good luck to both of you! When you're done, press **Report Result** "
            f"to log the outcome{' for ELO' if is_ranked else ''}, then **Close Duel**."
        ),
        color=0xE63946 if is_ranked else 0x3498DB
    )
    embed.set_footer(text="BLZ-T · Matchmaking")

    try:
        await channel.send(
            content=f"{user1.mention} {user2.mention}", embed=embed, view=DuelControlsView(),
            allowed_mentions=discord.AllowedMentions(users=True)
        )
    except Exception as e:
        logger.error(f"!!! [DUEL SEND ERROR]: {e}")

    logger.info(f">>> [DUEL] Channel created: #{channel.name} ({mode}, {user1.name} vs {user2.name})")
    return channel


async def handle_queue_join(interaction: discord.Interaction, mode: str):
    user = interaction.user
    guild = interaction.guild
    queue = QUEUES[mode]
    lock = QUEUE_LOCKS[mode]

    if guild is None:
        await interaction.response.send_message("This only works inside the server.", ephemeral=True)
        return

    already_in_queue = False
    opponent_id = None

    async with lock:
        if user.id in queue:
            already_in_queue = True
        elif queue:
            opponent_id = queue.pop(0)
        else:
            queue.append(user.id)

    if already_in_queue:
        await interaction.response.send_message(
            f"You're already in the {mode} queue. We'll notify you when we find an opponent.", ephemeral=True
        )
        return

    if opponent_id is None:
        await interaction.response.send_message(
            f"✅ You joined the {mode} queue. We'll notify you when we find an opponent.", ephemeral=True
        )
        await update_queue_panel()
        return

    opponent = guild.get_member(opponent_id)
    if opponent is None or opponent.id == user.id:
        async with lock:
            queue.append(user.id)
        await interaction.response.send_message(
            f"✅ You joined the {mode} queue. We'll notify you when we find an opponent.", ephemeral=True
        )
        await update_queue_panel()
        return

    await interaction.response.defer(ephemeral=True)
    channel = await create_duel_channel(guild, opponent, user, mode)

    if channel is None:
        async with lock:
            queue.append(opponent_id)
        await interaction.followup.send("Couldn't create the duel channel. Contact an administrator.", ephemeral=True)
        await update_queue_panel()
        return

    await interaction.followup.send(f"⚔️ Opponent found! Your duel: {channel.mention}", ephemeral=True)
    try:
        await opponent.send(f"⚔️ Opponent found! Your duel: {channel.mention}")
    except Exception:
        pass  # the user may have DMs disabled

    await update_queue_panel()


async def ensure_matchmaking_panel():
    """Publish the permanent matchmaking embed if it's not already in the channel."""
    global matchmaking_panel_message

    channel = client.get_channel(QUEUE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(QUEUE_CHANNEL_ID)
        except Exception as e:
            logger.error(f"!!! [MATCHMAKING PANEL] Channel {QUEUE_CHANNEL_ID} not found: {e}")
            return

    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == client.user.id and msg.components:
                matchmaking_panel_message = msg
                logger.info(f">>> [MATCHMAKING PANEL] Already published in #{channel.name}")
                await update_queue_panel()
                return

        matchmaking_panel_message = await channel.send(embed=build_matchmaking_embed(), view=MatchmakingView())
        logger.info(f">>> [MATCHMAKING PANEL] Published in #{channel.name}")
    except discord.Forbidden:
        logger.error(f"!!! [MATCHMAKING PANEL] Missing permissions in #{channel.name}")
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL] Error: {e}")


# =====================================================================================
# /elo COMMAND
# =====================================================================================

@client.tree.command(name="elo", description="Check a player's Blazing Lock ELO ranking")
@app_commands.describe(player="The player to check (leave empty to check yourself)")
async def elo_command(interaction: discord.Interaction, player: discord.Member = None):
    if interaction.channel_id != ELO_COMMAND_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ELO_COMMAND_CHANNEL_ID}>.", ephemeral=True
        )
        return

    target = player or interaction.user
    row = await get_or_create_player(target)
    rank_name, rank_emoji = get_rank(row.elo)
    rank_display = f"{rank_emoji} {rank_name}".strip()

    embed = discord.Embed(title=f"📊 {target.display_name}'s Blazing Lock ELO", color=0xE63946)
    embed.add_field(name="ELO", value=str(row.elo), inline=True)
    embed.add_field(name="Rank", value=rank_display, inline=True)
    embed.add_field(name="Ranked Record", value=f"{row.ranked_wins}W / {row.ranked_losses}L / {row.ranked_draws}D", inline=False)
    embed.add_field(name="Friendly Record", value=f"{row.friendly_wins}W / {row.friendly_losses}L / {row.friendly_draws}D", inline=False)
    if isinstance(target, discord.Member) and target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    await interaction.response.send_message(embed=embed)


# =====================================================================================
# BOT LIFECYCLE
# =====================================================================================

@client.event
async def on_ready():
    logger.info(f">>> [DISCORD]: Logged in as {client.user}")
    bot_ready_event.set()

    # Register persistent views (buttons survive restarts)
    try:
        client.add_view(MatchmakingView())
        client.add_view(DuelControlsView())
        logger.info(">>> [MATCHMAKING] Persistent views registered")
    except Exception as e:
        logger.error(f"!!! [VIEW REGISTER]: {e}")

    # Sync slash commands to the guild directly (near-instant, vs up to 1h for global sync).
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        client.tree.copy_global_to(guild=guild_obj)
        synced = await client.tree.sync(guild=guild_obj)
        logger.info(f">>> [SLASH] Synced {len(synced)} command(s) to guild {GUILD_ID}")
    except Exception as e:
        logger.error(f"!!! [SLASH SYNC ERROR]: {e}")

    # Publish (or refresh) the matchmaking panel
    try:
        await ensure_matchmaking_panel()
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL ON READY]: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    client.run(TOKEN)
