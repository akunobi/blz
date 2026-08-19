# bot.py - BLZ-T Bot: Matchmaking + Blazing Lock ELO System
import discord
from discord import app_commands
from discord.ext import commands
import os
import re
import random
import threading
import asyncio
import time
import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify
from dotenv import load_dotenv
from PIL import Image
from easy_pil import Editor, Canvas, Font, LinearGradient, load_image_async

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
ADDELO_ROLE_ID = 1538589345991360527    # Only members with this role can use /addelo

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
# DATABASE (MongoDB Atlas via pymongo) — player ELO, records, and duel history for
# anti-farming checks. Connection string comes from the DATABASE_URL env var (set in
# Render), read with os.environ.get() so it works the same locally (via .env) and in
# production. This replaces the old SQLite file, which was wiped on every Render
# restart/redeploy since the free tier's filesystem is ephemeral — that's why ELO and
# duel history looked like they were "not saving."
# =====================================================================================

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set.")

# A short serverSelectionTimeoutMS means a bad URI / unreachable Atlas cluster (e.g. its
# Network Access list doesn't include Render's IPs) fails fast with a clear error instead of
# hanging for the ~30s pymongo default — which, left uncaught, looks like a silent startup
# freeze rather than the actual misconfiguration.
try:
    _mongo_client = MongoClient(DATABASE_URL, serverSelectionTimeoutMS=8000)
    _mongo_client.admin.command("ping")
except Exception as e:
    raise RuntimeError(
        f"Could not connect to MongoDB using DATABASE_URL: {e}\n"
        "Check that the connection string is correct and that your Atlas cluster's "
        "Network Access list allows connections from anywhere (0.0.0.0/0), since Render "
        "does not use a static outbound IP on standard plans."
    ) from e

# get_default_database() uses the DB name embedded in the URI (e.g. ".../blz_bot?...").
# Falls back to an explicit name if the URI doesn't include one.
try:
    db = _mongo_client.get_default_database()
except Exception:
    db = _mongo_client["blz_bot"]

players_col = db["players"]
duel_history_col = db["duel_history"]

# Helpful indexes (no-ops if they already exist)
players_col.create_index([("elo", DESCENDING)])
duel_history_col.create_index([("player_low", ASCENDING), ("player_high", ASCENDING), ("mode", ASCENDING), ("created_at", DESCENDING)])


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
    doc = players_col.find_one({"_id": user_id})
    if doc is None:
        doc = {
            "_id": user_id,
            "username": username,
            "elo": 1000,
            "ranked_wins": 0, "ranked_losses": 0, "ranked_draws": 0,
            "friendly_wins": 0, "friendly_losses": 0, "friendly_draws": 0,
        }
        players_col.insert_one(doc)
    elif doc.get("username") != username:
        players_col.update_one({"_id": user_id}, {"$set": {"username": username}})

    return PlayerRow(
        user_id, username, doc["elo"],
        doc["ranked_wins"], doc["ranked_losses"], doc["ranked_draws"],
        doc["friendly_wins"], doc["friendly_losses"], doc["friendly_draws"],
    )


async def get_or_create_player(user: discord.abc.User) -> PlayerRow:
    return await asyncio.to_thread(_get_or_create_player_sync, user.id, str(user))


def _apply_result_sync(user_id: int, elo_delta: int, mode: str, win_inc: int, loss_inc: int, draw_inc: int) -> int:
    doc = players_col.find_one({"_id": user_id}, {"elo": 1})
    current = doc["elo"] if doc else 1000
    new_elo = max(0, current + elo_delta)

    # $inc and $setOnInsert can't target the same field in one update, so only the fields
    # NOT touched by $inc for this mode go in $setOnInsert. This makes update_one() safe to
    # call even if get_or_create_player() hasn't run for this user yet (upsert=True would
    # otherwise create a document missing "username" and the other mode's counters).
    if mode == "ranked":
        inc_fields = {"ranked_wins": win_inc, "ranked_losses": loss_inc, "ranked_draws": draw_inc}
        other_defaults = {"friendly_wins": 0, "friendly_losses": 0, "friendly_draws": 0}
    else:
        inc_fields = {"friendly_wins": win_inc, "friendly_losses": loss_inc, "friendly_draws": draw_inc}
        other_defaults = {"ranked_wins": 0, "ranked_losses": 0, "ranked_draws": 0}

    players_col.update_one(
        {"_id": user_id},
        {
            "$set": {"elo": new_elo},
            "$inc": inc_fields,
            "$setOnInsert": {"username": str(user_id), **other_defaults},
        },
        upsert=True,
    )
    return new_elo


