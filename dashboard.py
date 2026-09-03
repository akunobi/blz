"""
dashboard.py — Web dashboard for the BLZ-T Discord bot.

This is a Flask Blueprint that plugs into the Flask app bot.py ALREADY runs
(the tiny one it uses for Render's health check), so everything still runs
as one process on one port. Slash commands in bot.py are untouched — this
just gives people a second way to do (almost) all of the same things from
a browser instead of typing commands.

WHAT IT DOES
------------
1. A public landing page at "/" — server info (FAQ, XP/levels, roles) with
   a "Log in with Discord" button. This is what visitors see automatically;
   nobody needs to know the "/dashboard" URL exists.
2. Discord login (OAuth2) instead of a bot invite / server nickname. Any
   Discord account can log in and use the dashboard right away — there is
   no manual "approve this person" step anymore.
3. The 3 admin Discord accounts below (ADMIN_DISCORD_IDS) — plus anyone
   they promote — can grant or revoke "admin" status for other logged-in
   members, from Admin -> Manage Admins. This only controls access to the
   admin-only pages (moderation DMs stay role-gated exactly as before); it
   no longer gates basic dashboard login the way the old approval queue did.
4. Web pages standing in for the slash commands:

     /elo             -> /elo, /leaderboard
     /elo/<id>        -> /elo <player>, /addelo (staff)
     /elo/settings    -> /setelocolor, /resetelocolor, /setelobanner, /resetelobanner
     /economy         -> /balance, /daily, /weekly, /work, /inventory, /sell, /use, /pay
     /economy/shop    -> /shop, /buy
     /economy/leaderboard -> /baltop
     /economy/games   -> /rps, /coinflip, /slots, /guess
     /tryouts         -> /viewt, /ep (self)
     /tryouts/ep/<id> -> /ep <player> (staff)
     /tryouts/exclude -> the /viewtpanel exclude/include panel (staff)
     /tryouts/in      -> /in, /endin (staff)
     /tryouts/tdone   -> /tdone (staff)
     /matchmaking     -> the matchmaking panel buttons (join/leave a queue —
                          this really creates a private duel channel, same
                          as clicking the button in Discord)
     /moderation      -> /bandm, /warndm, /bandmtest, /warndmtest (staff)

All the same permission checks the slash commands use (role IDs from
bot.py's config) are re-applied here, so the dashboard can't be used to do
anything a given Discord member couldn't already do with commands.

WIRING IT INTO bot.py
----------------------
Add these two lines near the BOTTOM of bot.py — after everything else is
defined (the `db`/collections, `client`, every config constant, and every
helper function), but before the `if __name__ == "__main__":` block:

    from dashboard import init_dashboard
    init_dashboard(app)

`app` is the exact same Flask app bot.py already created for the health
check route — nothing new needs to run or listen on a second port.

REQUIRED ENV VARS
------------------
Create a Discord OAuth2 application (you can reuse the bot's own
application at https://discord.com/developers/applications -> your app ->
OAuth2) and set:

    DISCORD_CLIENT_ID       — the application's Client ID
    DISCORD_CLIENT_SECRET   — the application's Client Secret
    DASHBOARD_REDIRECT_URI  — e.g. https://your-app.onrender.com/dashboard/callback
                              (must be added under OAuth2 -> Redirects in
                              the Discord dev portal, EXACTLY as written)

Optional:
    DASHBOARD_SECRET_KEY    — Flask session signing key. If unset, a random
                              one is generated at boot, which means every
                              logged-in session is invalidated on every
                              restart/redeploy. Set a fixed random string
                              (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`)
                              as a Render env var for persistent logins.

ALSO NEEDS: `pip install requests` (everything else — Flask, pymongo,
discord.py, Pillow — is already a bot.py dependency).
"""

import os
import io
import time
import random
import asyncio
import secrets
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode
import bot as botmod

import discord
import requests
from PIL import Image
from flask import (
    Flask, Blueprint, redirect, request, session, url_for,
    render_template_string, abort, flash,
)

# `bot` must already be fully loaded by the time this module is imported —
# see the wiring instructions above (import dashboard AFTER everything else
# in bot.py is defined). Referencing it via the module object (rather than
# `from bot import x, y, z`) means every lookup below happens at call time,
# once bot.py has finished setting everything up.

logger = logging.getLogger("blz-dashboard")

# Reuse the SAME Flask app bot.py already created (it has the "/" health-check
# route Render pings). Creating a second, separate Flask() here was the bug:
# the dashboard blueprint would get registered on bot.py's app while a totally
# different, route-less app was the one actually being served -> 404 on
# everything, including "/" and every "/dashboard/..." page.
app = botmod.app

# =====================================================================================
# CONFIG
# =====================================================================================

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DASHBOARD_REDIRECT_URI = os.getenv("DASHBOARD_REDIRECT_URI")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY") or secrets.token_hex(32)

if not all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI]):
    logger.warning(
        "!!! [DASHBOARD] DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET / DASHBOARD_REDIRECT_URI "
        "are not fully set in the environment — Discord login will fail until they are."
    )

if not os.getenv("DASHBOARD_SECRET_KEY"):
    logger.warning(
        "!!! [DASHBOARD] DASHBOARD_SECRET_KEY is not set — using a random key generated "
        "at boot. Every restart/redeploy will invalidate ALL existing sessions, including "
        "one that's mid-login (someone can hit /login, then /callback fail with 'Invalid "
        "OAuth state' if the process restarts in between, and be stuck unable to log in "
        "at all until the process stays up long enough). Set a fixed DASHBOARD_SECRET_KEY "
        "in Render's env vars to fix this permanently."
    )

# The 3 "root" admins. They always have admin access and can promote/demote
# any other logged-in member to/from admin from Admin -> Manage Admins. Root
# admins themselves can't be demoted through the dashboard UI.
ADMIN_DISCORD_IDS = {1075463469865906216, 898579360720764999, 1375115979285073951}

# Public-facing name used on the landing page / <title> / support-server links.
SERVER_NAME = "Blazing Lock"

# Staff section — announcements & FAQ are read live from these Discord channels;
# the sheet just opens externally (staff already have Google access to it).
STAFF_ANNOUNCEMENTS_CHANNEL_ID = 1538589350768676990
STAFF_FAQ_CHANNEL_ID = 1538589350768676992
STAFF_SHEET_URL = "https://docs.google.com/spreadsheets/d/1IFbMfdH1aq_LrK_-owNkZmj1POqQVxs_wJvOwd91VYw/edit?usp=drivesdk"

# Coin emoji images for the web dashboard. bot.py's CURRENCY/COIN1-4 constants are
# Discord's <:name:id> markup, which only renders inside Discord clients -- in a
# browser it would just show up as literal text. These CDN links are the same four
# server emojis, used here as <img> tags instead.
COIN_IMAGE_URLS = {
    "coin1": "https://cdn.discordapp.com/emojis/1328399864526143488.webp?size=96",
    "coin2": "https://cdn.discordapp.com/emojis/1345765306655707198.webp?size=96",
    "coin3": "https://cdn.discordapp.com/emojis/1321451928864952361.webp?size=96",
    "coin4": "https://cdn.discordapp.com/emojis/1361906308214947880.webp?size=96",
}


def coin_img(name="coin2"):
    """Inline <img> for a coin emoji, sized to the surrounding text via the
    .coin-icon CSS class. Only use this in template context vars marked
    |safe -- never inside flash() messages, which render as plain text and
    can contain user-controlled data (display names, etc.)."""
    return f'<img src="{COIN_IMAGE_URLS[name]}" alt="coin" class="coin-icon">'


# Plain-text fallback for flash() messages, which are rendered without |safe
# (some contain user-controlled display names, so they must stay unescaped-HTML-free).
CURRENCY_TEXT = "🪙"

DISCORD_API = "https://discord.com/api"
OAUTH_AUTHORIZE_URL = f"{DISCORD_API}/oauth2/authorize"
OAUTH_TOKEN_URL = f"{DISCORD_API}/oauth2/token"
OAUTH_USER_URL = f"{DISCORD_API}/users/@me"

dash_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

# One more collection in the SAME MongoDB database bot.py already connected
# to — nothing new to configure.
access_col = botmod.db["dashboard_access"]
access_col.create_index("status")
access_col.create_index("is_admin")


# =====================================================================================
# HELPERS — running coroutines on the bot's event loop from this Flask thread
# =====================================================================================

def run_coro(coro, timeout=20):
    """Schedules a coroutine on the live discord.py event loop and blocks this
    (Flask request) thread for the result. Needed for anything that touches the
    real Discord connection — sending DMs, creating channels, assigning roles,
    posting messages. Pure database reads/writes don't need this; they use the
    bot's already-thread-safe `_..._sync` pymongo helpers directly instead."""
    if not botmod.bot_ready_event.is_set():
        raise RuntimeError("The Discord bot isn't connected yet — try again in a moment.")
    future = asyncio.run_coroutine_threadsafe(coro, botmod.client.loop)
    return future.result(timeout=timeout)


def get_member(user_id):
    """Cached guild member lookup — read-only, no event loop needed."""
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is None:
        return None
    return guild.get_member(user_id)


def has_role(user_id, role_ids):
    if isinstance(role_ids, int):
        role_ids = {role_ids}
    member = get_member(user_id)
    if member is None:
        return False
    return any(r.id in role_ids for r in member.roles)