async def apply_result(user_id: int, elo_delta: int, mode: str, win_inc=0, loss_inc=0, draw_inc=0) -> int:
    return await asyncio.to_thread(_apply_result_sync, user_id, elo_delta, mode, win_inc, loss_inc, draw_inc)


def _get_ranked_record_sync(user_id: int):
    doc = players_col.find_one({"_id": user_id}, {"ranked_wins": 1, "ranked_losses": 1})
    if doc is None:
        return 0, 0
    return doc["ranked_wins"], doc["ranked_losses"]


async def get_ranked_record(user_id: int):
    return await asyncio.to_thread(_get_ranked_record_sync, user_id)


def _count_recent_ranked_duels_sync(id_a: int, id_b: int, hours: int) -> int:
    lo, hi = sorted((id_a, id_b))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return duel_history_col.count_documents({
        "player_low": lo,
        "player_high": hi,
        "mode": "ranked",
        "created_at": {"$gte": cutoff},
    })


async def count_recent_ranked_duels(id_a: int, id_b: int, hours: int = FARMING_LOOKBACK_HOURS) -> int:
    return await asyncio.to_thread(_count_recent_ranked_duels_sync, id_a, id_b, hours)


def _record_duel_sync(id_a: int, id_b: int, mode: str, counted: bool):
    lo, hi = sorted((id_a, id_b))
    duel_history_col.insert_one({
        "player_low": lo,
        "player_high": hi,
        "mode": mode,
        "counted_for_elo": counted,
        "created_at": datetime.now(timezone.utc),
    })


async def record_duel(id_a: int, id_b: int, mode: str, counted: bool):
    await asyncio.to_thread(_record_duel_sync, id_a, id_b, mode, counted)


def _get_top_players_sync(limit: int) -> list:
    docs = players_col.find().sort("elo", DESCENDING).limit(limit)
    return [
        PlayerRow(
            d["_id"], d["username"], d["elo"],
            d["ranked_wins"], d["ranked_losses"], d["ranked_draws"],
            d["friendly_wins"], d["friendly_losses"], d["friendly_draws"],
        )
        for d in docs
    ]


async def get_top_players(limit: int = 10) -> list:
    return await asyncio.to_thread(_get_top_players_sync, limit)


def _adjust_elo_sync(user_id: int, delta: int) -> int:
    doc = players_col.find_one({"_id": user_id}, {"elo": 1})
    current = doc["elo"] if doc else 1000
    new_elo = max(0, current + delta)
    players_col.update_one(
        {"_id": user_id},
        {
            "$set": {"elo": new_elo},
            "$setOnInsert": {
                "username": str(user_id),
                "ranked_wins": 0, "ranked_losses": 0, "ranked_draws": 0,
                "friendly_wins": 0, "friendly_losses": 0, "friendly_draws": 0,
            },
        },
        upsert=True,
    )
    return new_elo


async def adjust_elo(user_id: int, delta: int) -> int:
    """Directly nudges a player's ELO without touching win/loss/draw counters. Used by /addelo."""
    return await asyncio.to_thread(_adjust_elo_sync, user_id, delta)


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


TIER_BOUNDS = [
    (float('-inf'), 999, "Below Bronze"),
    (1000, 1099, "Bronze"),
    (1100, 1199, "Silver"),
    (1200, 1299, "Gold"),
    (1300, 1399, "Platinum"),
    (1400, 1499, "Diamond"),
    (1500, 1599, "Elite"),
    (1600, 1699, "Master"),
    (1700, float('inf'), "Blazing"),
]


def get_rank_progress(elo: int):
    """Returns (percent_through_tier, progress_label, rank_name) for the ELO card's bar."""
    for i, (lo, hi, name) in enumerate(TIER_BOUNDS):
        if lo <= elo <= hi:
            if name == "Blazing":
                return 100, "MAX RANK", name
            if name == "Below Bronze":
                pct = max(0, min(100, round((elo / 1000) * 100)))
                return pct, f"{1000 - elo} ELO to Bronze", name
            span = hi - lo + 1
            pct = max(0, min(100, round(((elo - lo) / span) * 100)))
            next_name = TIER_BOUNDS[i + 1][2]
            return pct, f"{hi + 1 - elo} ELO to {next_name}", name
    return 0, "", "Below Bronze"


# =====================================================================================
# VISUAL CARDS (easy-pil) — /elo and /leaderboard render PNG cards instead of plain text.
# Everything here runs fully in-memory: no files are read from or written to disk.
# =====================================================================================

def _placeholder_avatar(accent) -> Image.Image:
    """Fallback used when an avatar can't be downloaded."""
    return Image.new("RGBA", (256, 256), (*accent, 255))


def get_role_accent_color(member: discord.Member):
    """Top role color, falling back to Discord Blurple when the role has no color set."""
    if isinstance(member, discord.Member):
        role_color = member.top_role.color
        if role_color.value != 0:
            return (role_color.r, role_color.g, role_color.b)
    return (88, 101, 242)


def draw_rank_badge(size: int, rank_name: str) -> Editor:
    """Draws a small procedural icon for a rank tier — no external image assets needed."""
    ed = Editor(Canvas((size, size), color=(0, 0, 0, 0)))
    cx = cy = size / 2
    r = size * 0.42

    if rank_name == "Below Bronze":
        ed.regular_polygon((cx, cy), sides=3, radius=r, rotation=180, fill="#5b5f66", outline="#3d4046", stroke_width=3)
    elif rank_name == "Bronze":
        ed.donut((cx, cy), inner_radius=r * 0.55, outer_radius=r, fill="#cd7f32", outline="#8a531f", stroke_width=3)
    elif rank_name == "Silver":
        ed.donut((cx, cy), inner_radius=r * 0.55, outer_radius=r, fill="#c7cdd6", outline="#8a919c", stroke_width=3)
    elif rank_name == "Gold":
        ed.donut((cx, cy), inner_radius=r * 0.55, outer_radius=r, fill="#ffd447", outline="#c9962a", stroke_width=3)
    elif rank_name == "Platinum":
        ed.squircle((cx - r, cy - r), width=r * 2, height=r * 2, radius_ratio=0.35, fill="#7fe3d8", outline="#3fa89c", stroke_width=3)
    elif rank_name == "Diamond":
        ed.regular_polygon((cx, cy), sides=4, radius=r, rotation=0, fill="#63b8ff", outline="#2f7fce", stroke_width=3)
    elif rank_name == "Elite":
        ed.star((cx, cy), points=5, outer_radius=r, inner_radius=r * 0.45, fill="#c084fc", outline="#8b3fe0", stroke_width=3)
    elif rank_name == "Master":
        ed.star((cx, cy), points=6, outer_radius=r, inner_radius=r * 0.55, fill="#ffd700", outline="#a9781a", stroke_width=3)
    elif rank_name == "Blazing":
        flame_points = [
            (cx, cy - r), (cx + r * 0.55, cy - r * 0.1), (cx + r * 0.35, cy - r * 0.05),
            (cx + r * 0.65, cy + r * 0.5), (cx, cy + r), (cx - r * 0.65, cy + r * 0.5),
            (cx - r * 0.35, cy - r * 0.05), (cx - r * 0.55, cy - r * 0.1),
        ]
        ed.polygon(flame_points, fill=LinearGradient(["#ff7b00", "#ff2e2e"], direction="vertical"), outline="#7a1500")
    return ed


def _overlay_rounded_rect(base: Editor, position, width, height, radius, rgba_color):
    """Draws a translucent rounded rect that properly blends against what's already on the
    card. (Drawing translucent shapes directly on top of existing content doesn't blend in
    Pillow — it overwrites — so this renders on its own transparent layer and pastes it,
    which does composite correctly.)"""
    layer = Editor(Canvas((int(width), int(height)), color=(0, 0, 0, 0)))
    layer.rectangle((0, 0), width=width, height=height, fill=rgba_color, radius=radius)
    base.paste(layer, position)