def discord_avatar_url(user_id, avatar_hash, size=128):
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size={size}"
    index = (int(user_id) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def member_avatar_url(user_id, size=128):
    member = get_member(user_id)
    if member is not None:
        try:
            return str(member.display_avatar.replace(size=size))
        except Exception:
            return str(member.display_avatar)
    return discord_avatar_url(user_id, None, size=size)


def display_name_for(user_id, fallback=None):
    member = get_member(user_id)
    if member is not None:
        return member.display_name
    return fallback or f"User {user_id}"


def _player_row(user_id):
    doc = botmod.players_col.find_one({"_id": user_id})
    if doc is None:
        return botmod.PlayerRow(user_id, f"User {user_id}", 1000, 0, 0, 0, 0, 0, 0)
    return botmod.PlayerRow(
        doc["_id"], doc["username"], doc["elo"],
        doc["ranked_wins"], doc["ranked_losses"], doc["ranked_draws"],
        doc["friendly_wins"], doc["friendly_losses"], doc["friendly_draws"],
    )


# =====================================================================================
# AUTH — session, CSRF, access-approval status
# =====================================================================================

def _discord_user():
    return session.get("discord_user")


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _check_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf"):
        abort(400, "Your session expired — please reload the page and try again.")


def _access_status(user_id):
    """Any Discord account that has ever logged in (or a root admin) has
    full dashboard access — there's no approval queue anymore. This still
    returns the string "approved" (rather than a bool) so every existing
    `{% if status == "approved" %}` check in the templates keeps working
    unchanged."""
    if user_id in ADMIN_DISCORD_IDS:
        return "approved"
    return "approved" if access_col.find_one({"_id": user_id}) else None


def is_admin_user(user_id):
    """True for the 3 root admins, or anyone a root admin has promoted from
    Admin -> Manage Admins."""
    if user_id in ADMIN_DISCORD_IDS:
        return True
    doc = access_col.find_one({"_id": user_id})
    return bool(doc and doc.get("is_admin"))


def is_staff_user(user_id):
    """Gates the Staff section (announcements, escalation guide, FAQ, sheet).
    Admins always have it; anyone else needs to be individually approved from
    Admin -> Manage Staff."""
    if is_admin_user(user_id):
        return True
    doc = access_col.find_one({"_id": user_id})
    return bool(doc and doc.get("is_staff"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _discord_user():
            return redirect(url_for("dashboard.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def approved_required(view):
    """Kept as its own decorator (rather than swapping every route over to
    login_required) in case a future feature needs to suspend a specific
    member's dashboard access again without touching every route."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _discord_user()
        if not user:
            return redirect(url_for("dashboard.login", next=request.path))
        if _access_status(user["id"]) != "approved":
            return redirect(url_for("dashboard.home"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _discord_user()
        if not user or not is_admin_user(user["id"]):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _discord_user()
        if not user:
            return redirect(url_for("dashboard.login", next=request.path))
        if not is_staff_user(user["id"]):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# =====================================================================================
# LAYOUT
# =====================================================================================

LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#05070d">
<title>{{ title }} · Blazing Lock</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Big+Shoulders:wght@700;900&family=JetBrains+Mono:wght@400;500;700&display=swap');

  :root {
    color-scheme: dark; /* stops the OS/browser dark-mode heuristics from
                            re-tinting form controls/scrollbars — this theme
                            is always dark, on purpose, everywhere */
    --bg: #05070d; --surface: #0c111d; --surface-2: #131c30;
    --line: #1c2740; --line-bright: #2e4064;
    --text: #eef1f8; --text-dim: #7e89a8;
    --accent: #2f6bff; --accent-dim: #16224a;
    --danger: #ff3b4e; --danger-dim: #401019;
    --warn: #ffb020; --warn-dim: #4a3508;
    --info: #5ea8ff;
    --font-display: 'Big Shoulders', sans-serif;
    --font-body: 'JetBrains Mono', monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: var(--font-body); font-size: 15px; color: var(--text); min-height: 100vh;
    background-color: var(--bg);
    background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
                       linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
    background-size: 40px 40px;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .dock {
    position: fixed; left: 14px; top: 14px; width: 210px; z-index: 30;
    background: var(--surface); border: 1px solid var(--line-bright);
    opacity: 0; pointer-events: none; transition: opacity .15s ease;
  }
  .dock.show { opacity: 1; pointer-events: auto; }
  .dock-brand { padding: 16px 18px 12px; border-bottom: 1px solid var(--line); }
  .dock-brand .mark {
    display: inline-block; width: 9px; height: 9px; background: var(--accent); margin-right: 8px;
    animation: blip 1s steps(2) infinite;
  }
  @keyframes blip { 50% { opacity: .25; } }
  .dock-brand .word { font-family: var(--font-display); font-weight: 900; font-size: 18px; letter-spacing: .03em; color: var(--text); text-transform: uppercase; }
  .dock-brand .word span { color: var(--accent); }
  .dock-nav { padding: 6px 0; display: flex; flex-direction: column; }
  .dock-nav a {
    display: flex; align-items: baseline; gap: 10px; padding: 9px 18px; color: var(--text-dim);
    font-size: 13px; text-transform: uppercase; letter-spacing: .05em; border-left: 3px solid transparent;
  }
  .dock-nav a .idx { font-size: 11px; color: var(--line-bright); }
  .dock-nav a:hover, .dock-nav a:focus-visible { color: var(--text); text-decoration: none; background: var(--surface-2); border-left-color: var(--accent); }
  .dock-nav a:hover .idx, .dock-nav a:focus-visible .idx { color: var(--accent); }
  .dock-nav a.kbd-active { color: var(--text); background: var(--surface-2); border-left-color: var(--accent); }
  .dock-nav a.kbd-active .idx { color: var(--accent); }
  .badge { background: var(--danger); color: var(--bg); font-family: var(--font-body); font-weight: 700; font-size: 10px; padding: 2px 6px; margin-left: auto; }
  .helpbtn {
    position: fixed; left: 14px; bottom: 14px; z-index: 30; width: 28px; height: 28px;
    display: flex; align-items: center; justify-content: center;
    background: var(--surface); border: 1px solid var(--line-bright); color: var(--text-dim);
    font-family: var(--font-body); font-weight: 700; font-size: 13px;
  }
  .helpbtn:hover, .helpbtn:focus, .helpbtn.open { color: var(--accent); border-color: var(--accent); }
  .helpbtn .tip {
    position: absolute; left: 36px; bottom: 0; width: 210px; background: var(--surface);
    border: 1px solid var(--line-bright); padding: 10px 12px; font-size: 12px; color: var(--text-dim); line-height: 1.5;
    text-transform: none; letter-spacing: normal; opacity: 0; pointer-events: none;
    transform: translateX(-6px); transition: opacity .12s, transform .12s;
  }
  .helpbtn:hover .tip, .helpbtn:focus .tip, .helpbtn.open .tip { opacity: 1; transform: none; pointer-events: auto; }
  .idbox {
    position: fixed; top: 18px; right: 24px; z-index: 20; display: flex; align-items: center; gap: 10px;
    font-size: 13px; white-space: nowrap; color: var(--text-dim); background: var(--surface);
    border: 1px solid var(--line); padding: 7px 12px;
  }
  .idbox img { width: 24px; height: 24px; border: 1px solid var(--line-bright); border-radius: 0; }
  main { max-width: 960px; margin: 0 auto; padding: 70px 40px 70px; position: relative; z-index: 1; }
  .linkrow { display: flex; flex-wrap: wrap; border: 1px solid var(--line); background: var(--surface); margin-top: 16px; }
  .linkrow a { padding: 10px 16px; color: var(--text-dim); font-size: 13px; text-transform: uppercase; letter-spacing: .04em; border-right: 1px solid var(--line); }
  .linkrow a:last-child { border-right: none; }
  .linkrow a:hover { color: var(--accent); background: var(--surface-2); text-decoration: none; }
  @media (max-width: 760px) {
    .dock {
      opacity: 1 !important; pointer-events: auto !important; position: fixed; top: auto !important; bottom: 0; left: 0; right: 0;
      width: auto; height: 58px; display: flex; align-items: center; border: none; border-top: 2px solid var(--line-bright);
    }
    .dock-brand { display: none; }
    .dock-nav { flex-direction: row; padding: 0; overflow-x: auto; flex: 1; }
    .dock-nav a { border-left: none; border-top: 3px solid transparent; padding: 0 16px; white-space: nowrap; height: 58px; }
    .dock-nav a:hover, .dock-nav a:focus-visible { border-left-color: transparent; border-top-color: var(--accent); }
    .helpbtn { display: none; }
    .idbox { top: 12px; right: 12px; padding: 5px 9px; font-size: 0; }
    .idbox img { margin: 0; }
    .idbox .btn { font-size: 11px; }
    main { padding: 24px 20px 90px; }
  }
  .flash {
    padding: 12px 16px; margin-bottom: 12px; font-size: 14px; position: relative;
    background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--text-dim);
  }
  .flash.success { border-left-color: var(--accent); }
  .flash.error { border-left-color: var(--danger); }
  .flash.info { border-left-color: var(--info); }
  h1 { font-family: var(--font-display); font-weight: 900; font-size: 30px; line-height: 1.2; margin: 0 0 20px; color: var(--text); text-transform: uppercase; letter-spacing: .02em; }
  h1::before { content: "// "; font-family: var(--font-body); color: var(--accent); }
  h2 {
    font-family: var(--font-body); font-weight: 700; font-size: 12px; margin: 32px 0 14px; color: var(--text-dim);
    letter-spacing: .1em; text-transform: uppercase; border-left: 3px solid var(--accent); padding-left: 10px;
  }
  .card, .stat {
    background: var(--surface); border: 1px solid var(--line); padding: 20px; position: relative;
    animation: pop-in .25s ease-out backwards;
  }
  .card { margin-bottom: 16px; }
  .card::before, .card::after, .stat::before, .stat::after {
    content: ""; position: absolute; width: 9px; height: 9px; border: 2px solid var(--accent); opacity: .7; pointer-events: none;
  }
  .card::before, .stat::before { top: -1px; left: -1px; border-right: none; border-bottom: none; }
  .card::after, .stat::after { bottom: -1px; right: -1px; border-left: none; border-top: none; }
  @keyframes pop-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  .grid > *:nth-child(1) { animation-delay: 0ms; } .grid > *:nth-child(2) { animation-delay: 40ms; }
  .grid > *:nth-child(3) { animation-delay: 80ms; } .grid > *:nth-child(4) { animation-delay: 120ms; }
  .grid > *:nth-child(5) { animation-delay: 160ms; } .grid > *:nth-child(6) { animation-delay: 200ms; }
  .grid > *:nth-child(n+7) { animation-delay: 220ms; }
  a.card { display: block; color: var(--text); transition: border-color .1s, transform .1s; }
  a.card:hover { text-decoration: none; border-color: var(--line-bright); transform: translateY(-2px); }
  a.card:hover::before, a.card:hover::after { opacity: 1; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
  .stat .label { color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
  .stat .value { font-family: var(--font-display); font-weight: 900; font-size: 28px; margin-top: 8px; display: block; color: var(--text); }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }
  th { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-dim); border-bottom: 2px solid var(--line-bright); }
  tr:hover td { background: var(--surface-2); }
  .avatar-sm { width: 22px; height: 22px; border: 1px solid var(--line-bright); border-radius: 0; vertical-align: middle; margin-right: 9px; }
  .progress { background: var(--surface-2); border: 1px solid var(--line); height: 16px; overflow: hidden; padding: 2px; }
  .progress > div { background: var(--accent); height: 100%; transition: width .8s ease; }
  .btn {
    display: inline-block; background: var(--accent); color: var(--bg); border: 1px solid var(--accent);
    padding: 10px 18px; font-family: var(--font-body); font-size: 14px; cursor: pointer; font-weight: 700;
    text-transform: uppercase; letter-spacing: .05em; transition: background .1s, color .1s, opacity .1s;
  }
  .btn:hover { text-decoration: none; opacity: .85; }
  .btn:active { opacity: .65; }
  .btn.secondary { background: transparent; color: var(--text); border-color: var(--line-bright); }
  .btn.small { padding: 6px 11px; font-size: 12px; }
  .btn.danger { background: var(--danger); border-color: var(--danger); }
  .btn.success { background: var(--accent); border-color: var(--accent); }
  input[type=text], input[type=number], input[type=password], textarea, select, input[type=file] {
    width: 100%; background: var(--surface-2); border: 1px solid var(--line); border-radius: 0;
    padding: 9px 10px; color: var(--text); font-size: 14px; margin-top: 5px; font-family: var(--font-body);
  }
  input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
  label { font-size: 11px; color: var(--text-dim); font-weight: 700; text-transform: uppercase; letter-spacing: .06em; font-family: var(--font-body); }
  form.inline { display: inline-block; margin-right: 6px; }
  .field { margin-bottom: 15px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
  .row .field { flex: 1; min-width: 160px; }
  .muted { color: var(--text-dim); }
  .pill { display: inline-block; padding: 3px 10px 3px 8px; font-size: 12px; background: var(--surface-2); border-left: 3px solid var(--text-dim); text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim); }
  .pill.pending { border-left-color: var(--warn); color: var(--warn); }
  .pill.approved { border-left-color: var(--accent); color: var(--accent); }
  .pill.denied { border-left-color: var(--danger); color: var(--danger); }
  .empty { color: var(--text-dim); padding: 18px 0; text-align: center; font-size: 14px; }
  .coin-icon { height: 1em; width: 1em; vertical-align: -0.15em; }
  .center { text-align: center; }
  .login-hero { text-align: center; padding: 70px 20px; }
  .login-hero h1 { font-size: 36px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 18px; flex-wrap: wrap; }
  .tabs a { padding: 8px 14px; background: var(--surface); border: 1px solid var(--line); color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  .tabs a.active { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  ::-webkit-scrollbar { width: 12px; height: 12px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--line-bright); }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
  }
  :root { --bg:#080808; --surface:#111; --surface-2:#191919; --line:#303030; --line-bright:#686868; --text:#f4f0e8; --text-dim:#9b968d; --accent:#d7ff3f; --accent-dim:#26300c; --danger:#ff5c5c; --font-display:'Big Shoulders',sans-serif; --font-body:'JetBrains Mono',monospace; }
  body { background-color:var(--bg); background-image:radial-gradient(circle at 85% 10%,rgba(215,255,63,.12),transparent 28%),linear-gradient(135deg,transparent 0 49%,rgba(255,255,255,.035) 50% 50.2%,transparent 50.4%),linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px); background-size:auto,auto,48px 48px; overflow-x:hidden; }
  body::before { display:none; }
  main { max-width:1180px; padding:clamp(92px,12vw,150px) clamp(20px,6vw,72px) 110px; }
  .dock { width:240px; background:rgba(17,17,17,.94); border:1px solid var(--line-bright); box-shadow:12px 12px 0 var(--accent); }
  .dock-brand { padding:20px; } .dock-brand .mark { background:var(--accent); }
  .dock-nav a { padding:12px 20px; } .dock-nav a:hover,.dock-nav a:focus-visible,.dock-nav a.kbd-active { background:var(--accent); color:var(--bg); border-left-color:var(--bg); }
  .dock-nav a:hover .idx,.dock-nav a:focus-visible .idx,.dock-nav a.kbd-active .idx { color:var(--bg); }
  .idbox { top:22px; right:clamp(18px,5vw,64px); background:transparent; border:0; padding:0; }
  .search-shell { position:relative; display:flex; align-items:center; width:38px; height:38px; overflow:visible; }
  .search-shell::before { content:'⌕'; position:absolute; left:12px; top:7px; color:var(--accent); font-size:22px; pointer-events:none; z-index:1; }
  .search-input { width:38px !important; height:38px; margin:0 !important; padding:0 12px 0 38px !important; background:var(--surface) !important; border:1px solid var(--line-bright) !important; color:var(--text) !important; transition:width .25s cubic-bezier(.2,.8,.2,1),box-shadow .25s; }
  .appactions .search-shell { width:clamp(190px,24vw,380px); } .appactions .search-input { width:100% !important; box-shadow:none; } .appactions .search-shell:focus-within .search-input { box-shadow:4px 4px 0 var(--accent); }
  .search-results { position:absolute; top:44px; right:0; width:260px; background:var(--surface); border:1px solid var(--line-bright); display:none; max-height:280px; overflow:auto; box-shadow:6px 6px 0 var(--accent); }
  .search-results.open { display:block; } .search-results a { display:block; padding:11px 13px; color:var(--text-dim); border-bottom:1px solid var(--line); font-size:12px; } .search-results a:hover { background:var(--accent); color:var(--bg); text-decoration:none; }
  h1 { font-size:clamp(38px,7vw,82px); max-width:850px; line-height:.9; letter-spacing:0; margin-bottom:28px; } h1::before { content:'↳ '; color:var(--accent); }
  h2 { margin-top:54px; color:var(--accent); border-left:0; border-top:1px solid var(--accent); padding:10px 0 0; }
  .card,.stat { background:linear-gradient(145deg,rgba(255,255,255,.065),rgba(255,255,255,.018)); border-color:var(--line); border-radius:0; padding:24px; box-shadow:4px 4px 0 rgba(215,255,63,.13); }
  .card::before,.stat::before { border-color:var(--accent); } .card::after,.stat::after { border-color:var(--accent); }
  .grid { gap:20px; } .grid > *:hover { box-shadow:8px 8px 0 var(--accent); }
  .stat .value { font-size:42px; color:var(--accent); } .btn { background:var(--accent); border-color:var(--accent); border-radius:0; padding:12px 20px; } .btn.secondary { color:var(--text); border-color:var(--line-bright); }
  .progress > div { background:var(--accent); } .pill.approved { color:var(--accent); border-left-color:var(--accent); }
  .dock, .helpbtn, .idbox { display:none !important; }
  .appbar { position:fixed; top:0; left:0; right:0; z-index:25; min-height:64px; display:flex; align-items:center; gap:22px; padding:12px clamp(16px,5vw,64px); background:rgba(8,8,8,.9); border-bottom:1px solid var(--line-bright); backdrop-filter:blur(10px); }
  .appbrand { color:var(--text); font:900 22px var(--font-display); letter-spacing:.04em; white-space:nowrap; } .appbrand span { color:var(--accent); }
  .appnav { display:flex; align-items:center; gap:4px; flex:1; overflow:auto; } .appnav a { padding:8px 10px; color:var(--text-dim); font-size:11px; text-transform:uppercase; letter-spacing:.06em; white-space:nowrap; } .appnav a:hover { color:var(--accent); text-decoration:none; background:var(--accent-dim); }
  .appactions { display:flex; align-items:center; gap:10px; margin-left:auto; white-space:nowrap; } .appactions img { width:26px; height:26px; border:1px solid var(--line-bright); }
  @media (max-width:760px) { .appbar { flex-wrap:wrap; gap:8px; padding:9px 14px; } .appnav { order:3; flex-basis:100%; margin:0 -14px; padding:0 14px 3px; } .appactions { margin-left:auto; } main { padding-top:128px; } }
  @media (max-width:760px) { main { padding-top:128px; } .appactions { width:100%; } .appactions .search-shell { width:100%; } .search-input { width:100% !important; } .search-results { width:min(86vw,320px); } h1 { font-size:clamp(42px,14vw,70px); } }
  .scroll-progress { position:fixed; top:0; left:0; width:100%; height:3px; z-index:100; background:var(--accent); transform:scaleX(0); transform-origin:left; }
  main > * { --scroll-shift:0px; opacity:0; transform:translate3d(0,calc(18px + var(--scroll-shift)),0); transition:opacity .7s ease,transform .9s cubic-bezier(.2,.7,.2,1); }
  main > *.is-visible { opacity:1; transform:translate3d(0,var(--scroll-shift),0); }
  main h1 { text-wrap:balance; } main .grid > * { transition:transform .6s cubic-bezier(.2,.7,.2,1),box-shadow .25s; }
  @media (prefers-reduced-motion:reduce) { main > * { opacity:1; transform:none; transition:none; } }
  .cinema-scroll { position:relative; height:100vh; }
  .cinema-world { position:sticky; top:0; height:100vh; overflow:hidden; perspective:1200px; }
  .cinema-world::before,.cinema-world::after { content:''; position:fixed; inset:0; pointer-events:none; z-index:-1; }
  .cinema-world::before { background:radial-gradient(ellipse at 50% 110%,rgba(215,255,63,.16),transparent 44%),linear-gradient(115deg,transparent 0 47%,rgba(215,255,63,.06) 48%,transparent 49%); transform:scale(1.2); }
  .cinema-world::after { background:linear-gradient(90deg,rgba(8,8,8,.9),transparent 20%,transparent 80%,rgba(8,8,8,.9)),linear-gradient(0deg,rgba(8,8,8,.7),transparent 18%,transparent 82%,rgba(8,8,8,.65)); }
  .cinema-orbit { position:fixed; width:42vw; height:42vw; max-width:620px; max-height:620px; right:-10vw; top:18vh; border:1px solid rgba(215,255,63,.28); border-radius:50%; transform:rotateX(68deg) rotateZ(var(--orbit,0deg)); pointer-events:none; z-index:-1; box-shadow:0 0 90px rgba(215,255,63,.08); }
  .cinema-orbit::before { content:''; position:absolute; inset:12%; border:1px solid rgba(215,255,63,.12); border-radius:50%; }
  main { width:100vw; height:calc(100vh - 64px); overflow:hidden; transform-style:preserve-3d; }
  .cinema-track { display:flex; height:100%; width:max-content; opacity:1 !important; transform-style:preserve-3d; will-change:transform; }
  html,body { scrollbar-width:none; -ms-overflow-style:none; overflow-x:hidden; } html::-webkit-scrollbar,body::-webkit-scrollbar,.cinema-world::-webkit-scrollbar,.cinema-track::-webkit-scrollbar { display:none; width:0; height:0; }
  .cinema-track > * { position:relative; flex:0 0 100vw; width:100vw; height:100%; min-height:0; overflow:hidden; display:flex; flex-direction:column; justify-content:center; padding:clamp(42px,8vw,110px) clamp(24px,7vw,110px); margin:0; border:0; transform-origin:50% 50%; }
  .cinema-track > *::before { content:attr(data-scene); position:absolute; top:24px; right:clamp(24px,7vw,110px); color:var(--accent); font-size:10px; letter-spacing:.2em; opacity:.55; }
  .cinema-track > *.is-visible { transform:scale(1); }
  .cinema-flash { position:relative; z-index:3; }
  .cinema-camera { position:fixed; bottom:22px; left:clamp(16px,5vw,64px); z-index:20; display:flex; align-items:center; gap:12px; color:var(--text-dim); font-size:10px; letter-spacing:.14em; text-transform:uppercase; }
  .cinema-camera i { display:block; width:42px; height:1px; background:var(--accent); }
  @media (max-width:760px) { .cinema-orbit { width:90vw; height:90vw; top:38vh; right:-38vw; } .cinema-track > * { padding:38px 20px; } .cinema-camera { bottom:76px; } }
  .cinema-scroll { height:auto; } .cinema-world { position:relative; top:auto; height:auto; min-height:100vh; overflow:visible; } main { width:auto; height:auto; min-height:100vh; overflow:visible; } .cinema-track { display:block; width:auto; height:auto; transform:none !important; }
  .cinema-track > * { width:auto; height:auto; min-height:78vh; overflow:visible; padding:clamp(70px,10vw,150px) clamp(24px,7vw,110px); }
  .cinema-track .grid { display:flex; flex-wrap:nowrap; gap:20px; overflow:hidden; width:100%; padding:6px 4px 20px; scroll-behavior:auto; overscroll-behavior-x:contain; }
  .cinema-track .grid > * { flex:0 0 min(78vw,360px); min-width:0; }
  .cinema-track .grid { scrollbar-width:none; } .cinema-track .grid::-webkit-scrollbar { display:none; width:0; height:0; }
  .cinema-track > * { display:block; width:auto; height:auto; min-height:0; overflow:visible; padding:0; }
  @media (max-width:760px) { .cinema-track > * { min-height:0; padding:58px 20px; } .cinema-track .grid { gap:14px; } .cinema-track .grid > * { flex-basis:82vw; } }
</style>
</head>
<body>
<div class="scroll-progress"></div>
<div class="cinema-scroll"><div class="cinema-world"><div class="cinema-orbit"></div><div class="cinema-camera"><i></i><span>scroll / navigate</span></div>
<header class="appbar">
  <a class="appbrand" href="{{ url_for('dashboard.home') }}">BLZ<span>/</span>WEB</a>
  {% if status == "approved" %}<nav class="appnav">
    <a href="{{ url_for('dashboard.home') }}">Home</a><a href="{{ url_for('dashboard.elo_leaderboard') }}">ELO</a><a href="{{ url_for('dashboard.economy_home') }}">Economy</a><a href="{{ url_for('dashboard.tryouts_home') }}">Tryouts</a><a href="{{ url_for('dashboard.matchmaking') }}">Matchmaking</a>
    {% if show_staff %}<a href="{{ url_for('dashboard.staff_home') }}">Staff</a>{% endif %}{% if show_moderation %}<a href="{{ url_for('dashboard.moderation') }}">Moderation</a>{% endif %}{% if is_admin %}<a href="{{ url_for('dashboard.admin_access') }}">Admin</a>{% endif %}
  </nav>{% endif %}
  <div class="appactions">
    <div class="search-shell"><input class="search-input" type="search" placeholder="Search" aria-label="Search this page"><div class="search-results"></div></div>
    {% if user %}<img src="{{ user.avatar_url }}" alt=""><a href="{{ url_for('dashboard.logout') }}" class="btn small secondary">Log out</a>{% else %}<a href="{{ url_for('dashboard.login') }}" class="btn small">Log in with Discord</a>{% endif %}
    <a href="/" class="btn small secondary">Back to site</a>
  </div>
</header>
<main><div class="cinema-track">
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="flash {{ category }} cinema-flash">{{ message }}</div>
  {% endfor %}
  {{ content|safe }}
</div></main>
</div></div>
<script>
(function () {
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Tally numbers up from zero like an arcade high-score counting into place,
  // leaving any surrounding text (units, slashes, emoji) exactly where it was.
  function animateValue(el) {
    var raw = el.textContent;
    var matches = raw.match(/\\d[\\d,]*/g);
    if (!matches || reduceMotion) return;
    var targets = matches.map(function (m) { return parseInt(m.replace(/,/g, ''), 10); });
    var duration = 650;
    var startTime = null;

    function frame(ts) {
      if (startTime === null) startTime = ts;
      var t = Math.min((ts - startTime) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      var i = 0;
      el.textContent = raw.replace(/\\d[\\d,]*/g, function () {
        var current = Math.round(targets[i] * eased);
        i++;
        return current.toLocaleString('en-US');
      });
      if (t < 1) {
        window.requestAnimationFrame(frame);
      } else {
        el.textContent = raw;
      }
    }
    window.requestAnimationFrame(frame);
  }
  document.querySelectorAll('.value').forEach(animateValue);

  var scrollProgress = document.querySelector('.scroll-progress');
  function updateProgress() { if (scrollProgress) scrollProgress.style.transform = 'scaleX(' + (window.scrollY / Math.max(1, document.documentElement.scrollHeight - window.innerHeight)) + ')'; }
  document.querySelectorAll('.cinema-track .grid').forEach(function (scene) {
    scene.addEventListener('wheel', function (event) {
      var max = scene.scrollWidth - scene.clientWidth, next = scene.scrollLeft + event.deltaY;
      if (max <= 0 || (next <= 0 && event.deltaY < 0) || (next >= max && event.deltaY > 0)) return;
      event.preventDefault(); scene.scrollLeft = Math.max(0, Math.min(max, next));
    }, { passive:false });
  });
  updateProgress(); window.addEventListener('scroll', updateProgress, { passive:true });

  document.querySelectorAll('.search-shell, .public-search').forEach(function (shell) {
    var input = shell.querySelector('input'), results = shell.querySelector('.search-results');
    if (!input || !results) return;
    var targets = Array.prototype.slice.call(document.querySelectorAll('h1,h2,h3,.card,.stat,.level-card'));
    input.addEventListener('input', function () {
      var term = input.value.trim().toLowerCase(); results.innerHTML = '';
      if (!term) { results.classList.remove('open'); return; }
      targets.filter(function (el) { return el.textContent.toLowerCase().indexOf(term) >= 0; }).slice(0, 8).forEach(function (el, i) {
        if (!el.id) el.id = 'search-result-' + i;
        var link = document.createElement('a'); link.href = '#' + el.id; link.textContent = el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 70);
        link.addEventListener('click', function () { results.classList.remove('open'); input.value = ''; }); results.appendChild(link);
      });
      if (!results.children.length) { var empty = document.createElement('div'); empty.textContent = 'No matches'; empty.style.cssText = 'padding:12px;color:var(--text-dim);font-size:12px;'; results.appendChild(empty); }
      results.classList.add('open');
    });
    input.addEventListener('keydown', function (event) { if (event.key === 'Escape') { input.value = ''; results.classList.remove('open'); input.blur(); } });
    document.addEventListener('click', function (event) { if (!shell.contains(event.target)) results.classList.remove('open'); });
  });
})();
</script>
</body>
</html>"""


def page(title, body_template, **ctx):
    ctx.setdefault("csrf", _csrf_token())
    body_html = render_template_string(body_template, **ctx)

    user = _discord_user()
    if user:
        uid = user["id"]
        status = _access_status(uid)
        is_admin = is_admin_user(uid)
        show_staff = is_staff_user(uid)
        show_moderation = has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID)
        pending_count = 0
    else:
        status, is_admin, show_staff, show_moderation, pending_count = None, False, False, False, 0

    return render_template_string(
        LAYOUT, title=title, content=body_html, user=user,
        status=status, is_admin=is_admin, show_staff=show_staff,
        show_moderation=show_moderation, pending_count=pending_count,
    )


@dash_bp.app_errorhandler(400)
def _bad_request(e):
    return page("Something went wrong", f"""<div class='card center'><h1>⚠️ Something went wrong</h1>
                              <p class='muted'>{e.description or "Please try that again."}</p>
                              <a class="btn" href="{{{{ url_for('dashboard.home') }}}}">Back to dashboard</a></div>"""), 400


@dash_bp.app_errorhandler(403)
def _forbidden(e):
    return page("Forbidden", "<div class='card center'><h1>🚫 Forbidden</h1>"
                              "<p class='muted'>You don't have permission to view this page.</p>"
                              "<a class=\"btn\" href=\"{{ url_for('dashboard.home') }}\">Back to dashboard</a></div>"), 403


@dash_bp.app_errorhandler(404)
def _not_found(e):
    return page("Not Found", "<div class='card center'><h1>🔍 Not Found</h1>"
                              "<p class='muted'>That page doesn't exist.</p>"
                              "<a class=\"btn\" href=\"{{ url_for('dashboard.home') }}\">Back to dashboard</a></div>"), 404


@dash_bp.app_errorhandler(500)
def _server_error(e):
    return page("Error", "<div class='card center'><h1>💥 Something broke</h1>"
                          "<p class='muted'>An unexpected error occurred. Try again in a moment.</p>"
                          "<a class=\"btn\" href=\"{{ url_for('dashboard.home') }}\">Back to dashboard</a></div>"), 500


# =====================================================================================
# AUTH ROUTES
# =====================================================================================

@dash_bp.route("/")
def home():
    user = _discord_user()
    if not user:
        return page("Dashboard", """
<div class="login-hero">
<h1>🔒 Blazing Lock</h1>
<p class="muted">Manage ELO, the economy, tryouts, matchmaking and more — right from the browser.</p>
<a class="btn" href="{{ url_for('dashboard.login') }}">Log in with Discord</a>
</div>""")

    uid = user["id"]
    row = _player_row(uid)
    rank_name, rank_emoji = botmod.get_rank(row.elo)
    pct, progress_label, _ = botmod.get_rank_progress(row.elo)
    econ_doc = botmod._get_econ_sync(uid)
    is_tryouter = has_role(uid, botmod.TRYOUT_QUOTA_ROLE_IDS)
    ep = botmod._get_quota_ep_sync(uid) if is_tryouter else None
    queued_modes = [m for m in ("ranked", "friendly") if uid in botmod.QUEUES[m]]

    return page("Dashboard", """
<h1>Welcome back</h1>
<div class="grid">
  <div class="stat">
    <div class="label">ELO</div>
    <div class="value">{{ elo }}</div>
    <div class="muted">{{ rank_emoji }} {{ rank_name }} · {{ progress_label }}</div>
    <div class="progress" style="margin-top:8px;"><div style="width:{{ pct }}%;"></div></div>
  </div>
  <div class="stat">
    <div class="label">Coin Balance</div>
    <div class="value">{{ balance }} <img src="https://cdn.discordapp.com/emojis/1345765306655707198.webp?size=96" alt="coin" class="coin-icon"></div>
    <a href="{{ url_for('dashboard.economy_home') }}" class="muted">Open economy →</a>
  </div>
  {% if is_tryouter %}
  <div class="stat">
    <div class="label">Weekly Tryout EP</div>
    <div class="value">{{ ep }}/{{ quota_ep_target }}</div>
    <a href="{{ url_for('dashboard.tryouts_home') }}" class="muted">Open tryouts →</a>
  </div>
  {% endif %}
  <div class="stat">
    <div class="label">Matchmaking Queue</div>
    <div class="value">{% if queued_modes %}{{ queued_modes|join(', ')|capitalize }}{% else %}Not queued{% endif %}</div>
    <a href="{{ url_for('dashboard.matchmaking') }}" class="muted">Open matchmaking →</a>
  </div>
</div>

<h2>Quick links</h2>
<div class="grid">
  <a class="card" href="{{ url_for('dashboard.elo_leaderboard') }}"><strong>🏆 ELO Leaderboard</strong><br><span class="muted">See top ranked players</span></a>
  <a class="card" href="{{ url_for('dashboard.tryouts_home') }}"><strong>🎯 Tryouts</strong><br><span class="muted">View your tryout status</span></a>
  <a class="card" href="{{ url_for('dashboard.matchmaking') }}"><strong>⚔️ Matchmaking</strong><br><span class="muted">Queue up for a duel</span></a>
  <a class="card" href="{{ url_for('dashboard.economy_shop') }}"><strong>🛒 Shop</strong><br><span class="muted">Buy items with your coins</span></a>
  <a class="card" href="{{ url_for('dashboard.economy_games') }}"><strong>🎲 Games</strong><br><span class="muted">RPS, coinflip, slots, guess</span></a>
  <a class="card" href="{{ url_for('dashboard.economy_leaderboard') }}"><strong>📈 Coin Leaderboard</strong><br><span class="muted">See the richest players</span></a>
  {% if is_staff_addelo %}<a class="card" href="{{ url_for('dashboard.tryouts_in') }}"><strong>🟢 Manage IN</strong><br><span class="muted">Excuse tryouters from quota</span></a>{% endif %}
  {% if is_staff_addelo %}<a class="card" href="{{ url_for('dashboard.elo_settings') }}"><strong>🎨 ELO Card Settings</strong><br><span class="muted">Accent color &amp; banner</span></a>{% endif %}
  {% if is_moderator %}<a class="card" href="{{ url_for('dashboard.moderation') }}"><strong>🟥 Moderation DMs</strong><br><span class="muted">Send ban/warn notices</span></a>{% endif %}
  {% if show_staff %}<a class="card" href="{{ url_for('dashboard.staff_home') }}"><strong>🔒 Staff</strong><br><span class="muted">Announcements, FAQ, escalation guide &amp; sheet</span></a>{% endif %}
  {% if is_admin %}<a class="card" href="{{ url_for('dashboard.admin_access') }}"><strong>🛡️ Manage Admins</strong><br><span class="muted">Escalate or revoke admin/staff access</span></a>{% endif %}
</div>""",
                elo=row.elo, rank_name=rank_name, rank_emoji=rank_emoji, pct=pct, progress_label=progress_label,
                balance=econ_doc["balance"], is_tryouter=is_tryouter, ep=ep, quota_ep_target=botmod.TRYOUT_QUOTA_EP,
                queued_modes=queued_modes, is_staff_addelo=has_role(uid, botmod.ADDELO_ROLE_ID),
                is_moderator=has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID),
                show_staff=is_staff_user(uid), is_admin=is_admin_user(uid))


def _safe_next_path(raw):
    """Only follow `next` if it's a same-app relative path — an unchecked
    value here would let someone craft a login link that bounces a member
    off to an external site right after they authenticate."""
    if raw and raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return url_for("dashboard.home")


@dash_bp.route("/login")
def login():
    if not all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI]):
        abort(500, "Discord login isn't configured yet — DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET / "
                    "DASHBOARD_REDIRECT_URI need to be set in the environment.")
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["next"] = _safe_next_path(request.args.get("next"))
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DASHBOARD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return redirect(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@dash_bp.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        flash(f"Discord login was cancelled ({error}).", "error")
        return redirect(url_for("dashboard.home"))

    state = request.args.get("state")
    if not state or state != session.pop("oauth_state", None):
        logger.warning(
            "!!! [DASHBOARD OAUTH] State mismatch on /callback (session cookie missing or "
            "stale — often caused by the process restarting mid-login, or an in-app/embedded "
            "browser blocking the session cookie). Sending the user back to try again."
        )
        return page("Login expired", """
<div class="card center" style="padding:50px 20px;">
<h1>⏳ That login link expired</h1>
<p class="muted">Your login session didn't make it all the way back from Discord — this can
happen if the server restarted at the wrong moment, or if you're using an app's built-in
browser (like tapping the link inside Discord itself) instead of your regular browser.</p>
<p class="muted">Try opening this page in your normal browser (Safari/Chrome/etc.) and log in again.</p>
<a class="btn" href="{{ url_for('dashboard.login') }}">Log in with Discord</a>
</div>""")

    code = request.args.get("code")
    if not code:
        flash("Missing authorization code from Discord — please try again.", "error")
        return redirect(url_for("dashboard.home"))

    token_resp = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DASHBOARD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if token_resp.status_code != 200:
        logger.error(f"!!! [DASHBOARD OAUTH] Token exchange failed: {token_resp.status_code} {token_resp.text}")
        flash("Discord login failed during token exchange. Please try again.", "error")
        return redirect(url_for("dashboard.home"))

    access_token = token_resp.json().get("access_token")
    user_resp = requests.get(OAUTH_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    if user_resp.status_code != 200:
        logger.error(f"!!! [DASHBOARD OAUTH] User fetch failed: {user_resp.status_code} {user_resp.text}")
        flash("Discord login failed while fetching your profile. Please try again.", "error")
        return redirect(url_for("dashboard.home"))

    profile = user_resp.json()
    user_id = int(profile["id"])
    username = profile.get("global_name") or profile.get("username") or f"User {user_id}"
    avatar_hash = profile.get("avatar")

    session["discord_user"] = {
        "id": user_id, "username": username,
        "avatar_url": discord_avatar_url(user_id, avatar_hash),
    }
    session["csrf"] = secrets.token_hex(16)

    # Every login is granted dashboard access immediately — no admin needs to
    # approve it. We still upsert a record per user so the "Manage Admins"
    # page has a list of logged-in members to promote from, and so we can
    # tell root admins apart from promoted ones.
    now = datetime.now(timezone.utc)
    existing = access_col.find_one({"_id": user_id})
    if existing is None:
        access_col.insert_one({
            "_id": user_id, "username": username, "avatar": avatar_hash,
            "status": "approved", "is_admin": user_id in ADMIN_DISCORD_IDS,
            "first_login_at": now, "last_login_at": now,
            "promoted_by": None, "promoted_at": None,
        })
        logger.info(f">>> [DASHBOARD] New dashboard login from {username} ({user_id})")
    else:
        access_col.update_one(
            {"_id": user_id},
            {"$set": {"username": username, "avatar": avatar_hash, "status": "approved", "last_login_at": now}},
        )

    dest = session.pop("next", None) or url_for("dashboard.home")
    return redirect(dest)


@dash_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("dashboard.home"))


# =====================================================================================
# ADMIN — access-request approval queue
# =====================================================================================

ADMIN_ACCESS_TMPL = """
<h1>Manage Admins</h1>
<p class="muted">Anyone who has ever logged in to the dashboard shows up below. Root admins (the 3 built-in accounts) are always admins and can't be changed here. Everyone else can be escalated to admin, or individually granted the Staff section, with one click.</p>

<div class="card">
<h2 style="margin-top:0;">Root Admins</h2>
<table><thead><tr><th>User</th><th></th></tr></thead><tbody>
{% for r in root_admins %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td><span class="pill approved">Root Admin</span></td>
</tr>
{% endfor %}
</tbody></table>
</div>

<div class="card">
<h2 style="margin-top:0;">Promoted Admins ({{ promoted|length }})</h2>
{% if promoted %}
<table><thead><tr><th>User</th><th>Admin since</th><th></th></tr></thead><tbody>
{% for r in promoted %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.promoted_at.strftime('%Y-%m-%d %H:%M UTC') if r.promoted_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='demote') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small danger">Revoke Admin</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody's been promoted yet.</p>{% endif %}
</div>

<div class="card">
<h2 style="margin-top:0;">Staff Access ({{ staff_members|length }})</h2>
<p class="muted" style="margin-top:-6px;">Grants the Staff section (announcements, escalation guide, FAQ, sheet). Admins already have it automatically.</p>
{% if staff_members %}
<table><thead><tr><th>User</th><th>Staff since</th><th></th></tr></thead><tbody>
{% for r in staff_members %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.staff_granted_at.strftime('%Y-%m-%d %H:%M UTC') if r.staff_granted_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_staff_action', uid=r._id, action='revoke') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small danger">Revoke Staff</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody's been granted staff access yet.</p>{% endif %}
</div>

<div class="card">
<h2 style="margin-top:0;">Members ({{ members|length }})</h2>
{% if members %}
<table><thead><tr><th>User</th><th>First login</th><th></th></tr></thead><tbody>
{% for r in members %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.first_login_at.strftime('%Y-%m-%d %H:%M UTC') if r.first_login_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='promote') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small success">Escalate to Admin</button>
    </form>
    {% if not r.is_staff %}
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_staff_action', uid=r._id, action='grant') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small secondary">Grant Staff</button>
    </form>
    {% endif %}
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody's logged in yet.</p>{% endif %}
</div>"""


@dash_bp.route("/admin/access")
@admin_required
def admin_access():
    root_admins = list(access_col.find({"_id": {"$in": list(ADMIN_DISCORD_IDS)}}))
    promoted = list(access_col.find({"is_admin": True, "_id": {"$nin": list(ADMIN_DISCORD_IDS)}}).sort("promoted_at", -1))
    members = list(access_col.find({"is_admin": {"$ne": True}, "_id": {"$nin": list(ADMIN_DISCORD_IDS)}}).sort("last_login_at", -1))
    staff_members = list(access_col.find({"is_staff": True, "is_admin": {"$ne": True}}).sort("staff_granted_at", -1))
    return page("Manage Admins", ADMIN_ACCESS_TMPL, root_admins=root_admins, promoted=promoted,
                members=members, staff_members=staff_members)


@dash_bp.route("/admin/access/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_access_action(uid, action):
    _check_csrf()
    if action not in ("promote", "demote"):
        abort(404)
    if uid in ADMIN_DISCORD_IDS:
        flash("Root admins already have admin access — nothing to change.", "info")
        return redirect(url_for("dashboard.admin_access"))
    make_admin = action == "promote"
    now = datetime.now(timezone.utc)
    result = access_col.update_one(
        {"_id": uid},
        {"$set": {
            "is_admin": make_admin,
            "promoted_by": _discord_user()["id"] if make_admin else None,
            "promoted_at": now if make_admin else None,
        }},
    )
    if result.matched_count == 0:
        flash("That user hasn't logged in to the dashboard yet.", "error")
    else:
        flash(f"{uid} is {'now an admin' if make_admin else 'no longer an admin'}.", "success")
    return redirect(url_for("dashboard.admin_access"))


@dash_bp.route("/admin/staff/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_staff_action(uid, action):
    _check_csrf()
    if action not in ("grant", "revoke"):
        abort(404)
    grant = action == "grant"
    now = datetime.now(timezone.utc)
    result = access_col.update_one(
        {"_id": uid},
        {"$set": {
            "is_staff": grant,
            "staff_granted_by": _discord_user()["id"] if grant else None,
            "staff_granted_at": now if grant else None,
        }},
    )
    if result.matched_count == 0:
        flash("That user hasn't logged in to the dashboard yet.", "error")
    else:
        flash(f"{uid} {'now has' if grant else 'no longer has'} staff access.", "success")
    return redirect(url_for("dashboard.admin_access"))


# =====================================================================================
# STAFF — announcements/FAQ pulled live from Discord, static escalation guide, sheet link
# =====================================================================================

def fetch_staff_channel(channel_id, limit=25):
    try:
        return run_coro(botmod.fetch_channel_messages(channel_id, limit))
    except Exception as e:
        logger.error(f"!!! [STAFF] Failed to fetch channel {channel_id}: {e}")
        return None


STAFF_HOME_TMPL = """
<h1>Staff</h1>
<p class="muted section-intro">Internal tools and references for the Blazing Lock staff team.</p>
<div class="grid">
  <a class="card" href="{{ url_for('dashboard.staff_announcements') }}"><strong>📣 Announcements</strong><br><span class="muted">Latest from the announcements channel</span></a>
  <a class="card" href="{{ url_for('dashboard.staff_guide') }}"><strong>⚖️ Escalation Guide</strong><br><span class="muted">Offense tiers &amp; punishment ladder</span></a>
  <a class="card" href="{{ url_for('dashboard.staff_faq') }}"><strong>❓ Staff FAQ</strong><br><span class="muted">Moderation policies &amp; quick reference</span></a>
  <a class="card" href="{{ sheet_url }}" target="_blank" rel="noopener"><strong>📊 Staff Sheet</strong><br><span class="muted">Opens the shared spreadsheet ↗</span></a>
</div>"""

STAFF_FEED_TMPL = """
<h1>{{ heading }}</h1>
<p class="muted section-intro">Live from <span class="pill approved">#{{ channel_name }}</span> — read-only.</p>
{% if messages is none %}
<div class="card"><p class="empty">Couldn't reach Discord right now — try again in a moment.</p></div>
{% elif messages %}
{% for m in messages %}
<div class="card">
  <img class="avatar-sm" src="{{ m.avatar }}"><strong>{{ m.author }}</strong>
  <span class="muted" style="float:right;font-size:12px;">{{ m.created_at.strftime('%Y-%m-%d %H:%M UTC') }}</span>
  <p style="white-space:pre-wrap;margin:10px 0 0;">{{ m.content or '(no text — attachment/embed only)' }}</p>
</div>
{% endfor %}
{% else %}<div class="card"><p class="empty">No messages in that channel yet.</p></div>{% endif %}"""

STAFF_FAQ_TMPL = """
<h1>Staff FAQ</h1>
<p class="muted section-intro">A quick reference for context, intent, escalation, and staff conduct. When unsure, ask Management or HR before acting.</p>
<div class="card faq-note"><strong>Core rule</strong><p class="muted">Do not punish a keyword by itself. Review the full context, intent, target, repetition, and severity.</p></div>
{% for title, items in faq_sections %}<section class="faq-section"><h2>{{ title }}</h2>
{% for question, answer in items %}<details class="faq-item"><summary>{{ question }}</summary><div class="faq-answer">{{ answer|safe }}</div></details>{% endfor %}
</section>{% endfor %}
"""

STAFF_FAQ_SECTIONS = [
  ("Time Off", [
    ("How does LOA / Off-Duty work?", "Staff may request up to 7 days in <b>#loa-requests</b> for personal reasons, exams, or stress. Extensions need higher-management approval. While off-duty, do not handle tickets, moderation, staff work, meetings, or decisions. Inform Head Staff or Management when returning so the role can be removed."),
  ]),
  ("Context First", [
    ("When is slang or a bypass acceptable?", "Casual, non-targeted slang and non-offensive bypasses generally need no action. Moderate intent and impact, not isolated words. Aggressive or targeted bypasses can begin with a verbal warning and escalate if repeated."),
    ("What about drugs, threats, and religion?", "Ordinary conversation may be ignored or handled verbally. Intentional glorification, direct threats, or severe targeted remarks warrant stronger action. Religion disrespect generally starts at warning plus a 2-hour mute; death threats or wishes are ban-worthy because of the threat, not merely the religious topic."),
    ("How are sensitive topics handled?", "A careless first mention may receive a verbal warning. Serious, repeated, or inappropriate remarks may receive a mute or timeout. Sexual content involving minors, serious harm, or clearly zero-tolerance material is an immediate ban. Always assess context and intent."),
  ]),
  ("Content & Profiles", [
    ("How should GIF bypasses be handled?", "An automodded GIF containing mild bypass text does not need punishment unless it is spammed 3 or more times. Severe material, such as hard slurs, NSFW, or extremist references, may receive the appropriate action."),
    ("What is the profile punishment rule?", "Severe slurs, extremist references, terrorism or abuse jokes, and similar content can be instant-ban cases. NSFW profile images or banners begin with an instant kick followed by a ban. Do not punish ordinary swear words in a profile."),
    ("What counts as animal cruelty?", "Direct threats or violent statements toward animals can be banned; lesser violent remarks generally start verbal, then 2-hour mute. Clearly extreme real-looking violence can be an instant ban. Do not treat an overweight animal image as abuse without evidence of intentional harm or neglect."),
  ]),
  ("Advertising & Safety", [
    ("How do I handle advertising and Roblox TOS violations?", "Discord group-chat links, private-server links, random video links, or platform usernames usually start with a verbal warning and can escalate for spam. Malicious or NSFW links can be ban-worthy. Account buying, selling, trading, or wagering money/Robux is a ban for violating Roblox TOS; account giveaways or requests start verbal."),
    ("What is a compromised account?", "Suspicious links or messages sent without the owner's knowledge may indicate a hacked account. Ask other Staff or HR for guidance before deciding. Do not expose private investigation details."),
    ("When can staff punish in DMs?", "Only when the situation escalated from the Blaze server and the DM offence itself qualifies, such as malicious or NSFW links, black-market activity, death threats, doxxing, hard slurs, or pedophilia. Random unsolicited DMs with no server connection should be ignored."),
  ]),
  ("Escalation & Confidentiality", [
    ("How does stacking work?", "Stacking is allowed only within one specific offence path: verbal → mute → warn → ban, starting at the severity listed for that offence. Do not combine punishments from different offences. Harmful remarks have no mute step: warn, then ban if repeated."),
    ("What is leaking?", "Do not share logs involving another person, internal discussions, guidelines, notes, or protocols with non-staff. A member may be told about action taken against themselves only when they ask. Leaking can lead to strikes or demotion."),
    ("How should weird remarks, drama, and rule encouragement be handled?", "These generally begin with a verbal warning. Repetition can escalate to a 1- or 2-hour mute, then warn and ban. Use judgment and ask HR when the context is unclear."),
  ]),
  ("Commands & Corrections", [
    ("Which commands are useful?", "<code>?modlogs [user]</code> shows history; <code>?reason [case id]</code> edits a reason; <code>?warn</code>, <code>?mute</code>, <code>?unmute</code>, <code>?kick</code>, and <code>?ban</code> apply moderation; <code>?modstats</code> shows stats; <code>?w [user]</code> checks profile details; <code>?case</code> changes a duration. Use <code>?modlogs</code> to find case numbers."),
    ("How do I manually unmute someone?", "For an incorrect punishment, use <code>?w [user id]</code>, open the user's profile, choose Remove Timeout, and confirm. Do not use the unmute command for this correction."),
  ]),
]

FAQ_STYLE = """
<style>.faq-section{padding:0;border:0;margin:32px 0}.faq-item{border:1px solid var(--line);background:var(--surface);margin:8px 0;box-shadow:3px 3px 0 rgba(215,255,63,.12)}.faq-item summary{cursor:pointer;padding:16px 18px;font-weight:700;color:var(--text);list-style:'＋  '}.faq-item[open] summary{color:var(--accent);list-style:'－  '}.faq-answer{padding:0 18px 18px;color:var(--text-dim);line-height:1.7}.faq-answer code{color:var(--accent);background:var(--surface-2);padding:2px 5px}.faq-note{border-left:4px solid var(--accent)}</style>
"""

STAFF_GUIDE_TMPL = """
<h1>Escalation Guide</h1>
<p class="muted section-intro">Standard escalation path: <strong>Verbal (2–3)</strong> → Mute → Warn → Ban, unless a row says otherwise.</p>

<h2>Minor Offenses</h2>
<div class="card"><table><tbody>
{% for name, path in minor %}<tr><td>{{ name }}</td><td class="muted">{{ path }}</td></tr>{% endfor %}
</tbody></table></div>

<h2>Major Offenses</h2>
<div class="card"><table><tbody>
{% for name, path in major %}<tr><td>{{ name }}</td><td class="muted">{{ path }}</td></tr>{% endfor %}
</tbody></table></div>

<h2>Special Cases &amp; Unique Rules</h2>
<div class="card"><table><tbody>
{% for name, path in special %}<tr><td>{{ name }}</td><td class="muted">{{ path }}</td></tr>{% endfor %}
</tbody></table></div>

<h2>Proof &amp; Logging</h2>
<div class="card">
<p>Mandatory: log every action in <span class="pill approved">#punishments</span> with screenshots.</p>
<p class="muted" style="margin-bottom:4px;"><strong style="color:var(--text);">Punishments</strong></p>
<ul class="plain">{% for c in punish_cmds %}<li>{{ c }}</li>{% endfor %}</ul>
<p class="muted" style="margin-bottom:4px;"><strong style="color:var(--text);">Removal / Reversal</strong></p>
<ul class="plain">{% for c in reverse_cmds %}<li>{{ c }}</li>{% endfor %}</ul>
</div>"""

_MINOR_LADDER = "Verbal → 1h → Warn & Mute 1h → Mute 3 Days"
STAFF_MINOR_OFFENSES = [
    ("Spamming / Flooding / Chaining", _MINOR_LADDER),
    ("XP Farming", _MINOR_LADDER),
    ("Channel Misuse", _MINOR_LADDER),
    ("Non-English in Main Chat", _MINOR_LADDER),
    ("Ghost Pings / Useless Pings*", _MINOR_LADDER),
    ("NSFW Remarks", "2h Mute → Warn → Ban"),
    ("Toxicity", "Verbal Warning → 3h Mute → Warn → Ban"),
]
STAFF_MAJOR_OFFENSES = STAFF_MINOR_OFFENSES + [
    ("Minor Bypassing", "Verbal → 2h Mute → Warn → Ban"),
    ("Staff Disrespect", "Verbal Warning → 6h Mute → Warn → Ban"),
    ("Banned Word Bypass", "Verbal Warning → 2h Mute → Warn → Ban"),
    ('Casual Harmful Remarks (e.g. "kms")', "Warn → Ban"),
    ("Staff Impersonation", "Verbal 2x → Warn → Kick"),
]
STAFF_SPECIAL_CASES = [
    ("R-Word Usage", "Verbal Warning → Warn → Ban"),
    ("N-Word Usage (hard R)", "Instant Ban"),
    ("N-Word Usage (non-hard R)", "2h Mute → Warn → Ban"),
    ("Compromised Accounts", "14-Day Mute (until resolved)"),
    ("NSFW Profile (PFP/Banner)", "Kick → Verbal Warning → Ban"),
    ("Sensitive Topics (politics, wars, controversial figures, etc.)", "Warn → Ban"),
    ('Death Threats/Wishes (e.g. "jump off a bridge", "kill yourself")', "Immediate Ban"),
    ('Minor Death Wishes (e.g. "die")', "Verbal Warning 2x → Warn → Ban"),
]
STAFF_PUNISH_CMDS = ["?mute @user [duration] [reason]", "?warn @user [reason]", "?ban @user [reason]"]
STAFF_REVERSE_CMDS = ["?unmute @user", "?unban @user", "?void case [ID] — removes a warning",
                      "?void @user — clears modlogs", "?editcase [case number]"]


@dash_bp.route("/staff")
@staff_required
def staff_home():
    return page("Staff", STAFF_HOME_TMPL, sheet_url=STAFF_SHEET_URL)


@dash_bp.route("/staff/announcements")
@staff_required
def staff_announcements():
    messages = fetch_staff_channel(STAFF_ANNOUNCEMENTS_CHANNEL_ID)
    return page("Announcements", STAFF_FEED_TMPL, heading="📣 Announcements",
                channel_name="announcements", messages=messages)


@dash_bp.route("/staff/faq")
@staff_required
def staff_faq():
  return page("Staff FAQ", FAQ_STYLE + STAFF_FAQ_TMPL, faq_sections=STAFF_FAQ_SECTIONS)


@dash_bp.route("/staff/guide")
@staff_required
def staff_guide():
    return page("Escalation Guide", STAFF_GUIDE_TMPL, minor=STAFF_MINOR_OFFENSES,
                major=STAFF_MAJOR_OFFENSES, special=STAFF_SPECIAL_CASES,
                punish_cmds=STAFF_PUNISH_CMDS, reverse_cmds=STAFF_REVERSE_CMDS)


# =====================================================================================
# ELO
# =====================================================================================

ELO_LEADERBOARD_TMPL = """
<h1>🏆 ELO Leaderboard</h1>
<div class="card">
{% if rows %}
<table><thead><tr><th>#</th><th>Player</th><th>Rank</th><th>ELO</th><th>Ranked W-L-D</th></tr></thead><tbody>
{% for r in rows %}
<tr>
  <td>{{ r.position }}</td>
  <td><a href="{{ url_for('dashboard.elo_profile', uid=r.user_id) }}"><img class="avatar-sm" src="{{ r.avatar }}">{{ r.name }}</a></td>
  <td>{{ r.rank_emoji }} {{ r.rank_name }}</td>
  <td>{{ r.elo }}</td>
  <td class="muted">{{ r.record }}</td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">No ranked players yet.</p>{% endif %}
</div>
<div class="linkrow"><a href="{{ url_for('dashboard.elo_settings') }}">🎨 Card settings</a></div>"""

ELO_PROFILE_TMPL = """
<h1><img class="avatar-sm" style="width:34px;height:34px;" src="{{ avatar }}"> {{ display_name }}</h1>
<div class="grid">
  <div class="stat">
    <div class="label">ELO</div>
    <div class="value">{{ row.elo }}</div>
    <div class="muted">{{ rank_emoji }} {{ rank_name }} · {{ progress_label }}</div>
    <div class="progress" style="margin-top:8px;"><div style="width:{{ pct }}%;"></div></div>
  </div>
  <div class="stat"><div class="label">Ranked Record</div><div class="value">{{ row.ranked_wins }}-{{ row.ranked_losses }}-{{ row.ranked_draws }}</div></div>
  <div class="stat"><div class="label">Friendly Record</div><div class="value">{{ row.friendly_wins }}-{{ row.friendly_losses }}-{{ row.friendly_draws }}</div></div>
</div>

{% if can_adjust %}
<h2>Adjust ELO (staff)</h2>
<div class="card">
<form method="post" action="{{ url_for('dashboard.elo_adjust', uid=row.user_id) }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="row">
    <div class="field"><label>Amount (negative to subtract)</label><input type="number" name="amount" required></div>
    <div class="field"><label>Reason (optional)</label><input type="text" name="reason"></div>
  </div>
  <button class="btn">Apply</button>
</form>
</div>
{% endif %}"""

ELO_SETTINGS_TMPL = """
<h1>🎨 ELO Card Settings</h1>
<div class="card">
<h2 style="margin-top:0;">Your Banner</h2>
<p class="muted">Custom background image for your /elo card{{ ' — currently set.' if has_banner else '.' }}</p>
<form method="post" action="{{ url_for('dashboard.elo_settings_banner') }}" enctype="multipart/form-data">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="field"><input type="file" name="banner" accept="image/*" required></div>
  <button class="btn">Upload</button>
</form>
{% if has_banner %}
<form method="post" action="{{ url_for('dashboard.elo_settings_banner_reset') }}" style="margin-top:8px;">
  <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn secondary">Reset to default</button>
</form>
{% endif %}
</div>

{% if can_set_color %}
<div class="card">
<h2 style="margin-top:0;">Global Accent Color (staff)</h2>
<p class="muted">Overrides the role-based accent color on every /elo card server-wide.
{% if current_color %}Currently: <span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:rgb({{ current_color[0] }},{{ current_color[1] }},{{ current_color[2] }});vertical-align:middle;"></span>
{% else %}Not set — using role colors.{% endif %}</p>
<form method="post" action="{{ url_for('dashboard.elo_settings_color') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="row"><div class="field"><label>Hex color</label><input type="text" name="color" placeholder="ff2e2e"></div></div>
  <button class="btn">Set color</button>
</form>
<form method="post" action="{{ url_for('dashboard.elo_settings_color_reset') }}" style="margin-top:8px;">
  <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn secondary">Reset to role-based default</button>
</form>
</div>
{% endif %}"""


@dash_bp.route("/elo")
@approved_required
def elo_leaderboard():
    top = botmod._get_top_players_sync(50)
    rows = []
    for i, row in enumerate(top, start=1):
        rank_name, rank_emoji = botmod.get_rank(row.elo)
        rows.append({
            "position": i, "user_id": row.user_id,
            "name": display_name_for(row.user_id, fallback=row.username),
            "avatar": member_avatar_url(row.user_id),
            "elo": row.elo, "rank_name": rank_name, "rank_emoji": rank_emoji,
            "record": f"{row.ranked_wins}-{row.ranked_losses}-{row.ranked_draws}",
        })
    return page("ELO Leaderboard", ELO_LEADERBOARD_TMPL, rows=rows)


@dash_bp.route("/elo/<int:uid>")
@approved_required
def elo_profile(uid):
    row = _player_row(uid)
    rank_name, rank_emoji = botmod.get_rank(row.elo)
    pct, progress_label, _ = botmod.get_rank_progress(row.elo)
    return page(f"{row.username} — ELO", ELO_PROFILE_TMPL,
                row=row, rank_name=rank_name, rank_emoji=rank_emoji, pct=pct, progress_label=progress_label,
                can_adjust=has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID),
                avatar=member_avatar_url(uid), display_name=display_name_for(uid, fallback=row.username))


@dash_bp.route("/elo/<int:uid>/adjust", methods=["POST"])
@approved_required
def elo_adjust(uid):
    _check_csrf()
    user = _discord_user()
    if not has_role(user["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Amount must be a whole number.", "error")
        return redirect(url_for("dashboard.elo_profile", uid=uid))
    reason = request.form.get("reason", "").strip() or None

    botmod._get_or_create_player_sync(uid, display_name_for(uid))
    new_elo = botmod._adjust_elo_sync(uid, amount)
    rank_name, rank_emoji = botmod.get_rank(new_elo)

    log_line = (
        f"🛠️ **Manual ELO adjustment (dashboard)** — <@{uid}>: "
        f"{'+' if amount >= 0 else ''}{amount} ELO (now {new_elo}) by <@{user['id']}>."
    )
    if reason:
        log_line += f" Reason: {reason}"
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is not None:
        try:
            run_coro(botmod.post_result(guild, log_line))
        except Exception as e:
            logger.error(f"!!! [DASHBOARD ADDELO LOG]: {e}")

    flash(f"ELO adjusted by {amount:+d} — now {new_elo} ({rank_emoji} {rank_name}).", "success")
    return redirect(url_for("dashboard.elo_profile", uid=uid))


@dash_bp.route("/elo/settings")
@approved_required
def elo_settings():
    uid = _discord_user()["id"]
    return page("ELO Card Settings", ELO_SETTINGS_TMPL,
                can_set_color=has_role(uid, botmod.ADDELO_ROLE_ID),
                current_color=botmod._get_elo_accent_color_sync(),
                has_banner=botmod._get_elo_banner_data_sync(uid) is not None)


@dash_bp.route("/elo/settings/color", methods=["POST"])
@approved_required
def elo_settings_color():
    _check_csrf()
    if not has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    rgb = botmod._parse_hex_color(request.form.get("color", ""))
    if rgb is None:
        flash("Invalid hex color — use a 6-digit hex code, e.g. ff2e2e.", "error")
    else:
        botmod._set_elo_accent_color_sync(rgb)
        flash("Global /elo accent color updated.", "success")
    return redirect(url_for("dashboard.elo_settings"))


@dash_bp.route("/elo/settings/color/reset", methods=["POST"])
@approved_required
def elo_settings_color_reset():
    _check_csrf()
    if not has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    botmod._clear_elo_accent_color_sync()
    flash("Accent color reset to the role-based default.", "success")
    return redirect(url_for("dashboard.elo_settings"))


@dash_bp.route("/elo/settings/banner", methods=["POST"])
@approved_required
def elo_settings_banner():
    _check_csrf()
    uid = _discord_user()["id"]
    file = request.files.get("banner")
    if not file or not file.filename:
        flash("Choose an image file first.", "error")
        return redirect(url_for("dashboard.elo_settings"))
    data = file.read()
    if len(data) > botmod.MAX_ELO_BANNER_BYTES:
        flash(f"That image is too large (max {botmod.MAX_ELO_BANNER_BYTES // (1024 * 1024)}MB).", "error")
        return redirect(url_for("dashboard.elo_settings"))
    try:
        Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        flash("That file doesn't look like a valid image.", "error")
        return redirect(url_for("dashboard.elo_settings"))
    botmod._set_elo_banner_data_sync(uid, data)
    flash("Your /elo banner was updated.", "success")
    return redirect(url_for("dashboard.elo_settings"))


@dash_bp.route("/elo/settings/banner/reset", methods=["POST"])
@approved_required
def elo_settings_banner_reset():
    _check_csrf()
    botmod._clear_elo_banner_sync(_discord_user()["id"])
    flash("Your /elo banner was reset to default.", "success")
    return redirect(url_for("dashboard.elo_settings"))


# =====================================================================================
# ECONOMY
# =====================================================================================

ECONOMY_HOME_TMPL = """
<h1>💰 Economy</h1>
<div class="grid">
  <div class="stat"><div class="label">Balance</div><div class="value">{{ balance }} {{ currency|safe }}</div></div>
  <div class="stat">
    <div class="label">Daily</div>
    {% if daily_ready %}
    <form method="post" action="{{ url_for('dashboard.economy_daily') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small" style="margin-top:6px;">Claim {{ daily_amount }} {{ currency|safe }}</button>
    </form>
    {% else %}<div class="muted" style="margin-top:6px;">Ready in {{ daily_wait }}</div>{% endif %}
  </div>
  <div class="stat">
    <div class="label">Weekly</div>
    {% if weekly_ready %}
    <form method="post" action="{{ url_for('dashboard.economy_weekly') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small" style="margin-top:6px;">Claim {{ weekly_amount }} {{ currency|safe }}</button>
    </form>
    {% else %}<div class="muted" style="margin-top:6px;">Ready in {{ weekly_wait }}</div>{% endif %}
  </div>
  <div class="stat">
    <div class="label">Work</div>
    {% if work_ready %}
    <form method="post" action="{{ url_for('dashboard.economy_work') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small" style="margin-top:6px;">Work a shift</button>
    </form>
    {% else %}<div class="muted" style="margin-top:6px;">Ready in {{ work_wait }}</div>{% endif %}
  </div>
</div>

<h2>Pay someone</h2>
<div class="card">
<form method="post" action="{{ url_for('dashboard.economy_pay') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="row">
    <div class="field"><label>Recipient Discord ID</label><input type="text" name="user_id" required></div>
    <div class="field"><label>Amount</label><input type="number" name="amount" min="1" required></div>
  </div>
  <button class="btn">Pay</button>
</form>
</div>

<h2>Your Inventory</h2>
<div class="card">
{% if inventory %}
<table><thead><tr><th>Item</th><th>Qty</th><th></th></tr></thead><tbody>
{% for entry in inventory %}
<tr>
  <td>{{ entry.item.emoji }} {{ entry.item.name }}</td>
  <td>{{ entry.qty }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.economy_sell') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <input type="hidden" name="item" value="{{ entry.item.id }}"><input type="hidden" name="quantity" value="1">
      <button class="btn small secondary">Sell 1</button>
    </form>
    {% if entry.item.id == 'chest' %}
    <form class="inline" method="post" action="{{ url_for('dashboard.economy_use') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><input type="hidden" name="item" value="chest">
      <button class="btn small">Open</button>
    </form>
    {% endif %}
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Your inventory is empty. Visit the <a href="{{ url_for('dashboard.economy_shop') }}">shop</a>.</p>{% endif %}
</div>

<div class="linkrow"><a href="{{ url_for('dashboard.economy_shop') }}">🛒 Shop</a><a href="{{ url_for('dashboard.economy_leaderboard') }}">📈 Leaderboard</a><a href="{{ url_for('dashboard.economy_games') }}">🎲 Games</a></div>"""

ECONOMY_SHOP_TMPL = """
<h1>🛒 Item Shop</h1>
<p class="muted">Balance: {{ balance }} {{ currency|safe }}</p>
<div class="grid">
{% for it in items %}
<div class="card">
  <strong>{{ it.emoji }} {{ it.name }}</strong> — {{ it.price }} {{ currency|safe }}<br>
  <span class="muted">{{ it.desc }}</span>
  <form method="post" action="{{ url_for('dashboard.economy_buy') }}" style="margin-top:10px;">
    <input type="hidden" name="csrf_token" value="{{ csrf }}"><input type="hidden" name="item" value="{{ it.id }}">
    <div class="row"><div class="field"><label>Qty</label><input type="number" name="quantity" value="1" min="1"></div></div>
    <button class="btn small">Buy</button>
  </form>
</div>
{% endfor %}
</div>"""

ECONOMY_LEADERBOARD_TMPL = """
<h1>📈 Richest Players</h1>
<div class="card">
{% if rows %}
<table><thead><tr><th>#</th><th>Player</th><th>Balance</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{ r.position }}</td><td>{{ r.name }}</td><td>{{ r.balance }} {{ currency|safe }}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="empty">No one has any coins yet.</p>{% endif %}
</div>"""

ECONOMY_GAMES_TMPL = """
<h1>🎲 Games</h1>
<p class="muted">Balance: {{ balance }} {{ currency|safe }}</p>
<div class="grid">
  <div class="card">
    <h2 style="margin-top:0;">Rock Paper Scissors</h2>
    <form method="post" action="{{ url_for('dashboard.economy_rps') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <div class="field"><label>Move</label><select name="choice"><option value="rock">Rock</option><option value="paper">Paper</option><option value="scissors">Scissors</option></select></div>
      <div class="field"><label>Bet (optional)</label><input type="number" name="bet" value="0" min="0"></div>
      <button class="btn">Play</button>
    </form>
  </div>
  <div class="card">
    <h2 style="margin-top:0;">Coinflip</h2>
    <form method="post" action="{{ url_for('dashboard.economy_coinflip') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <div class="field"><label>Side</label><select name="side"><option value="heads">Heads</option><option value="tails">Tails</option></select></div>
      <div class="field"><label>Bet</label><input type="number" name="bet" value="10" min="1"></div>
      <button class="btn">Flip</button>
    </form>
  </div>
  <div class="card">
    <h2 style="margin-top:0;">Slots</h2>
    <form method="post" action="{{ url_for('dashboard.economy_slots') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <div class="field"><label>Bet</label><input type="number" name="bet" value="10" min="1"></div>
      <button class="btn">Spin</button>
    </form>
  </div>
  <div class="card">
    <h2 style="margin-top:0;">Guess the Number (1-10, 10x payout)</h2>
    <form method="post" action="{{ url_for('dashboard.economy_guess') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <div class="field"><label>Guess</label><input type="number" name="number" min="1" max="10" required></div>
      <div class="field"><label>Bet</label><input type="number" name="bet" value="10" min="1"></div>
      <button class="btn">Guess</button>
    </form>
  </div>
</div>"""


@dash_bp.route("/economy")
@approved_required
def economy_home():
    uid = _discord_user()["id"]
    doc = botmod._get_econ_sync(uid)
    now = datetime.now(timezone.utc)
    last_daily, last_weekly, last_work = doc.get("last_daily"), doc.get("last_weekly"), doc.get("last_work")
    daily_ready = not last_daily or botmod._aware(last_daily) + botmod.DAILY_COOLDOWN <= now
    weekly_ready = not last_weekly or botmod._aware(last_weekly) + botmod.WEEKLY_COOLDOWN <= now
    work_ready = not last_work or botmod._aware(last_work) + botmod.WORK_COOLDOWN <= now
    daily_wait = None if daily_ready else botmod._fmt_remaining(botmod._aware(last_daily) + botmod.DAILY_COOLDOWN, now)
    weekly_wait = None if weekly_ready else botmod._fmt_remaining(botmod._aware(last_weekly) + botmod.WEEKLY_COOLDOWN, now)
    work_wait = None if work_ready else botmod._fmt_remaining(botmod._aware(last_work) + botmod.WORK_COOLDOWN, now)
    inventory = [
        {"item": botmod.SHOP_BY_ID[i], "qty": q}
        for i, q in doc.get("inventory", {}).items() if q > 0 and i in botmod.SHOP_BY_ID
    ]
    return page("Economy", ECONOMY_HOME_TMPL, balance=doc["balance"], daily_ready=daily_ready, weekly_ready=weekly_ready,
                work_ready=work_ready, daily_wait=daily_wait, weekly_wait=weekly_wait, work_wait=work_wait,
                daily_amount=botmod.DAILY_AMOUNT, weekly_amount=botmod.WEEKLY_AMOUNT,
                inventory=inventory, currency=coin_img("coin2"))


@dash_bp.route("/economy/daily", methods=["POST"])
@approved_required
def economy_daily():
    _check_csrf()
    uid = _discord_user()["id"]
    doc = botmod._get_econ_sync(uid)
    now = datetime.now(timezone.utc)
    last = doc.get("last_daily")
    if last and botmod._aware(last) + botmod.DAILY_COOLDOWN > now:
        flash(f"Already claimed. Come back in {botmod._fmt_remaining(botmod._aware(last) + botmod.DAILY_COOLDOWN, now)}.", "error")
    else:
        new_bal = botmod._add_balance_sync(uid, botmod.DAILY_AMOUNT)
        botmod.economy_col.update_one({"_id": uid}, {"$set": {"last_daily": now}}, upsert=True)
        flash(f"Claimed your daily {botmod.DAILY_AMOUNT} {CURRENCY_TEXT}! Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/weekly", methods=["POST"])
@approved_required
def economy_weekly():
    _check_csrf()
    uid = _discord_user()["id"]
    doc = botmod._get_econ_sync(uid)
    now = datetime.now(timezone.utc)
    last = doc.get("last_weekly")
    if last and botmod._aware(last) + botmod.WEEKLY_COOLDOWN > now:
        flash(f"Already claimed. Come back in {botmod._fmt_remaining(botmod._aware(last) + botmod.WEEKLY_COOLDOWN, now)}.", "error")
    else:
        new_bal = botmod._add_balance_sync(uid, botmod.WEEKLY_AMOUNT)
        botmod.economy_col.update_one({"_id": uid}, {"$set": {"last_weekly": now}}, upsert=True)
        flash(f"Claimed your weekly {botmod.WEEKLY_AMOUNT} {CURRENCY_TEXT}! Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/work", methods=["POST"])
@approved_required
def economy_work():
    _check_csrf()
    uid = _discord_user()["id"]
    doc = botmod._get_econ_sync(uid)
    now = datetime.now(timezone.utc)
    last = doc.get("last_work")
    if last and botmod._aware(last) + botmod.WORK_COOLDOWN > now:
        flash(f"You're tired. Rest {botmod._fmt_remaining(botmod._aware(last) + botmod.WORK_COOLDOWN, now)} more.", "error")
    else:
        earned = random.randint(botmod.WORK_MIN, botmod.WORK_MAX)
        jobs = ["delivered pizzas", "coded a bot", "walked dogs", "streamed on Twitch", "mowed a lawn", "fixed a PC"]
        new_bal = botmod._add_balance_sync(uid, earned)
        botmod.economy_col.update_one({"_id": uid}, {"$set": {"last_work": now}}, upsert=True)
        flash(f"You {random.choice(jobs)} and earned {earned} {CURRENCY_TEXT}! Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/shop")
@approved_required
def economy_shop():
    doc = botmod._get_econ_sync(_discord_user()["id"])
    return page("Shop", ECONOMY_SHOP_TMPL, items=botmod.SHOP_ITEMS, balance=doc["balance"], currency=coin_img("coin2"))


@dash_bp.route("/economy/buy", methods=["POST"])
@approved_required
def economy_buy():
    _check_csrf()
    uid = _discord_user()["id"]
    item = request.form.get("item", "").lower()
    try:
        qty = int(request.form.get("quantity", "1"))
    except ValueError:
        qty = 0
    if item not in botmod.SHOP_BY_ID or qty < 1:
        flash("Unknown item or invalid quantity.", "error")
        return redirect(url_for("dashboard.economy_shop"))
    it = botmod.SHOP_BY_ID[item]
    cost = it["price"] * qty
    doc = botmod._get_econ_sync(uid)
    if doc["balance"] < cost:
        flash(f"Need {cost} {CURRENCY_TEXT}, you have {doc['balance']}.", "error")
        return redirect(url_for("dashboard.economy_shop"))
    botmod._add_balance_sync(uid, -cost)
    botmod._add_item_sync(uid, item, qty)
    flash(f"Bought {qty}x {it['emoji']} {it['name']} for {cost} {CURRENCY_TEXT}.", "success")
    return redirect(url_for("dashboard.economy_shop"))


@dash_bp.route("/economy/sell", methods=["POST"])
@approved_required
def economy_sell():
    _check_csrf()
    uid = _discord_user()["id"]
    item = request.form.get("item", "").lower()
    try:
        qty = int(request.form.get("quantity", "1"))
    except ValueError:
        qty = 0
    if item not in botmod.SHOP_BY_ID or qty < 1:
        flash("Unknown item or invalid quantity.", "error")
        return redirect(url_for("dashboard.economy_home"))
    if not botmod._remove_item_sync(uid, item, qty):
        flash("You don't have that many.", "error")
        return redirect(url_for("dashboard.economy_home"))
    refund = (botmod.SHOP_BY_ID[item]["price"] // 2) * qty
    new_bal = botmod._add_balance_sync(uid, refund)
    flash(f"Sold {qty}x {botmod.SHOP_BY_ID[item]['name']} for {refund} {CURRENCY_TEXT}. Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/use", methods=["POST"])
@approved_required
def economy_use():
    _check_csrf()
    uid = _discord_user()["id"]
    item = request.form.get("item", "").lower()
    if item != "chest":
        flash("That item can't be used.", "error")
        return redirect(url_for("dashboard.economy_home"))
    if not botmod._remove_item_sync(uid, "chest", 1):
        flash("You don't have a Mystery Chest.", "error")
        return redirect(url_for("dashboard.economy_home"))
    reward = random.randint(100, 1500)
    new_bal = botmod._add_balance_sync(uid, reward)
    flash(f"The chest held {reward} {CURRENCY_TEXT}! Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/pay", methods=["POST"])
@approved_required
def economy_pay():
    _check_csrf()
    uid = _discord_user()["id"]
    try:
        target_id = int(request.form.get("user_id", "0"))
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Invalid recipient or amount.", "error")
        return redirect(url_for("dashboard.economy_home"))
    if amount < 1:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard.economy_home"))
    if target_id == uid:
        flash("You can't pay yourself.", "error")
        return redirect(url_for("dashboard.economy_home"))
    target_member = get_member(target_id)
    if target_member is None or target_member.bot:
        flash("Invalid recipient — they need to be a member of the server.", "error")
        return redirect(url_for("dashboard.economy_home"))
    doc = botmod._get_econ_sync(uid)
    if doc["balance"] < amount:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard.economy_home"))
    botmod._add_balance_sync(uid, -amount)
    botmod._add_balance_sync(target_id, amount)
    flash(f"Paid {target_member.display_name} {amount} {CURRENCY_TEXT}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/leaderboard")
@approved_required
def economy_leaderboard():
    top = list(botmod.economy_col.find().sort("balance", botmod.DESCENDING).limit(10))
    rows = [{"position": i, "name": display_name_for(d["_id"]), "balance": d["balance"]} for i, d in enumerate(top, start=1)]
    return page("Richest Players", ECONOMY_LEADERBOARD_TMPL, rows=rows, currency=coin_img("coin2"))


@dash_bp.route("/economy/games")
@approved_required
def economy_games():
    doc = botmod._get_econ_sync(_discord_user()["id"])
    return page("Games", ECONOMY_GAMES_TMPL, balance=doc["balance"], currency=coin_img("coin2"))


@dash_bp.route("/economy/games/rps", methods=["POST"])
@approved_required
def economy_rps():
    _check_csrf()
    uid = _discord_user()["id"]
    choice = request.form.get("choice", "")
    try:
        bet = int(request.form.get("bet", "0"))
    except ValueError:
        bet = 0
    if choice not in ("rock", "paper", "scissors"):
        flash("Invalid choice.", "error")
        return redirect(url_for("dashboard.economy_games"))
    doc = botmod._get_econ_sync(uid)
    if bet and doc["balance"] < bet:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard.economy_games"))
    bot_choice = random.choice(["rock", "paper", "scissors"])
    beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if choice == bot_choice:
        result, delta = "It's a tie!", 0
    elif beats[choice] == bot_choice:
        result, delta = f"You win{f' {bet}' if bet else ''}!", bet
    else:
        result, delta = f"You lose{f' {bet}' if bet else ''}!", -bet
    new_bal = botmod._add_balance_sync(uid, delta) if bet else doc["balance"]
    flash(f"You: {choice} vs Bot: {bot_choice} — {result} Balance: {new_bal}.", "success" if delta >= 0 else "info")
    return redirect(url_for("dashboard.economy_games"))


@dash_bp.route("/economy/games/coinflip", methods=["POST"])
@approved_required
def economy_coinflip():
    _check_csrf()
    uid = _discord_user()["id"]
    side = request.form.get("side", "")
    try:
        bet = int(request.form.get("bet", "0"))
    except ValueError:
        bet = 0
    if side not in ("heads", "tails") or bet < 1:
        flash("Invalid side or bet.", "error")
        return redirect(url_for("dashboard.economy_games"))
    doc = botmod._get_econ_sync(uid)
    if doc["balance"] < bet:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard.economy_games"))
    outcome = random.choice(["heads", "tails"])
    won = outcome == side
    new_bal = botmod._add_balance_sync(uid, bet if won else -bet)
    flash(f"It landed on {outcome}! You {'won' if won else 'lost'} {bet} {CURRENCY_TEXT}. Balance: {new_bal}.",
          "success" if won else "info")
    return redirect(url_for("dashboard.economy_games"))


@dash_bp.route("/economy/games/slots", methods=["POST"])
@approved_required
def economy_slots():
    _check_csrf()
    uid = _discord_user()["id"]
    try:
        bet = int(request.form.get("bet", "0"))
    except ValueError:
        bet = 0
    if bet < 1:
        flash("Bet must be positive.", "error")
        return redirect(url_for("dashboard.economy_games"))
    doc = botmod._get_econ_sync(uid)
    if doc["balance"] < bet:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard.economy_games"))
    symbols = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    spin = [random.choice(symbols) for _ in range(3)]
    if spin[0] == spin[1] == spin[2]:
        delta = bet * (10 if spin[0] == "7️⃣" else 5)
        result = f"JACKPOT! You won {delta} {CURRENCY_TEXT}!"
    elif len(set(spin)) == 2:
        delta = bet
        result = f"Two match! You won {delta} {CURRENCY_TEXT}!"
    else:
        delta = -bet
        result = f"No match. You lost {bet} {CURRENCY_TEXT}."
    new_bal = botmod._add_balance_sync(uid, delta)
    flash(f"[ {' | '.join(spin)} ] {result} Balance: {new_bal}.", "success" if delta >= 0 else "info")
    return redirect(url_for("dashboard.economy_games"))


@dash_bp.route("/economy/games/guess", methods=["POST"])
@approved_required
def economy_guess():
    _check_csrf()
    uid = _discord_user()["id"]
    try:
        number = int(request.form.get("number", "0"))
        bet = int(request.form.get("bet", "0"))
    except ValueError:
        flash("Invalid input.", "error")
        return redirect(url_for("dashboard.economy_games"))
    if not (1 <= number <= 10) or bet < 1:
        flash("Number must be 1-10 and bet must be positive.", "error")
        return redirect(url_for("dashboard.economy_games"))
    doc = botmod._get_econ_sync(uid)
    if doc["balance"] < bet:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard.economy_games"))
    answer = random.randint(1, 10)
    won = number == answer
    new_bal = botmod._add_balance_sync(uid, bet * 10 if won else -bet)
    if won:
        flash(f"Correct! It was {answer}. You won {bet * 10} {CURRENCY_TEXT}! Balance: {new_bal}.", "success")
    else:
        flash(f"Wrong, it was {answer}. You lost {bet} {CURRENCY_TEXT}. Balance: {new_bal}.", "info")
    return redirect(url_for("dashboard.economy_games"))


# =====================================================================================
# TRYOUTS
# =====================================================================================

TRYOUTS_HOME_TMPL = """
<h1>📋 Tryouts</h1>

{% if is_tryouter %}
<div class="grid">
  <div class="stat">
    <div class="label">Weekly EP</div>
    <div class="value">{{ ep }}/{{ quota_target }}</div>
    <div class="muted">{% if excused %}🟢 Excused until {{ excuse_until.strftime('%Y-%m-%d %H:%M UTC') }}{% elif ep >= quota_target %}✅ Quota met{% else %}❌ Quota not met yet{% endif %}</div>
  </div>
  <div class="stat"><div class="label">Resets</div><div class="value" style="font-size:16px;">{{ reset_at.strftime('%a %Y-%m-%d %H:%M UTC') }}</div></div>
</div>
{% else %}
<p class="muted">You don't hold a tryouter role, so quota doesn't apply to you.</p>
{% endif %}

{% if can_check_ep %}
<h2>Check someone's EP</h2>
<div class="card">
<form method="get" action="{{ url_for('dashboard.tryouts_ep_lookup') }}">
  <div class="row">
    <div class="field"><label>Discord ID</label><input type="text" name="uid" required></div>
  </div>
  <button class="btn">Check</button>
</form>
</div>
{% endif %}

<h2>Active Tryouters ({{ tryouter_rows|length }})</h2>
<div class="card">
{% if tryouter_rows %}
<table><tbody>
{% for r in tryouter_rows %}<tr><td>{{ r.name }}</td>{% if can_check_ep %}<td><a href="{{ url_for('dashboard.tryouts_ep_check', uid=r.id) }}">EP →</a></td>{% endif %}</tr>{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody currently has a tryouter role.</p>{% endif %}
</div>

<div class="grid">
  {% if can_tdone %}<a class="card" href="{{ url_for('dashboard.tdone_form') }}"><strong>📝 Post Tryout Result</strong></a>{% endif %}
  {% if can_exclude %}<a class="card" href="{{ url_for('dashboard.tryouts_exclude') }}"><strong>🚫 Exclude / Include</strong></a>{% endif %}
  {% if can_manage_in %}<a class="card" href="{{ url_for('dashboard.tryouts_in') }}"><strong>🟢 Manage IN</strong></a>{% endif %}
</div>"""

TRYOUTS_EP_RESULT_TMPL = """
<h1>📋 {{ name }} — Weekly Quota</h1>
<div class="card">
<p>EP this week: <strong>{{ ep }}/{{ target }}</strong></p>
<p>Status: {% if excused %}🟢 Excused until {{ excuse_until.strftime('%Y-%m-%d %H:%M UTC') }}{% elif ep >= target %}✅ Quota met{% else %}❌ Quota not met yet{% endif %}</p>
<p class="muted">Resets: {{ reset_at.strftime('%a %Y-%m-%d %H:%M UTC') }}</p>
</div>
<div class="linkrow"><a href="{{ url_for('dashboard.tryouts_home') }}">← Back to Tryouts</a></div>"""

TRYOUTS_EXCLUDE_TMPL = """
<h1>🚫 Exclude / Include from /viewt</h1>
<div class="card">
{% if rows %}
<table><thead><tr><th>Tryouter</th><th>Status</th><th></th></tr></thead><tbody>
{% for r in rows %}
<tr>
  <td>{{ r.name }}</td>
  <td>{% if r.excluded %}<span class="pill denied">Excluded</span>{% else %}<span class="pill approved">Active</span>{% endif %}</td>
  <td>
    <form method="post" action="{{ url_for('dashboard.tryouts_exclude_toggle', uid=r.id) }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}">
      <button class="btn small {{ 'success' if r.excluded else 'secondary' }}">{{ 'Include' if r.excluded else 'Exclude' }}</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody currently has a tryouter role.</p>{% endif %}
</div>"""

TRYOUTS_IN_TMPL = """
<h1>🟢 Manage IN</h1>
<div class="card">
<h2 style="margin-top:0;">Put someone on IN</h2>
<form method="post" action="{{ url_for('dashboard.tryouts_in_set') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="row">
    <div class="field"><label>Tryouter Discord ID</label><input type="text" name="tryouter_id" required></div>
    <div class="field"><label>Days</label><input type="number" name="days" min="1" required></div>
    <div class="field"><label>Reason (optional)</label><input type="text" name="reason"></div>
  </div>
  <button class="btn">Set IN</button>
</form>
</div>

<h2>Currently on IN</h2>
<div class="card">
{% if rows %}
<table><thead><tr><th>Tryouter</th><th>Until</th><th>Reason</th><th></th></tr></thead><tbody>
{% for r in rows %}
<tr>
  <td>{{ r.name }}</td>
  <td class="muted">{{ r.in_until.strftime('%Y-%m-%d %H:%M UTC') }}</td>
  <td class="muted">{{ r.reason or '—' }}</td>
  <td>
    <form method="post" action="{{ url_for('dashboard.tryouts_in_end', uid=r.id) }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small danger">End IN</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody is currently on IN.</p>{% endif %}
</div>"""

TDONE_FORM_TMPL = """
<h1>📝 Post Tryout Result</h1>
<div class="card">
<div class="tabs">
{% for key, cfg in positions.items() %}
<a href="{{ url_for('dashboard.tdone_form', position=key) }}" class="{{ 'active' if key == position else '' }}">{{ cfg.label }}</a>
{% endfor %}
</div>
<form method="post" action="{{ url_for('dashboard.tdone_submit') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <input type="hidden" name="position" value="{{ position }}">
  <div class="field"><label>Player Discord ID</label><input type="text" name="player_id" required></div>
  <div class="row">
  {% for stat in config.stats %}
    <div class="field"><label>{{ stat }} (0-10)</label><input type="text" name="stat_{{ loop.index0 }}" placeholder="e.g. 7.5" required></div>
  {% endfor %}
  </div>
  <div class="field"><label>Feedback</label><textarea name="feedback" rows="4" required></textarea></div>
  <button class="btn">Post Result</button>
</form>
</div>"""


def _tryouter_ids():
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    ids = set()
    if guild:
        for role_id in botmod.TDONE_ALLOWED_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                ids.update(m.id for m in role.members)
    return ids


@dash_bp.route("/tryouts")
@approved_required
def tryouts_home():
    uid = _discord_user()["id"]
    is_tryouter = has_role(uid, botmod.TRYOUT_QUOTA_ROLE_IDS)
    ep, excused, excuse_until = None, False, None
    if is_tryouter:
        ep = botmod._get_quota_ep_sync(uid)
        in_doc = botmod._get_in_doc_sync(uid)
        now = datetime.now(timezone.utc)
        if in_doc and in_doc.get("in_until") and botmod._aware(in_doc["in_until"]) > now:
            excused, excuse_until = True, botmod._aware(in_doc["in_until"])

    excluded_ids = botmod._get_excluded_ids_sync()
    active_ids = _tryouter_ids() - excluded_ids
    tryouter_rows = sorted(({"id": tid, "name": display_name_for(tid)} for tid in active_ids), key=lambda r: r["name"].lower())

    return page("Tryouts", TRYOUTS_HOME_TMPL,
                is_tryouter=is_tryouter, ep=ep, quota_target=botmod.TRYOUT_QUOTA_EP,
                excused=excused, excuse_until=excuse_until, reset_at=botmod.next_quota_reset_at(),
                tryouter_rows=tryouter_rows,
                can_exclude=has_role(uid, botmod.VIEWT_EXCLUDE_PANEL_ROLE_IDS),
                can_manage_in=has_role(uid, botmod.ADDELO_ROLE_ID),
                can_tdone=has_role(uid, botmod.TDONE_ALLOWED_ROLE_IDS),
                can_check_ep=has_role(uid, botmod.TRYOUT_QUOTA_ROLE_IDS))


@dash_bp.route("/tryouts/ep")
@approved_required
def tryouts_ep_lookup():
    if not has_role(_discord_user()["id"], botmod.TRYOUT_QUOTA_ROLE_IDS):
        abort(403)
    try:
        uid = int(request.args.get("uid", "0"))
    except ValueError:
        flash("Invalid Discord ID.", "error")
        return redirect(url_for("dashboard.tryouts_home"))
    return redirect(url_for("dashboard.tryouts_ep_check", uid=uid))


@dash_bp.route("/tryouts/ep/<int:uid>")
@approved_required
def tryouts_ep_check(uid):
    if not has_role(_discord_user()["id"], botmod.TRYOUT_QUOTA_ROLE_IDS):
        abort(403)
    if not has_role(uid, botmod.TRYOUT_QUOTA_ROLE_IDS):
        flash(f"{display_name_for(uid)} doesn't hold a tryouter role, so quota doesn't apply to them.", "info")
        return redirect(url_for("dashboard.tryouts_home"))
    ep = botmod._get_quota_ep_sync(uid)
    in_doc = botmod._get_in_doc_sync(uid)
    now = datetime.now(timezone.utc)
    excused = bool(in_doc and in_doc.get("in_until") and botmod._aware(in_doc["in_until"]) > now)
    return page(f"{display_name_for(uid)} — EP", TRYOUTS_EP_RESULT_TMPL,
                name=display_name_for(uid), ep=ep, target=botmod.TRYOUT_QUOTA_EP, excused=excused,
                excuse_until=botmod._aware(in_doc["in_until"]) if excused else None, reset_at=botmod.next_quota_reset_at())


@dash_bp.route("/tryouts/exclude")
@approved_required
def tryouts_exclude():
    if not has_role(_discord_user()["id"], botmod.VIEWT_EXCLUDE_PANEL_ROLE_IDS):
        abort(403)
    excluded_ids = botmod._get_excluded_ids_sync()
    rows = sorted(
        ({"id": tid, "name": display_name_for(tid), "excluded": tid in excluded_ids} for tid in _tryouter_ids()),
        key=lambda r: r["name"].lower(),
    )
    return page("Exclude / Include Tryouters", TRYOUTS_EXCLUDE_TMPL, rows=rows)


@dash_bp.route("/tryouts/exclude/<int:uid>/toggle", methods=["POST"])
@approved_required
def tryouts_exclude_toggle(uid):
    _check_csrf()
    if not has_role(_discord_user()["id"], botmod.VIEWT_EXCLUDE_PANEL_ROLE_IDS):
        abort(403)
    excluded_ids = botmod._get_excluded_ids_sync()
    now_excluded = uid not in excluded_ids
    botmod._set_excluded_sync(uid, now_excluded)
    flash(f"{display_name_for(uid)} {'excluded from' if now_excluded else 'included back in'} /viewt.", "success")
    return redirect(url_for("dashboard.tryouts_exclude"))


@dash_bp.route("/tryouts/in")
@approved_required
def tryouts_in():
    if not has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    now = datetime.now(timezone.utc)
    rows = []
    for tid in _tryouter_ids():
        in_doc = botmod._get_in_doc_sync(tid)
        if in_doc and in_doc.get("in_until") and botmod._aware(in_doc["in_until"]) > now:
            rows.append({
                "id": tid, "name": display_name_for(tid), "in_until": botmod._aware(in_doc["in_until"]),
                "reason": in_doc.get("reason"),
            })
    rows.sort(key=lambda r: r["in_until"])
    return page("Manage IN", TRYOUTS_IN_TMPL, rows=rows)


@dash_bp.route("/tryouts/in/set", methods=["POST"])
@approved_required
def tryouts_in_set():
    _check_csrf()
    if not has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    try:
        tryouter_id = int(request.form.get("tryouter_id", "0"))
        days = int(request.form.get("days", "0"))
    except ValueError:
        flash("Invalid input.", "error")
        return redirect(url_for("dashboard.tryouts_in"))
    if days <= 0:
        flash("Days must be a positive number.", "error")
        return redirect(url_for("dashboard.tryouts_in"))
    reason = request.form.get("reason", "").strip() or None

    now = datetime.now(timezone.utc)
    existing = botmod._get_in_doc_sync(tryouter_id)
    if existing and existing.get("cooldown_until"):
        cooldown_until = botmod._aware(existing["cooldown_until"])
        if now < cooldown_until:
            flash(f"{display_name_for(tryouter_id)} is on IN cooldown until "
                  f"{cooldown_until.strftime('%Y-%m-%d %H:%M UTC')} and can't be put on IN again yet.", "error")
            return redirect(url_for("dashboard.tryouts_in"))

    in_until = now + timedelta(days=days)
    cooldown_until = in_until + timedelta(days=botmod.IN_COOLDOWN_DAYS)
    botmod._set_in_status_sync(tryouter_id, now, in_until, cooldown_until, _discord_user()["id"], reason)
    flash(f"{display_name_for(tryouter_id)} is excused from tryout quota until {in_until.strftime('%Y-%m-%d %H:%M UTC')}.", "success")
    return redirect(url_for("dashboard.tryouts_in"))


@dash_bp.route("/tryouts/in/end/<int:uid>", methods=["POST"])
@approved_required
def tryouts_in_end(uid):
    _check_csrf()
    if not has_role(_discord_user()["id"], botmod.ADDELO_ROLE_ID):
        abort(403)
    now = datetime.now(timezone.utc)
    existing = botmod._get_in_doc_sync(uid)
    if not existing or not existing.get("in_until") or botmod._aware(existing["in_until"]) <= now:
        flash(f"{display_name_for(uid)} isn't currently on IN.", "error")
        return redirect(url_for("dashboard.tryouts_in"))
    cooldown_until = now + timedelta(days=botmod.IN_COOLDOWN_DAYS)
    botmod._set_in_status_sync(uid, existing.get("in_since", now), now, cooldown_until,
                                _discord_user()["id"], existing.get("reason"))
    flash(f"{display_name_for(uid)}'s IN has been ended.", "success")
    return redirect(url_for("dashboard.tryouts_in"))


@dash_bp.route("/tryouts/tdone", methods=["GET"])
@approved_required
def tdone_form():
    if not has_role(_discord_user()["id"], botmod.TDONE_ALLOWED_ROLE_IDS):
        abort(403)
    position = request.args.get("position", "cf_wing")
    if position not in botmod.POSITION_STATS:
        position = "cf_wing"
    return page("Post Tryout Result", TDONE_FORM_TMPL, position=position,
                config=botmod.POSITION_STATS[position], positions=botmod.POSITION_STATS)


@dash_bp.route("/tryouts/tdone", methods=["POST"])
@approved_required
def tdone_submit():
    _check_csrf()
    user = _discord_user()
    if not has_role(user["id"], botmod.TDONE_ALLOWED_ROLE_IDS):
        abort(403)

    position = request.form.get("position", "")
    if position not in botmod.POSITION_STATS:
        flash("Invalid position.", "error")
        return redirect(url_for("dashboard.tdone_form"))
    config = botmod.POSITION_STATS[position]

    try:
        player_id = int(request.form.get("player_id", "0"))
    except ValueError:
        flash("Invalid player ID.", "error")
        return redirect(url_for("dashboard.tdone_form", position=position))

    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is None:
        flash("The bot isn't connected to the server right now.", "error")
        return redirect(url_for("dashboard.tdone_form", position=position))
    player = guild.get_member(player_id)
    if player is None:
        flash("Couldn't find that player in the server.", "error")
        return redirect(url_for("dashboard.tdone_form", position=position))

    ratings = []
    for i, stat_name in enumerate(config["stats"]):
        raw = request.form.get(f"stat_{i}", "").strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            flash(f"'{raw}' isn't a valid number for {stat_name}.", "error")
            return redirect(url_for("dashboard.tdone_form", position=position))
        if not (0 <= value <= 10):
            flash(f"{stat_name} must be between 0 and 10 (got {value}).", "error")
            return redirect(url_for("dashboard.tdone_form", position=position))
        ratings.append((stat_name, value))

    feedback = request.form.get("feedback", "").strip()
    if not feedback:
        flash("Feedback is required.", "error")
        return redirect(url_for("dashboard.tdone_form", position=position))

    overall = round(sum(v for _, v in ratings) / len(ratings), 1)
    tier_name, role_id = botmod.get_position_rank(position, overall)
    rank_role_pool = botmod.get_position_rank_role_ids(position)
    host_member = guild.get_member(user["id"])

    role_note = None
    new_role_name = None

    async def _assign_role_and_post():
        nonlocal role_note, new_role_name
        new_role = None
        if role_id:
            try:
                new_role = guild.get_role(role_id)
                if new_role is None:
                    role_note = "Rank role not found on this server — couldn't assign it."
                else:
                    roles_to_remove = [r for r in player.roles if r.id in rank_role_pool and r.id != role_id]
                    if roles_to_remove:
                        await player.remove_roles(*roles_to_remove, reason="Tryout result — rank updated (dashboard)")
                    if new_role not in player.roles:
                        await player.add_roles(new_role, reason="Tryout result — rank assigned (dashboard)")
                    new_role_name = new_role.name
            except discord.Forbidden:
                role_note = "Couldn't assign the rank role — check the bot's role position/permissions."
            except Exception as e:
                role_note = f"Couldn't assign the rank role due to an error: {e}"

        if tier_name is None:
            rank_display = "*Unranked (below 4.6 — no tier yet)*"
        else:
            rank_display = f"**{new_role_name or tier_name}**"

        text = botmod.build_tryout_result_text(
            player=player, host=host_member, position_label=config["label"],
            ratings=ratings, overall=overall, rank_display=rank_display, feedback=feedback,
        )
        await botmod.post_result(guild, text, channel_id=botmod.TRYOUT_RESULTS_CHANNEL_ID)

    try:
        run_coro(_assign_role_and_post())
    except Exception as e:
        flash(f"Couldn't post the tryout result: {e}", "error")
        return redirect(url_for("dashboard.tdone_form", position=position))

    today_count, total_count = botmod._record_tryout_host_sync(user["id"])
    try:
        host_text = botmod.build_tryout_host_stats_text(host_member, today_count, total_count)
        run_coro(botmod.post_result(guild, host_text, channel_id=botmod.TRYOUT_HOST_STATS_CHANNEL_ID))
    except Exception:
        pass
    botmod._increment_quota_ep_sync(user["id"])

    msg = f"✅ Tryout result posted — {player.display_name} scored {overall}/10."
    if role_note:
        msg += f" ⚠️ {role_note}"
    flash(msg, "success")
    return redirect(url_for("dashboard.tdone_form", position=position))


# =====================================================================================
# MATCHMAKING
# =====================================================================================

MATCHMAKING_TMPL = """
<h1>⚔️ Matchmaking</h1>
{% if not bot_online %}<div class="flash error">The bot isn't fully connected yet — try again in a moment.</div>{% endif %}
<div class="grid">
  <div class="card">
    <h2 style="margin-top:0;">🏆 Ranked ({{ ranked_queue|length }}/2)</h2>
    {% if ranked_queue %}<ul>{% for n in ranked_queue %}<li>{{ n }}</li>{% endfor %}</ul>{% else %}<p class="muted">Queue is empty.</p>{% endif %}
    {% if in_ranked %}
    <form method="post" action="{{ url_for('dashboard.matchmaking_leave') }}"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn secondary">Leave Queue</button></form>
    {% else %}
    <form method="post" action="{{ url_for('dashboard.matchmaking_join', mode='ranked') }}"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn">Find Ranked Duel</button></form>
    {% endif %}
  </div>
  <div class="card">
    <h2 style="margin-top:0;">🤝 Friendly ({{ friendly_queue|length }}/2)</h2>
    {% if friendly_queue %}<ul>{% for n in friendly_queue %}<li>{{ n }}</li>{% endfor %}</ul>{% else %}<p class="muted">Queue is empty.</p>{% endif %}
    {% if in_friendly %}
    <form method="post" action="{{ url_for('dashboard.matchmaking_leave') }}"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn secondary">Leave Queue</button></form>
    {% else %}
    <form method="post" action="{{ url_for('dashboard.matchmaking_join', mode='friendly') }}"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn">Find Friendly Duel</button></form>
    {% endif %}
  </div>
</div>
<p class="muted">Joining works the same as the buttons in Discord — if you're the second player, a private duel channel is created automatically and you'll be notified in the server.</p>
<script>setTimeout(function(){ location.reload(); }, 15000);</script>"""


async def _web_join_queue_async(guild, member, mode):
    queue = botmod.QUEUES[mode]
    lock = botmod.QUEUE_LOCKS[mode]
    joined_at = botmod.QUEUE_JOINED_AT[mode]

    already_in_queue, opponent_id = False, None
    async with lock:
        if member.id in queue:
            already_in_queue = True
        elif queue:
            opponent_id = queue.pop(0)
            joined_at.pop(opponent_id, None)
        else:
            queue.append(member.id)
            joined_at[member.id] = time.time()

    if already_in_queue:
        return {"status": "already_in_queue"}
    if opponent_id is None:
        await botmod.update_queue_panel()
        return {"status": "joined"}

    opponent = guild.get_member(opponent_id)
    if opponent is None or opponent.id == member.id:
        async with lock:
            queue.append(member.id)
            joined_at[member.id] = time.time()
        await botmod.update_queue_panel()
        return {"status": "joined"}

    channel = await botmod.create_duel_channel(guild, opponent, member, mode)
    if channel is None:
        async with lock:
            queue.append(opponent_id)
            joined_at[opponent_id] = time.time()
        await botmod.update_queue_panel()
        return {"status": "duel_failed"}

    try:
        await opponent.send(f"⚔️ Opponent found! Your duel: {channel.mention}")
    except Exception:
        pass
    await botmod.update_queue_panel()
    return {"status": "matched", "channel_name": channel.name}


async def _web_leave_queue_async(user_id):
    removed_from = []
    for mode in ("ranked", "friendly"):
        lock = botmod.QUEUE_LOCKS[mode]
        queue = botmod.QUEUES[mode]
        async with lock:
            if user_id in queue:
                queue.remove(user_id)
                botmod.QUEUE_JOINED_AT[mode].pop(user_id, None)
                removed_from.append(mode)
    if removed_from:
        await botmod.update_queue_panel()
    return removed_from


@dash_bp.route("/matchmaking")
@approved_required
def matchmaking():
    uid = _discord_user()["id"]
    return page("Matchmaking", MATCHMAKING_TMPL,
                ranked_queue=[display_name_for(u) for u in botmod.QUEUES["ranked"]],
                friendly_queue=[display_name_for(u) for u in botmod.QUEUES["friendly"]],
                in_ranked=uid in botmod.QUEUES["ranked"], in_friendly=uid in botmod.QUEUES["friendly"],
                bot_online=botmod.bot_ready_event.is_set())


@dash_bp.route("/matchmaking/join/<mode>", methods=["POST"])
@approved_required
def matchmaking_join(mode):
    _check_csrf()
    if mode not in ("ranked", "friendly"):
        abort(404)
    uid = _discord_user()["id"]
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is None:
        flash("The bot isn't connected to the server right now.", "error")
        return redirect(url_for("dashboard.matchmaking"))
    member = guild.get_member(uid)
    if member is None:
        flash("You need to be a member of the Discord server to queue up.", "error")
        return redirect(url_for("dashboard.matchmaking"))
    try:
        result = run_coro(_web_join_queue_async(guild, member, mode))
    except Exception as e:
        flash(f"Couldn't join the queue: {e}", "error")
        return redirect(url_for("dashboard.matchmaking"))

    if result["status"] == "already_in_queue":
        flash(f"You're already in the {mode} queue.", "info")
    elif result["status"] == "joined":
        flash(f"You joined the {mode} queue. We'll notify you in Discord when we find an opponent.", "success")
    elif result["status"] == "matched":
        flash(f"⚔️ Opponent found! Your duel channel: #{result['channel_name']}", "success")
    else:
        flash("Couldn't create the duel channel — contact an administrator.", "error")
    return redirect(url_for("dashboard.matchmaking"))


@dash_bp.route("/matchmaking/leave", methods=["POST"])
@approved_required
def matchmaking_leave():
    _check_csrf()
    uid = _discord_user()["id"]
    try:
        removed = run_coro(_web_leave_queue_async(uid))
    except Exception as e:
        flash(f"Couldn't leave the queue: {e}", "error")
        return redirect(url_for("dashboard.matchmaking"))
    if removed:
        flash(f"Left the {' and '.join(removed)} queue.", "success")
    else:
        flash("You're not currently in a queue.", "info")
    return redirect(url_for("dashboard.matchmaking"))


# =====================================================================================
# MODERATION (ban/warn DMs)
# =====================================================================================

MODERATION_TMPL = """
<h1>🟥 Moderation DMs</h1>
<p class="muted">{% if can_live %}You have full access.{% elif can_test %}You have test-role access — DMs still send for real.{% endif %}</p>
<div class="grid">
<div class="card">
<h2 style="margin-top:0;">Ban Notice</h2>
<form method="post" action="{{ url_for('dashboard.moderation_ban') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="field"><label>Member Discord ID</label><input type="text" name="member_id" required></div>
  <div class="field"><label>Reason</label><input type="text" name="reason" required></div>
  <button class="btn danger">Send Ban DM</button>
</form>
</div>
<div class="card">
<h2 style="margin-top:0;">Warn Notice</h2>
<form method="post" action="{{ url_for('dashboard.moderation_warn') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <div class="field"><label>Member Discord ID</label><input type="text" name="member_id" required></div>
  <div class="field"><label>Punishment</label><input type="text" name="punishment" placeholder="e.g. 2h mute" required></div>
  <div class="field"><label>Reason</label><input type="text" name="reason" required></div>
  <button class="btn" style="background:var(--warn);">Send Warn DM</button>
</form>
</div>
</div>"""


async def _send_dm_async(guild, member_id, text):
    member = guild.get_member(member_id)
    if member is None:
        try:
            member = await guild.fetch_member(member_id)
        except Exception:
            return False, "Couldn't find that member in the server."
    try:
        await member.send(text)
    except Exception:
        return False, f"Couldn't DM {member.display_name} — they may have DMs disabled."
    return True, f"Notice sent to {member.display_name}."


@dash_bp.route("/moderation")
@approved_required
def moderation():
    uid = _discord_user()["id"]
    can_live = has_role(uid, botmod.BANDM_ROLE_ID)
    can_test = has_role(uid, botmod.BANDM_TEST_ROLE_ID)
    if not (can_live or can_test):
        abort(403)
    return page("Moderation DMs", MODERATION_TMPL, can_live=can_live, can_test=can_test)


@dash_bp.route("/moderation/ban", methods=["POST"])
@approved_required
def moderation_ban():
    _check_csrf()
    uid = _discord_user()["id"]
    if not (has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID)):
        abort(403)
    try:
        member_id = int(request.form.get("member_id", "0"))
    except ValueError:
        flash("Invalid member ID.", "error")
        return redirect(url_for("dashboard.moderation"))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Reason is required.", "error")
        return redirect(url_for("dashboard.moderation"))
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is None:
        flash("The bot isn't connected right now.", "error")
        return redirect(url_for("dashboard.moderation"))
    try:
        ok, msg = run_coro(_send_dm_async(guild, member_id, botmod.build_ban_dm(reason)))
    except Exception as e:
        ok, msg = False, str(e)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard.moderation"))


@dash_bp.route("/moderation/warn", methods=["POST"])
@approved_required
def moderation_warn():
    _check_csrf()
    uid = _discord_user()["id"]
    if not (has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID)):
        abort(403)
    try:
        member_id = int(request.form.get("member_id", "0"))
    except ValueError:
        flash("Invalid member ID.", "error")
        return redirect(url_for("dashboard.moderation"))
    punishment = request.form.get("punishment", "").strip()
    reason = request.form.get("reason", "").strip()
    if not punishment or not reason:
        flash("Punishment and reason are both required.", "error")
        return redirect(url_for("dashboard.moderation"))
    guild = botmod.client.get_guild(botmod.GUILD_ID)
    if guild is None:
        flash("The bot isn't connected right now.", "error")
        return redirect(url_for("dashboard.moderation"))
    try:
        ok, msg = run_coro(_send_dm_async(guild, member_id, botmod.build_warn_dm(punishment, reason)))
    except Exception as e:
        ok, msg = False, str(e)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard.moderation"))


# =====================================================================================
# PUBLIC LANDING PAGE — served at site root "/" (NOT under /dashboard, and not
# gated by login). This is what blazing.devs.surf shows automatically now,
# replacing the old {"bot": "BLZ-T Matchmaking", "status": "ok"} JSON health
# check (that route lived in bot.py — see the comment left there). It reuses
# the same color/font scheme as the dashboard above for consistency, but
# with a plain top bar instead of the dashboard's hover-triggered side dock,
# since this page is public and doesn't need a big authenticated nav.
#
# Content below is transcribed from the server's own info channels (FAQ,
# level-role perks, staff/community role descriptions). Edit the HTML in
# LANDING_BODY directly to add/change sections later.
# =====================================================================================

PUBLIC_LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#05070d">
<title>{{ title }} · """ + SERVER_NAME + """</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Big+Shoulders:wght@700;900&family=JetBrains+Mono:wght@400;500;700&display=swap');
  :root {
    color-scheme: dark;
    --bg: #05070d; --surface: #0c111d; --surface-2: #131c30;
    --line: #1c2740; --line-bright: #2e4064;
    --text: #eef1f8; --text-dim: #7e89a8;
    --accent: #2f6bff; --accent-dim: #16224a;
    --danger: #ff3b4e; --warn: #ffb020; --info: #5ea8ff;
    --font-display: 'Big Shoulders', sans-serif;
    --font-body: 'JetBrains Mono', monospace;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0; font-family: var(--font-body); font-size: 15px; color: var(--text); min-height: 100vh;
    background-color: var(--bg);
    background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
                       linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
    background-size: 34px 34px;
  }
  a { color: var(--accent); text-decoration: none; }
  h1 { font-family: var(--font-display); font-weight: 900; font-size: 30px; line-height: 1.2; margin: 0 0 20px; color: var(--text); text-transform: uppercase; letter-spacing: .02em; }
  h1::before { content: "// "; font-family: var(--font-body); color: var(--accent); }
  h2 {
    font-family: var(--font-body); font-weight: 700; font-size: 12px; margin: 0 0 14px; color: var(--text-dim);
    letter-spacing: .1em; text-transform: uppercase; border-left: 3px solid var(--accent); padding-left: 10px;
  }
  .muted { color: var(--text-dim); }
  .btn {
    display: inline-block; background: var(--accent); color: var(--bg); border: 1px solid var(--accent);
    padding: 10px 18px; font-family: var(--font-body); font-size: 14px; cursor: pointer; font-weight: 700;
    text-transform: uppercase; letter-spacing: .05em; transition: opacity .1s;
  }
  .btn:hover { opacity: .85; text-decoration: none; }
  .btn.secondary { background: transparent; color: var(--text); border-color: var(--line-bright); }
  .btn.small { padding: 6px 11px; font-size: 12px; }
  .card {
    background: var(--surface); border: 1px solid var(--line); padding: 20px; position: relative;
  }
  .card::before, .card::after {
    content: ""; position: absolute; width: 9px; height: 9px; border: 2px solid var(--accent); opacity: .7; pointer-events: none;
  }
  .card::before { top: -1px; left: -1px; border-right: none; border-bottom: none; }
  .card::after { bottom: -1px; right: -1px; border-left: none; border-top: none; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
  .pill { display: inline-block; padding: 3px 10px 3px 8px; font-size: 12px; background: var(--surface-2); border-left: 3px solid var(--text-dim); text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim); }
  .chip {
    display: inline-block; background: var(--surface-2); border: 1px solid var(--line-bright); color: var(--accent);
    font-size: 13px; padding: 2px 8px; font-weight: 700;
  }
  /* --- top bar (public site nav — deliberately simpler than the dashboard's dock) --- */
  .topbar {
    position: sticky; top: 0; z-index: 50; background: rgba(5,7,13,.92); backdrop-filter: blur(6px);
    border-bottom: 1px solid var(--line);
  }
  .topbar-inner {
    max-width: 1080px; margin: 0 auto; padding: 14px 24px; display: flex; align-items: center; gap: 24px;
  }
  .brand {
    font-family: var(--font-display); font-weight: 900; font-size: 20px; letter-spacing: .03em;
    color: var(--text); text-transform: uppercase; white-space: nowrap;
  }
  .brand span { color: var(--accent); }
  .toplinks { display: flex; gap: 4px; flex-wrap: wrap; flex: 1; }
  .toplinks a {
    color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
    padding: 8px 12px; border: 1px solid transparent;
  }
  .toplinks a:hover { color: var(--text); border-color: var(--line); text-decoration: none; }
  .topuser { display: flex; align-items: center; gap: 10px; white-space: nowrap; }
  .topuser img { width: 24px; height: 24px; border: 1px solid var(--line-bright); }
  @media (max-width: 760px) {
    .toplinks { order: 3; width: 100%; overflow-x: auto; justify-content: flex-start; }
    .topbar-inner { flex-wrap: wrap; padding: 12px 16px; }
  }
  /* --- page content --- */
  main { max-width: 1080px; margin: 0 auto; padding: 0 24px 80px; }
  .hero { text-align: center; padding: 70px 20px 60px; }
  .hero h1 { font-size: 42px; }
  .hero p { max-width: 560px; margin: 0 auto 26px; }
  .hero .actions { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
  section { padding: 56px 0; border-top: 1px solid var(--line); }
  .section-intro { margin: -6px 0 22px; }
  .level-card { background: var(--surface); border: 1px solid var(--line); padding: 16px 18px; position: relative; }
  .level-card::before { content: ""; position: absolute; top: -1px; left: -1px; width: 9px; height: 9px; border: 2px solid var(--accent); border-right: none; border-bottom: none; opacity: .7; }
  .level-card .lvl-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
  .level-card .lvl-num { font-family: var(--font-display); font-weight: 900; font-size: 22px; color: var(--accent); }
  .level-card .lvl-role { font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: .03em; }
  .level-card ul { margin: 0; padding-left: 18px; color: var(--text-dim); font-size: 13.5px; line-height: 1.7; }
  .role-card h3 { margin: 0 0 8px; font-size: 15px; text-transform: uppercase; letter-spacing: .02em; }
  .role-card p { margin: 0; color: var(--text-dim); font-size: 13.5px; line-height: 1.6; }
  ul.plain { margin: 0; padding-left: 18px; color: var(--text-dim); font-size: 14px; line-height: 1.85; }
  footer { text-align: center; padding: 40px 20px 60px; color: var(--text-dim); font-size: 12.5px; }
  ::-webkit-scrollbar { width: 12px; height: 12px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--line-bright); }
  :root { --bg:#080808; --surface:#111; --surface-2:#191919; --line:#303030; --line-bright:#686868; --text:#f4f0e8; --text-dim:#9b968d; --accent:#d7ff3f; --accent-dim:#26300c; --font-display:'Big Shoulders',sans-serif; --font-body:'JetBrains Mono',monospace; }
  body { background-color:var(--bg); background-image:radial-gradient(circle at 85% 4%,rgba(215,255,63,.14),transparent 30%),linear-gradient(135deg,transparent 0 49%,rgba(255,255,255,.04) 50% 50.2%,transparent 50.4%),linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px); background-size:auto,auto,48px 48px; }
  .topbar { background:rgba(8,8,8,.88); border-bottom:1px solid var(--line-bright); }
  .topbar-inner { max-width:1280px; padding:18px 28px; } .brand { font-size:25px; } .brand span { color:var(--accent) !important; }
  .toplinks a:hover { color:var(--accent); border-color:var(--accent); }
  .topuser { position:relative; } .public-search { position:relative; display:flex; width:38px; height:38px; transition:width .25s; }
  .public-search::before { content:'⌕'; position:absolute; left:11px; top:6px; color:var(--accent); font-size:22px; z-index:1; pointer-events:none; }
  .public-search { width:clamp(190px,22vw,320px); }
  .public-search input { width:100%; margin:0; padding:0 10px 0 36px; border-color:var(--line-bright); }
  .public-search input { height:38px; background:var(--surface); color:var(--text); font:12px var(--font-body); border:1px solid var(--line-bright); outline:none; }
  .public-search .search-results { position:absolute; top:44px; right:0; width:240px; display:none; max-height:280px; overflow:auto; background:var(--surface); border:1px solid var(--line-bright); box-shadow:6px 6px 0 var(--accent); z-index:60; }
  .public-search .search-results.open { display:block; } .public-search .search-results a { display:block; padding:11px 13px; color:var(--text-dim); border-bottom:1px solid var(--line); font-size:12px; } .public-search .search-results a:hover { background:var(--accent); color:var(--bg); text-decoration:none; }
  main { max-width:1280px; padding:0 28px 100px; } .hero { min-height:75vh; display:flex; flex-direction:column; justify-content:center; align-items:center; padding:90px 20px 70px; position:relative; }
  .hero::after { content:'SCROLL TO EXPLORE'; position:absolute; bottom:22px; left:50%; color:var(--accent); font-size:10px; letter-spacing:.18em; transform:translateX(-50%); }
  .hero h1 { font-size:clamp(64px,14vw,180px); line-height:.78; letter-spacing:-.03em; max-width:1100px; } .hero h1::before { content:'↳ '; color:var(--accent); }
  section { padding:100px 0; border-top:1px solid var(--line-bright); } section > h2 { font-size:16px; color:var(--accent); border-left:0; border-top:1px solid var(--accent); padding:12px 0 0; max-width:260px; }
  section > h2 { position:sticky; top:82px; z-index:4; background:linear-gradient(var(--bg) 72%,transparent); padding-bottom:18px; }
  .card,.level-card { background:linear-gradient(145deg,rgba(255,255,255,.07),rgba(255,255,255,.015)); border-color:var(--line); box-shadow:5px 5px 0 rgba(215,255,63,.14); } .card:hover,.level-card:hover { transform:translate(-3px,-3px); box-shadow:9px 9px 0 var(--accent); transition:transform .2s,box-shadow .2s; }
  .grid { gap:22px; } .chip { background:var(--accent); color:var(--bg); border-color:var(--accent); } .btn { background:var(--accent); border-color:var(--accent); } .btn.secondary { border-color:var(--line-bright); }
  @media (max-width:760px) { .topbar-inner { padding:13px 16px; } .topuser { width:100%; justify-content:flex-end; flex-wrap:wrap; } .public-search { width:100%; flex-basis:100%; } .public-search .search-results { width:min(86vw,320px); } main { padding:0 16px 80px; } .hero { min-height:78vh; padding-top:70px; } .hero h1 { font-size:clamp(62px,18vw,120px); } section { padding:70px 0; } section > h2 { top:116px; } }
  .scroll-progress { position:fixed; top:0; left:0; width:100%; height:4px; z-index:100; background:var(--accent); transform:scaleX(0); transform-origin:left; }
  .hero { min-height:100vh; overflow:hidden; isolation:isolate; } .hero::before { content:'BLAZE / PLAY / COMMUNITY'; position:absolute; top:18%; left:2%; color:var(--accent); font-size:10px; letter-spacing:.2em; writing-mode:vertical-rl; opacity:.65; z-index:-1; }
  .hero h1,.hero p,.hero .actions { transition:transform .2s linear,opacity .2s linear; } .hero h1 { text-shadow:12px 12px 0 rgba(215,255,63,.08); }
  main section { opacity:0; transform:translateY(42px); transition:opacity .8s ease,transform 1s cubic-bezier(.2,.7,.2,1); } main section.is-visible { opacity:1; transform:none; }
  main section .grid > * { opacity:0; transform:translateY(24px); transition:opacity .7s ease,transform .8s cubic-bezier(.2,.7,.2,1); } main section.is-visible .grid > * { opacity:1; transform:none; } main section.is-visible .grid > *:nth-child(2){transition-delay:.08s} main section.is-visible .grid > *:nth-child(3){transition-delay:.16s} main section.is-visible .grid > *:nth-child(4){transition-delay:.24s}
  #levels { position:relative; } #levels::before { content:'02'; position:absolute; right:0; top:72px; color:var(--accent); font:900 120px var(--font-display); opacity:.08; }
  @media (prefers-reduced-motion:reduce) { main section,main section .grid > * { opacity:1; transform:none; transition:none; } }
  .cinema-scroll { position:relative; height:100vh; }
  .cinema-world { position:sticky; top:0; height:100vh; overflow:hidden; perspective:1400px; }
  .cinema-world::before,.cinema-world::after { content:''; position:fixed; inset:0; pointer-events:none; z-index:-1; }
  .cinema-world::before { background:radial-gradient(ellipse at 50% 115%,rgba(215,255,63,.18),transparent 42%),linear-gradient(120deg,transparent 0 48%,rgba(215,255,63,.06) 49%,transparent 50%); transform:scale(1.3); }
  .cinema-world::after { background:linear-gradient(90deg,rgba(8,8,8,.9),transparent 22%,transparent 78%,rgba(8,8,8,.9)),linear-gradient(0deg,rgba(8,8,8,.72),transparent 18%,transparent 82%,rgba(8,8,8,.65)); }
  .cinema-orbit { position:fixed; width:62vw; height:62vw; max-width:900px; max-height:900px; left:50%; top:52%; border:1px solid rgba(215,255,63,.22); border-radius:50%; transform:translate(-50%,-50%) rotateX(68deg) rotateZ(var(--orbit,0deg)); pointer-events:none; z-index:-1; box-shadow:0 0 120px rgba(215,255,63,.08); }
  .cinema-orbit::before,.cinema-orbit::after { content:''; position:absolute; inset:10%; border:1px solid rgba(215,255,63,.11); border-radius:50%; } .cinema-orbit::after { inset:28%; }
  main { width:100vw; height:100vh; overflow:hidden; transform-style:preserve-3d; }
  .cinema-track { display:flex; height:100%; width:max-content; transform-style:preserve-3d; will-change:transform; }
  html,body { scrollbar-width:none; -ms-overflow-style:none; overflow-x:hidden; } html::-webkit-scrollbar,body::-webkit-scrollbar,.cinema-world::-webkit-scrollbar,.cinema-track::-webkit-scrollbar { display:none; width:0; height:0; }
  .cinema-track > section,.cinema-track > footer { position:relative; flex:0 0 100vw; width:100vw; height:100%; min-height:0; overflow:hidden; display:flex; flex-direction:column; justify-content:center; padding:clamp(70px,10vw,150px) clamp(28px,7vw,110px); border:0; transform-origin:50% 50%; }
  .cinema-track > section::before { content:attr(id); position:absolute; top:42px; right:clamp(28px,7vw,110px); color:var(--accent); font:10px var(--font-body); letter-spacing:.2em; opacity:.6; }
  .cinema-track > section.is-visible { transform:scale(1); }
  .cinema-camera { position:fixed; bottom:24px; left:clamp(16px,4vw,54px); z-index:20; display:flex; align-items:center; gap:12px; color:var(--text-dim); font-size:10px; letter-spacing:.14em; text-transform:uppercase; }
  .cinema-camera i { width:52px; height:1px; background:var(--accent); display:block; }
  .hero { z-index:2; } .hero::after { bottom:30px; }
  @media (max-width:760px) { .cinema-orbit { width:120vw; height:120vw; top:55%; } .cinema-track > section,.cinema-track > footer { padding:70px 20px; } .cinema-camera { bottom:76px; } }
  .cinema-scroll { height:auto; } .cinema-world { position:relative; top:auto; height:auto; min-height:100vh; overflow:visible; } main { width:auto; height:auto; min-height:100vh; overflow:visible; } .cinema-track { display:block; width:auto; height:auto; transform:none !important; }
  .cinema-track > section,.cinema-track > footer { width:auto; height:auto; min-height:100vh; overflow:visible; padding:clamp(70px,10vw,150px) clamp(28px,7vw,110px); }
  .cinema-track .grid { display:flex; flex-wrap:nowrap; gap:22px; overflow:hidden; width:100%; padding:6px 4px 20px; scroll-behavior:auto; overscroll-behavior-x:contain; }
  .cinema-track .grid > * { flex:0 0 min(76vw,390px); min-width:0; }
  .cinema-track .grid { scrollbar-width:none; } .cinema-track .grid::-webkit-scrollbar { display:none; width:0; height:0; }
  @media (max-width:760px) { .cinema-track > section,.cinema-track > footer { min-height:88vh; padding:70px 20px; } .cinema-track .grid { gap:14px; } .cinema-track .grid > * { flex-basis:84vw; } }
</style>
</head>
<body>
<div class="scroll-progress"></div>
<div class="cinema-scroll"><div class="cinema-world"><div class="cinema-orbit"></div><div class="cinema-camera"><i></i><span>scroll / navigate</span></div>
<div class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="#top"><span style="color:var(--accent);vertical-align:-3px;display:inline-block;"><svg width="18" height="18" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg></span> """ + SERVER_NAME.upper() + """</a>
    <nav class="toplinks">
      <a href="#faq">FAQ</a>
      <a href="#levels">XP &amp; Levels</a>
      <a href="#roles">Roles</a>
      <a href="#community">Community</a>
    </nav>
    <div class="topuser">
      <div class="public-search"><input type="search" placeholder="Search" aria-label="Search this page"><div class="search-results"></div></div>
      {% if user %}
        <img src="{{ user.avatar_url }}" alt="">
        <a class="btn small" href="{{ url_for('dashboard.home') }}">Dashboard</a>
        <a class="btn small secondary" href="{{ url_for('dashboard.logout') }}">Log out</a>
      {% else %}
        <a class="btn small" href="{{ url_for('dashboard.login') }}">Log in with Discord</a>
      {% endif %}
    </div>
  </div>
</div>
<main><div class="cinema-track">
{{ content|safe }}
</div></main>
</div></div>
<script>
(function () {
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var progress = document.querySelector('.scroll-progress'), hero = document.querySelector('.hero');
  function updateProgress() { if (progress) progress.style.transform = 'scaleX(' + (window.scrollY / Math.max(1, document.documentElement.scrollHeight - window.innerHeight)) + ')'; }
  document.querySelectorAll('.cinema-track .grid').forEach(function (scene) {
    scene.addEventListener('wheel', function (event) {
      var max = scene.scrollWidth - scene.clientWidth, next = scene.scrollLeft + event.deltaY;
      if (max <= 0 || (next <= 0 && event.deltaY < 0) || (next >= max && event.deltaY > 0)) return;
      event.preventDefault(); scene.scrollLeft = Math.max(0, Math.min(max, next));
    }, { passive:false });
  });
  updateProgress(); window.addEventListener('scroll', updateProgress, { passive:true });
  document.querySelectorAll('.search-shell, .public-search').forEach(function (shell) {
    var input = shell.querySelector('input'), results = shell.querySelector('.search-results');
    if (!input || !results) return;
    var targets = Array.prototype.slice.call(document.querySelectorAll('h1,h2,h3,.card,.stat,.level-card'));
    input.addEventListener('input', function () {
      var term = input.value.trim().toLowerCase(); results.innerHTML = '';
      if (!term) { results.classList.remove('open'); return; }
      targets.filter(function (el) { return el.textContent.toLowerCase().indexOf(term) >= 0; }).slice(0, 8).forEach(function (el, i) {
        if (!el.id) el.id = 'public-search-result-' + i;
        var link = document.createElement('a'); link.href = '#' + el.id; link.textContent = el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 70);
        link.addEventListener('click', function () { results.classList.remove('open'); input.value = ''; }); results.appendChild(link);
      });
      if (!results.children.length) { var empty = document.createElement('div'); empty.textContent = 'No matches'; empty.style.cssText = 'padding:12px;color:var(--text-dim);font-size:12px;'; results.appendChild(empty); }
      results.classList.add('open');
    });
    input.addEventListener('keydown', function (event) { if (event.key === 'Escape') { input.value = ''; results.classList.remove('open'); input.blur(); } });
    document.addEventListener('click', function (event) { if (!shell.contains(event.target)) results.classList.remove('open'); });
  });
})();
</script>
</body>
</html>"""


def public_page(title, body_template, **ctx):
    """Renders body_template as its OWN Jinja pass first (so {% if user %}
    etc. inside LANDING_BODY actually evaluate) before dropping the result
    into PUBLIC_LAYOUT — same two-stage pattern as page() above. Passing the
    raw template straight into `content` here was a bug: {{ }}/{% %} inside
    it never ran and showed up as literal text on the page."""
    user = _discord_user()
    body_html = render_template_string(body_template, user=user, **ctx)
    return render_template_string(PUBLIC_LAYOUT, title=title, content=body_html, user=user)


LANDING_BODY = """
<div class="hero" id="top">
<h1><span style="color:var(--accent);display:inline-block;vertical-align:-0.08em;"><svg width="0.75em" height="0.75em" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg></span> """ + SERVER_NAME + """</h1>
<p class="muted">Ranked duels, tryouts, and a community built around competitive play. Log in with Discord to check your ELO, queue for a match, or manage your tryout status — right from the browser.</p>
<div class="actions">
<a class="btn" href='""" + botmod.SUPPORT_SERVER_URL + """'>Support Discord Server</a>
{% if user %}
<a class="btn secondary" href="{{ url_for('dashboard.home') }}">Open Dashboard</a>
{% else %}
<a class="btn secondary" href="{{ url_for('dashboard.login') }}">Log in with Discord</a>
{% endif %}
</div>
</div>

<section id="faq">
<h2>Server FAQ</h2>
<p class="muted section-intro">You can find various information about the server itself here.</p>
<div class="grid">
  <div class="card">
    <strong>Q: How do I become a Question Helper?</strong>
    <p class="muted" style="margin-bottom:0;">The Question Helper team is primarily composed of individuals that actively partake in answering questions within the questions channel, and the requirements are similar to Banner Helpers, however with the added requirement of being knowledgeable about topics related to the game itself. They are likewise handpicked.</p>
  </div>
  <div class="card">
    <strong>Q: How do I get the Artist / Community Showcase role?</strong>
    <p class="muted" style="margin-bottom:0;">Please head to <span class="chip">#server-inquiries</span> to learn more about this.</p>
  </div>
  <div class="card">
    <strong>Q: How do I become staff?</strong>
    <p class="muted" style="margin-bottom:0;">Get handpicked by the Owner, or the best way is to apply in <span class="chip">#applications</span>.</p>
  </div>
</div>
</section>

<section id="levels">
<h2>XP &amp; Levels</h2>
<p class="muted section-intro">These are the role rewards you unlock gradually by staying active and reaching certain levels. Type <span class="chip">/rank</span> in bot commands to view your rank. Every checkpoint stacks — you keep all previous perks along with the new ones.</p>

<h3 style="font-family:var(--font-body);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--text);margin:26px 0 12px;">XP Boosts</h3>
<div class="card">
<ul class="plain">
  <li>Boosting the server gives you a global 5% XP multiplier.</li>
  <li>Sending messages in booster chat gives you a 10% XP multiplier.</li>
  <li>Upvoting the server on Bloxlink gives you a 10% XP multiplier (link in the Links tab).</li>
  <li>Upvoting Arcane bot gives you a 10% XP multiplier.</li>
  <li>The Gambler role, obtained through Unbelievaboat (LVL 50+ only), grants a 10% XP multiplier.</li>
  <li>A permanent XP boost can be obtained through Unbelievaboat for a 5% XP multiplier.</li>
  <li>The Donator role gives a 10% global XP multiplier, obtained by donating at least $30 or 3 battlepasses worth of giveaways.</li>
</ul>
</div>

<h3 style="font-family:var(--font-body);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--text);margin:30px 0 12px;">Level Rewards</h3>
<div class="grid">
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">05</span><span class="lvl-role">Demon Lord</span></div>
    <ul><li>External sticker permission</li><li>Access to <span class="chip">#media</span></li><li>Access to create suggestions</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">10</span><span class="lvl-role">Ace Eater</span></div>
    <ul><li>Unlocks sending images/GIFs outside media channels</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">15</span><span class="lvl-role">Godspeed</span></div>
    <ul><li>Access to stream in voice channels</li><li>AFK command access</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">20</span><span class="lvl-role">Eren</span></div>
    <ul><li>Access to General 2</li><li>Spoiler perms</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">25</span><span class="lvl-role">Slug Princess</span></div>
    <ul><li>Ability to change nickname</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">30</span><span class="lvl-role">Beatrice</span></div>
    <ul><li>Access to make polls</li><li>Embed permission</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">35</span><span class="lvl-role">Love Hashira</span></div>
    <ul><li>Reaction perms</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">40</span><span class="lvl-role">Sorcerer King</span></div>
    <ul><li>Immune to slowmode</li><li>Voice message perms in most channels</li><li class="muted">More to be added</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">—</span><span class="lvl-role">Unlockable Role</span></div>
    <ul><li>No mention limit</li><li>Activity perms in some channels</li><li class="muted">More to be added</li></ul></div>
  <div class="level-card"><div class="lvl-head"><span class="lvl-num">50</span><span class="lvl-role">Uta Queen</span></div>
    <ul><li>Lockdown immunity</li><li>Access to the Meals channel</li><li>Shown separately on the member list</li><li class="muted">More to be added</li></ul></div>
</div>
</section>

<section id="roles">
<h2>Server Roles</h2>

<h3 style="font-family:var(--font-body);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--text);margin:0 0 12px;">Server Management</h3>
<div class="grid">
  <div class="role-card card"><h3>Owner</h3><p>The highest authority of the server — manages everything, makes final decisions, oversees all operations, and ensures the community runs smoothly.</p></div>
  <div class="role-card card"><h3>Co-owner</h3><p>Assists the owner in managing the entire server, oversees all staff operations, handles major decisions, and ensures everything runs smoothly.</p></div>
  <div class="role-card card"><h3>Manager</h3><p>Oversees the entire server, keeping it fun, clean, and active.</p></div>
  <div class="role-card card"><h3>Head of Staff</h3><p>Oversees the entire server and staff team.</p></div>
  <div class="role-card card"><h3>Senior Administrator</h3><p>Supervises admins and moderators, manages high-level server operations, and assists in major decision-making.</p></div>
  <div class="role-card card"><h3>Administrator</h3><p>Oversees the moderation team and works with the moderator team.</p></div>
  <div class="role-card card"><h3>Senior Moderator</h3><p>Ensures the server is safe and enjoyable for all members.</p></div>
  <div class="role-card card"><h3>Moderator</h3><p>Ensures the server is safe and enjoyable for all members by moderating the chat.</p></div>
  <div class="role-card card"><h3>Junior Moderator</h3><p>Individuals on trial to become full-fledged moderators.</p></div>
</div>

<h3 style="font-family:var(--font-body);font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--text);margin:30px 0 12px;">Helper Team</h3>
<div class="grid">
  <div class="role-card card"><h3>Lead Helper</h3><p>Experienced members of the Question Helper team who assist and help newer helpers. They lead by example and keep things running smoothly within the helper group.</p></div>
  <div class="role-card card"><h3>Question Helper</h3><p>Question Helpers assist with server questions, game mechanics, and anything else members need help with — quickly and accurately.</p></div>
</div>
<p class="muted" style="margin-top:16px;font-size:13px;"><strong style="color:var(--text);">Staff role requirement:</strong> to become a staff member for """ + SERVER_NAME + """, you must patiently wait for applications from time to time.</p>
</section>

<section id="community">
<h2>Community Roles</h2>
<div class="grid">
  <div class="role-card card"><h3>GameNight Host</h3><p>Responsible for hosting game nights, engaging members with fun activities, managing lobbies, and ensuring everyone has a great time.</p></div>
  <div class="role-card card"><h3>Movie Night Host</h3><p>Hosts movie nights, handles movie suggestions, sets up watch parties, and ensures smooth streaming for all members.</p></div>
  <div class="role-card card"><h3>Server Booster</h3><p>Supports the server by boosting it. Perks: nickname permissions, pic perms, external emote &amp; sticker permissions, a custom role, access to the exclusive booster chat, and 1.5x more XP from chatting.</p></div>
  <div class="role-card card"><h3>Content Creator</h3><p>Officially recognized for the content they make. Requirements: at least one video uploaded in the past month, and 500+ subscribers/followers.</p></div>
  <div class="role-card card"><h3>Artist</h3><p>Artists deemed talented by staff can showcase their artwork in the server. Contact a staff member for your art piece to be reviewed and approved.</p></div>
</div>
</section>

<footer>More info gets added here over time — check back for updates.</footer>
"""


@app.route("/")
def public_landing():
    return public_page("Home", LANDING_BODY)


# =====================================================================================
# WIRING
# =====================================================================================

from threading import Thread


def _run_server():
    """Serves bot.py's Flask app (health check + dashboard blueprint, once
    mounted) on Render's PORT. Runs in a background thread so it doesn't
    block the Discord bot's own run loop in main.py."""
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def init_dashboard():
    """Call this once from main.py, before starting the Discord bot:

        from dashboard import init_dashboard
        init_dashboard()

    Mounts the dashboard blueprint onto bot.py's existing Flask app (`app`,
    aliased above to `botmod.app`) and starts serving it in a background
    thread — the SAME app/port Render's health check hits at "/", so nothing
    new needs to listen on a second port.
    """
    app.secret_key = DASHBOARD_SECRET_KEY
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.register_blueprint(dash_bp)
    logger.info(">>> [DASHBOARD] Web dashboard mounted at /dashboard")

    server_thread = Thread(target=_run_server, daemon=True)
    server_thread.start()


if __name__ == "__main__":
    print(
        "dashboard.py isn't meant to be run directly.\n"
        "Run main.py instead — it calls init_dashboard() then starts the bot."
    )