def build_elo_card(username: str, row: "PlayerRow", accent, avatar_img: Image.Image) -> Editor:
    pct, progress_label, rank_name = get_rank_progress(row.elo)

    W, H = 1000, 400
    base = Editor(Canvas((W, H), color=(16, 17, 20, 255)))
    base.rectangle((10, 10), width=W - 20, height=H - 20, fill=(26, 27, 31, 255), outline=accent, stroke_width=4, radius=28)

    glow = Editor(Canvas((340, 340), color=(0, 0, 0, 0)))
    glow.ellipse((0, 0), 340, 340, fill=(*accent, 110))
    glow = glow.blur(45)
    base.paste(glow, (-40, -40))

    glow2 = Editor(Canvas((300, 300), color=(0, 0, 0, 0)))
    glow2.ellipse((0, 0), 300, 300, fill=(*accent, 70))
    glow2 = glow2.blur(45)
    base.paste(glow2, (W - 230, H - 230))

    base.rectangle((10, 10), width=W - 20, height=H - 20, fill=None, outline=accent, stroke_width=4, radius=28)

    avatar = Editor(avatar_img).resize((176, 176)).circle_image()
    base.paste(avatar, (48, 60))
    base.ellipse((48, 60), 176, 176, outline=accent, stroke_width=6)
    base.ellipse((48, 60), 176, 176, outline=(255, 255, 255, 60), stroke_width=1)

    kicker_font = Font.poppins(variant="bold", size=18)
    name_font = Font.poppins(variant="bold", size=42)
    label_font = Font.poppins(variant="regular", size=20)
    big_font = Font.poppins(variant="bold", size=62)
    small_font = Font.poppins(variant="regular", size=18)
    chip_label_font = Font.poppins(variant="bold", size=15)
    chip_val_font = Font.poppins(variant="regular", size=20)

    base.text((250, 55), "BLAZING LOCK ELO", font=kicker_font, color=accent)
    base.text((248, 82), username, font=name_font, color="white")

    badge = draw_rank_badge(110, rank_name)
    base.paste(badge, (W - 170, 40))
    base.text((W - 115, 155), rank_name, font=label_font, color="white", align="center", anchor="ma")

    base.text((250, 165), "ELO", font=kicker_font, color=(200, 200, 205))
    base.text((248, 185), str(row.elo), font=big_font, color="white")

    bar_y = 272
    base.rounded_bar((250, bar_y), width=560, height=22, percentage=pct, fill=(42, 44, 50), color=accent, radius=11)
    base.text((250, bar_y + 32), progress_label, font=small_font, color=(190, 190, 195))

    chip_y = 335
    _overlay_rounded_rect(base, (48, chip_y), 430, 50, 18, (255, 255, 255, 24))
    base.ellipse((70, chip_y + 20), 10, 10, fill=accent)
    base.text((90, chip_y + 11), "RANKED", font=chip_label_font, color=accent)
    base.text((90, chip_y + 29), f"{row.ranked_wins}W  ·  {row.ranked_losses}L  ·  {row.ranked_draws}D", font=chip_val_font, color="white")

    _overlay_rounded_rect(base, (500, chip_y), 430, 50, 18, (255, 255, 255, 24))
    base.ellipse((522, chip_y + 20), 10, 10, fill=(88, 101, 242))
    base.text((542, chip_y + 11), "FRIENDLY", font=chip_label_font, color=(130, 148, 255))
    base.text((542, chip_y + 29), f"{row.friendly_wins}W  ·  {row.friendly_losses}L  ·  {row.friendly_draws}D", font=chip_val_font, color="white")

    return base


async def build_elo_card_file(username: str, row: "PlayerRow", accent, avatar_img: Image.Image) -> discord.File:
    editor = await asyncio.to_thread(build_elo_card, username, row, accent, avatar_img)
    return discord.File(fp=editor.image_bytes, filename="blazing_lock_elo.png")


LEADERBOARD_MEDAL_COLORS = {1: (255, 215, 0), 2: (200, 205, 212), 3: (205, 127, 50)}
LEADERBOARD_ACCENT = (230, 57, 70)


def build_leaderboard_card(entries: list) -> Editor:
    """entries: list of dicts with rank, username, elo, rank_name, record (w,l,d), avatar_img."""
    row_h = 66
    header_h = 90
    W = 1000
    H = header_h + row_h * len(entries) + 30

    base = Editor(Canvas((W, H), color=(16, 17, 20, 255)))
    base.rectangle((10, 10), width=W - 20, height=H - 20, fill=(26, 27, 31, 255), outline=LEADERBOARD_ACCENT, stroke_width=4, radius=28)

    glow = Editor(Canvas((420, 260), color=(0, 0, 0, 0)))
    glow.ellipse((0, 0), 420, 260, fill=(*LEADERBOARD_ACCENT, 90))
    glow = glow.blur(50)
    base.paste(glow, (W // 2 - 210, -120))
    base.rectangle((10, 10), width=W - 20, height=H - 20, fill=None, outline=LEADERBOARD_ACCENT, stroke_width=4, radius=28)

    title_font = Font.poppins(variant="bold", size=32)
    sub_font = Font.poppins(variant="regular", size=16)
    base.text((W / 2, 28), "BLAZING LOCK — LEADERBOARD", font=title_font, color="white", align="center", anchor="ma")
    base.text((W / 2, 66), "Top Ranked Players", font=sub_font, color=LEADERBOARD_ACCENT, align="center", anchor="ma")

    rank_font = Font.poppins(variant="bold", size=26)
    name_font = Font.poppins(variant="bold", size=22)
    elo_font = Font.poppins(variant="bold", size=24)
    tier_font = Font.poppins(variant="regular", size=15)
    record_font = Font.poppins(variant="regular", size=15)

    y = header_h
    for e in entries:
        rank = e["rank"]
        _overlay_rounded_rect(base, (30, y + 4), W - 60, row_h - 12, 16, (255, 255, 255, 20))

        medal = LEADERBOARD_MEDAL_COLORS.get(rank)
        rank_color = medal if medal else (190, 190, 195)
        base.text((60, y + row_h / 2 - 16), f"#{rank}", font=rank_font, color=rank_color)

        avatar_img = e.get("avatar_img") or _placeholder_avatar((90, 90, 100))
        avatar = Editor(avatar_img).resize((48, 48)).circle_image()
        avatar_y = int(y + (row_h - 48) / 2)
        base.paste(avatar, (135, avatar_y))
        base.ellipse((135, avatar_y), 48, 48, outline=(medal if medal else (255, 255, 255, 40)), stroke_width=3 if medal else 1)

        base.text((200, y + 12), e["username"], font=name_font, color="white")
        w, l, d = e["record"]
        base.text((200, y + 38), f"{e['rank_name']}   ·   {w}W {l}L {d}D", font=record_font, color=(180, 180, 185))

        base.text((W - 60, y + row_h / 2 - 15), str(e["elo"]), font=elo_font, color=LEADERBOARD_ACCENT, align="right", anchor="ra")
        base.text((W - 60, y + row_h / 2 + 10), "ELO", font=tier_font, color=(150, 150, 155), align="right", anchor="ra")

        y += row_h

    return base


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

    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.gray, emoji="🚪", custom_id="blz_queue_leave")
    async def leave_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_leave_queue(interaction)


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


async def handle_leave_queue(interaction: discord.Interaction):
    user = interaction.user
    removed_from = []

    for mode in ("ranked", "friendly"):
        async with QUEUE_LOCKS[mode]:
            if user.id in QUEUES[mode]:
                QUEUES[mode].remove(user.id)
                removed_from.append(mode)

    if not removed_from:
        await interaction.response.send_message("You're not currently in a queue.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"🚪 You left the {' and '.join(removed_from)} queue.", ephemeral=True
    )
    await update_queue_panel()


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

    # Card generation + avatar download takes a moment, so acknowledge immediately.
    await interaction.response.defer()

    target = player or interaction.user
    row = await get_or_create_player(target)
    accent = get_role_accent_color(target)

    try:
        avatar_url = str(target.display_avatar.replace(size=256, format="png"))
        avatar_img = await load_image_async(avatar_url)
    except Exception as e:
        logger.error(f"!!! [ELO CARD] Avatar download failed for {target.id}: {e}")
        avatar_img = _placeholder_avatar(accent)

    try:
        file = await build_elo_card_file(target.display_name, row, accent, avatar_img)
    except Exception as e:
        logger.error(f"!!! [ELO CARD] Render failed for {target.id}: {e}")
        await interaction.followup.send("Couldn't generate the ELO card right now. Try again in a moment.")
        return

    await interaction.followup.send(file=file)


@client.tree.command(name="addelo", description="Manually adjust a player's ELO (staff only)")
@app_commands.describe(
    player="The player to adjust",
    amount="ELO to add — use a negative number to subtract",
    reason="Optional reason, included in the log"
)
async def addelo_command(interaction: discord.Interaction, player: discord.Member, amount: int, reason: str = None):
    member_roles = getattr(interaction.user, "roles", [])
    if not any(r.id == ADDELO_ROLE_ID for r in member_roles):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    await get_or_create_player(player)  # make sure a row (with username) exists first
    new_elo = await adjust_elo(player.id, amount)
    rank_name, rank_emoji = get_rank(new_elo)
    rank_display = f"{rank_emoji} {rank_name}".strip()

    embed = discord.Embed(
        title="🛠️ ELO Adjusted",
        description=f"{interaction.user.mention} adjusted {player.mention}'s ELO.",
        color=0xE63946
    )
    embed.add_field(name="Change", value=f"{'+' if amount >= 0 else ''}{amount}", inline=True)
    embed.add_field(name="New ELO", value=f"{new_elo} ({rank_display})", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)

    await interaction.response.send_message(embed=embed)

    log_line = (
        f"🛠️ **Manual ELO adjustment** — {player.mention}: "
        f"{'+' if amount >= 0 else ''}{amount} ELO (now {new_elo}) by {interaction.user.mention}."
    )
    if reason:
        log_line += f" Reason: {reason}"
    try:
        await post_result(interaction.guild, log_line)
    except Exception as e:
        logger.error(f"!!! [ADDELO LOG ERROR]: {e}")


@client.tree.command(name="leaderboard", description="Show the top 10 Blazing Lock ranked players")
async def leaderboard_command(interaction: discord.Interaction):
    await interaction.response.defer()

    top_players = await get_top_players(10)
    if not top_players:
        await interaction.followup.send("No ranked duels have been recorded yet — be the first!")
        return

    guild = interaction.guild
    entries = []
    for i, row in enumerate(top_players, start=1):
        member = guild.get_member(row.user_id) if guild else None
        display_name = member.display_name if member else row.username

        try:
            if member is not None:
                avatar_url = str(member.display_avatar.replace(size=128, format="png"))
                avatar_img = await load_image_async(avatar_url)
            else:
                avatar_img = _placeholder_avatar((90, 90, 100))
        except Exception as e:
            logger.error(f"!!! [LEADERBOARD] Avatar download failed for {row.user_id}: {e}")
            avatar_img = _placeholder_avatar((90, 90, 100))

        rank_name, _ = get_rank(row.elo)
        entries.append({
            "rank": i,
            "username": display_name,
            "elo": row.elo,
            "rank_name": rank_name,
            "record": (row.ranked_wins, row.ranked_losses, row.ranked_draws),
            "avatar_img": avatar_img,
        })

    try:
        editor = await asyncio.to_thread(build_leaderboard_card, entries)
        file = discord.File(fp=editor.image_bytes, filename="blazing_lock_leaderboard.png")
    except Exception as e:
        logger.error(f"!!! [LEADERBOARD] Render failed: {e}")
        await interaction.followup.send("Couldn't generate the leaderboard right now. Try again in a moment.")
        return

    await interaction.followup.send(file=file)


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


def _run_with_backoff():
    """Runs the bot once. If Discord's login endpoint 429s us (e.g. after Render restarted
    the process too many times in a row and burned through the session-start limit), this
    waits out the backoff and then EXITS the process (non-zero) instead of looping back to
    call client.run() again on the same object.

    Reusing one discord.py Client/Bot instance across multiple run() calls is fragile —
    sockets/HTTP sessions from the failed attempt don't reliably tear down, so a second
    in-process retry can raise a *different*, unhandled error and crash again anyway. Exiting
    lets Render restart the process with a completely fresh Client — we just make sure we've
    already waited out Discord's requested cooldown first, so that restart doesn't instantly
    re-trigger the same rate limit. A bad token still fails fast rather than retrying forever.
    """
    try:
        client.run(TOKEN)
    except discord.errors.LoginFailure:
        logger.error("!!! [DISCORD LOGIN] Invalid token — check DISCORD_TOKEN and redeploy.")
        raise
    except discord.errors.HTTPException as e:
        if e.status == 429:
            requested = None
            try:
                requested = int(float(e.response.headers.get("Retry-After", 0)))
            except Exception:
                requested = None
            # Respect what Discord actually asked for — only a floor (in case the header is
            # missing/zero) and a generous safety ceiling (in case it's a nonsense value), no
            # silent truncation. A capped-but-mislabeled wait just means we come back too soon,
            # 429 again, and repeat — logging the real number is also what makes it possible to
            # tell "this is a short IDENTIFY cooldown" apart from "this is an hours-long ban."
            backoff = max(requested or 60, 60)
            backoff = min(backoff, 3600)
            logger.error(
                f"!!! [DISCORD LOGIN] Rate limited (429). Discord asked for a {requested}s "
                f"cooldown — waiting {backoff}s, then exiting so Render restarts with a clean process..."
            )
            time.sleep(backoff)
            raise SystemExit(1)
        raise


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    _run_with_backoff()
