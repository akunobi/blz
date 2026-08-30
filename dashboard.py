"""
dashboard.py — Web dashboard for the BLZ-T Discord bot.

This is a Flask Blueprint that plugs into the Flask app bot.py ALREADY runs
(the tiny one it uses for Render's health check), so everything still runs
as one process on one port. Slash commands in bot.py are untouched — this
just gives people a second way to do (almost) all of the same things from
a browser instead of typing commands.

WHAT IT DOES
------------
1. Discord login (OAuth2) instead of a bot invite / server nickname.
2. Every NEW login is put in a "pending" queue. Only the 3 admin Discord
   accounts below can approve or deny a pending login, from
   Admin -> Access Requests. Nobody else can use the rest of the dashboard
   until one of those 3 approves them. Approved status is remembered
   (stored in Mongo), so people don't need re-approval on every login.
3. Web pages standing in for the slash commands:

     /elo             -> /elo, /leaderboard
     /elo/<id>        -> /elo <player>, /addelo (staff)
     /elo/settings    -> /setelocolor, /resetelocolor, /setelobanner, /resetelobanner
     /economy         -> /balance, /daily, /work, /inventory, /sell, /use, /pay
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

# The only 3 people who can approve/deny dashboard login requests. They are
# always treated as approved themselves the moment they log in.
ADMIN_DISCORD_IDS = {1075463469865906216, 898579360720764999, 1375115979285073951}

DISCORD_API = "https://discord.com/api"
OAUTH_AUTHORIZE_URL = f"{DISCORD_API}/oauth2/authorize"
OAUTH_TOKEN_URL = f"{DISCORD_API}/oauth2/token"
OAUTH_USER_URL = f"{DISCORD_API}/users/@me"

dash_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

# One more collection in the SAME MongoDB database bot.py already connected
# to — nothing new to configure.
access_col = botmod.db["dashboard_access"]
access_col.create_index("status")


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
    if user_id in ADMIN_DISCORD_IDS:
        return "approved"
    doc = access_col.find_one({"_id": user_id})
    return doc["status"] if doc else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _discord_user():
            return redirect(url_for("dashboard.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def approved_required(view):
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
        if not user or user["id"] not in ADMIN_DISCORD_IDS:
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
<title>{{ title }} · BLZ-T Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Anton&family=Zilla+Slab:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap');

  :root {
    --bg: #16110c; --paper: #241c14; --paper-2: #2e2418; --line: #4a3c28;
    --line-bright: rgba(201,162,39,.55);
    --ink: #f1e7d3; --muted: #a4907a; --accent: #c8102e; --gold: #c9a227;
    --success: #4a7c4e; --warn: #c9a227; --danger: #c8102e;
    --font-display: 'Anton', sans-serif;
    --font-body: 'Zilla Slab', Georgia, serif;
    --shadow-deep: 0 10px 26px rgba(0,0,0,.45);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: var(--font-body); color: var(--ink); min-height: 100vh; position: relative;
    background-color: var(--bg);
    background-image:
      radial-gradient(circle at 18% -10%, rgba(200,16,46,.12), transparent 45%),
      radial-gradient(circle at 86% 0%, rgba(201,162,39,.10), transparent 42%);
  }
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: -2; opacity: .5; mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.4'/></svg>");
  }
  body::after {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: -1;
    box-shadow: inset 0 0 190px rgba(0,0,0,.6);
  }
  a { color: var(--gold); text-decoration: none; }
  a:hover { color: var(--accent); text-decoration: underline; }
  header.topbar {
    display: flex; align-items: center; justify-content: space-between; padding: 15px 26px; gap: 16px;
    background: linear-gradient(180deg, #1c1610, #130f0a); border-bottom: 3px solid var(--gold);
    position: sticky; top: 0; z-index: 10; flex-wrap: wrap; box-shadow: 0 4px 14px rgba(0,0,0,.4);
  }
  .brand {
    font-family: var(--font-display); font-weight: 400; letter-spacing: .03em; font-size: 22px; color: var(--ink);
    text-transform: uppercase; white-space: nowrap; display: flex; align-items: center; gap: 11px;
  }
  .brand::before {
    content: ""; width: 11px; height: 11px; border-radius: 50%; background: var(--accent);
    box-shadow: 0 0 0 3px rgba(200,16,46,.22), 0 0 0 1px var(--gold) inset;
    animation: seal-breathe 2.6s ease-in-out infinite; flex-shrink: 0;
  }
  @keyframes seal-breathe { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.18); opacity: .7; } }
  .brand span { color: var(--gold); }
  nav.mainnav { display: flex; gap: 4px; flex-wrap: wrap; font-family: var(--font-body); }
  nav.mainnav a {
    position: relative; color: var(--muted); padding: 8px 12px; font-size: 12px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .07em; transition: color .15s;
  }
  nav.mainnav a::after {
    content: ""; position: absolute; left: 10px; right: 10px; bottom: 5px; height: 2px; background: var(--gold);
    transform: scaleX(0); transform-origin: left; transition: transform .25s ease;
  }
  nav.mainnav a:hover { color: var(--ink); text-decoration: none; }
  nav.mainnav a:hover::after { transform: scaleX(1); }
  .badge { background: var(--accent); color: #fff5ee; border-radius: 3px; font-size: 10px; padding: 1px 6px; margin-left: 5px; font-weight: 700; display: inline-block; transform: rotate(-4deg); }
  .userbox { display: flex; align-items: center; gap: 10px; font-size: 13px; white-space: nowrap; font-weight: 600; }
  .userbox img { width: 30px; height: 30px; border-radius: 50%; border: 2px solid var(--gold); }
  main { max-width: 1080px; margin: 0 auto; padding: 30px 20px 70px; position: relative; z-index: 1; }
  .flash {
    padding: 13px 16px 13px 46px; border-radius: 3px; margin-bottom: 16px; font-size: 13.5px;
    position: relative; background: var(--paper); border: 1px solid var(--line); box-shadow: var(--shadow-deep);
  }
  .flash::before {
    content: ""; position: absolute; left: 13px; top: 50%; transform: translateY(-50%) rotate(-8deg);
    width: 15px; height: 21px; border-radius: 2px; box-shadow: 0 2px 4px rgba(0,0,0,.4);
  }
  .flash.success { border-left: 4px solid var(--success); }
  .flash.success::before { background: var(--success); }
  .flash.error { border-left: 4px solid var(--danger); }
  .flash.error::before { background: var(--danger); }
  .flash.info { border-left: 4px solid var(--gold); }
  .flash.info::before { background: var(--gold); }
  h1 { font-family: var(--font-display); font-weight: 400; font-size: 30px; margin: 0 0 20px; letter-spacing: .02em; text-transform: uppercase; text-shadow: 2px 2px 0 rgba(0,0,0,.4); }
  h2 { font-size: 13px; margin: 30px 0 14px; color: var(--gold); text-transform: uppercase; letter-spacing: .14em; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  h2::before { content: ""; width: 22px; height: 2px; background: var(--gold); display: inline-block; flex-shrink: 0; }
  h2::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  .card, .stat {
    background: linear-gradient(180deg, var(--paper), var(--paper-2));
    border: 1px solid var(--line); border-radius: 6px; padding: 20px; position: relative;
    box-shadow: var(--shadow-deep);
    background-image:
      radial-gradient(circle 3px at 11px 11px, rgba(201,162,39,.6) 98%, transparent),
      radial-gradient(circle 3px at calc(100% - 11px) 11px, rgba(201,162,39,.6) 98%, transparent),
      linear-gradient(180deg, var(--paper), var(--paper-2));
    animation: punch-in .4s cubic-bezier(.22,.9,.3,1.2) backwards;
  }
  .card { margin-bottom: 18px; }
  @keyframes punch-in { from { opacity: 0; transform: translateY(10px) scale(.97); } to { opacity: 1; transform: none; } }
  .grid > *:nth-child(1) { animation-delay: 0ms; } .grid > *:nth-child(2) { animation-delay: 45ms; }
  .grid > *:nth-child(3) { animation-delay: 90ms; } .grid > *:nth-child(4) { animation-delay: 135ms; }
  .grid > *:nth-child(5) { animation-delay: 180ms; } .grid > *:nth-child(6) { animation-delay: 225ms; }
  .grid > *:nth-child(n+7) { animation-delay: 260ms; }
  a.card { display: block; color: var(--ink); }
  a.card:hover { border-color: var(--line-bright); box-shadow: 0 0 0 1px var(--gold), var(--shadow-deep); text-decoration: none; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
  .stat .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .1em; font-weight: 600; }
  .stat .value { font-family: var(--font-display); font-size: 30px; font-weight: 400; margin-top: 6px; letter-spacing: .02em; color: var(--ink); }
  .value { display: inline-flex; }
  .value .flip-wrap { display: inline-flex; gap: 3px; align-items: baseline; }
  .value .flip-tile {
    display: inline-block; position: relative; min-width: .68em; height: 1.2em; line-height: 1.2em; text-align: center;
    padding: 0 2px; background: linear-gradient(180deg, #0f0c07, #1c160e); border-radius: 3px; border: 1px solid var(--line);
    color: var(--gold); box-shadow: inset 0 0 6px rgba(0,0,0,.65); backface-visibility: hidden;
  }
  .value .flip-tile::after { content: ""; position: absolute; left: 0; right: 0; top: 50%; height: 1px; background: rgba(0,0,0,.55); }
  .value .flip-tile.punct { background: transparent; border: none; box-shadow: none; color: var(--ink); min-width: auto; padding: 0; }
  .value .flip-tile.punct::after { content: none; }
  .value .flip-tile.flipping { animation: digit-flip .45s ease; }
  @keyframes digit-flip { 0% { transform: rotateX(0deg); } 45% { transform: rotateX(94deg); opacity: .35; } 55% { transform: rotateX(-94deg); opacity: .35; } 100% { transform: rotateX(0deg); } }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 11px 12px; border-bottom: 1px solid var(--line); }
  th { color: var(--gold); font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: .07em; border-bottom: 2px solid var(--gold); }
  tr { transition: background .15s; }
  tr:hover td { background: rgba(201,162,39,.05); }
  .avatar-sm { width: 26px; height: 26px; border-radius: 50%; vertical-align: middle; margin-right: 9px; border: 1px solid var(--line); }
  .progress { background: var(--paper-2); border: 1px solid var(--line); border-radius: 999px; height: 12px; overflow: hidden; box-shadow: inset 0 1px 3px rgba(0,0,0,.55); }
  .progress > div { background: linear-gradient(90deg, var(--accent), var(--gold)); height: 100%; position: relative; transition: width .6s cubic-bezier(.22,.9,.3,1.1); }
  .progress > div::after { content: ""; position: absolute; inset: 0; background: repeating-linear-gradient(-45deg, rgba(255,255,255,.14) 0 6px, transparent 6px 12px); }
  .btn {
    display: inline-block; background: var(--accent); color: #fff5ee; border: 2px solid #7a0a1c; border-radius: 5px;
    padding: 10px 20px; font-weight: 700; font-size: 12.5px; cursor: pointer; font-family: var(--font-body);
    text-transform: uppercase; letter-spacing: .05em; position: relative; overflow: hidden;
    box-shadow: 0 3px 0 #7a0a1c; transition: transform .1s ease, box-shadow .1s ease, filter .15s;
  }
  .btn:hover { filter: brightness(1.1); text-decoration: none; }
  .btn:active { transform: translateY(3px) rotate(-.6deg); box-shadow: 0 0 0 #7a0a1c; }
  .btn.secondary { background: var(--paper-2); color: var(--ink); border-color: var(--line); box-shadow: 0 3px 0 #17110a; }
  .btn.secondary:active { box-shadow: 0 0 0 #17110a; }
  .btn.small { padding: 7px 13px; font-size: 11px; }
  .btn.danger { background: var(--danger); border-color: #7a0a1c; box-shadow: 0 3px 0 #7a0a1c; }
  .btn.danger:active { box-shadow: 0 0 0 #7a0a1c; }
  .btn.success { background: var(--success); border-color: #2e4d31; color: #eafff0; box-shadow: 0 3px 0 #2e4d31; }
  .btn.success:active { box-shadow: 0 0 0 #2e4d31; }
  .ink-blot { position: absolute; border-radius: 50%; background: rgba(0,0,0,.35); transform: scale(0); pointer-events: none; animation: ink-spread .5s ease-out forwards; }
  @keyframes ink-spread { to { transform: scale(2.4); opacity: 0; } }
  input[type=text], input[type=number], input[type=password], textarea, select, input[type=file] {
    width: 100%; background: var(--paper-2); border: 1px solid var(--line); border-radius: 4px;
    padding: 10px 12px; color: var(--ink); font-size: 13.5px; margin-top: 5px; font-family: var(--font-body);
    box-shadow: inset 0 1px 4px rgba(0,0,0,.4); transition: border-color .15s, box-shadow .15s;
  }
  input:focus, textarea:focus, select:focus { outline: none; border-color: var(--gold); box-shadow: inset 0 1px 4px rgba(0,0,0,.4), 0 0 0 3px rgba(201,162,39,.18); }
  label { font-size: 12px; color: var(--muted); font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
  form.inline { display: inline-block; margin-right: 6px; }
  .field { margin-bottom: 16px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
  .row .field { flex: 1; min-width: 160px; }
  .muted { color: var(--muted); }
  .pill { display: inline-block; padding: 4px 12px 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; border: 1px solid transparent; }
  .pill::before { content: "●"; margin-right: 5px; font-size: 8px; vertical-align: middle; }
  .pill.pending { background: rgba(201,162,39,.12); color: var(--gold); border-color: rgba(201,162,39,.5); }
  .pill.approved { background: rgba(74,124,78,.15); color: #9fd1a3; border-color: rgba(74,124,78,.5); }
  .pill.denied { background: rgba(200,16,46,.12); color: #ff9caa; border-color: rgba(200,16,46,.5); }
  .empty { color: var(--muted); font-style: italic; padding: 18px 0; text-align: center; }
  .center { text-align: center; }
  .login-hero { text-align: center; padding: 70px 20px; }
  .login-hero h1 { font-size: 42px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }
  .tabs a { padding: 9px 16px; border-radius: 4px; background: var(--paper-2); border: 1px dashed var(--line); color: var(--muted); font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  .tabs a.active { background: rgba(200,16,46,.14); border-color: var(--accent); border-style: solid; color: var(--ink); }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 5px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--gold); }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
  }
</style>
</head>
<body>
<header class="topbar">
  <div class="brand"><span>BLZ-T</span> Dashboard</div>
  {% if status == "approved" %}
  <nav class="mainnav">
    <a href="{{ url_for('dashboard.home') }}">Home</a>
    <a href="{{ url_for('dashboard.elo_leaderboard') }}">ELO</a>
    <a href="{{ url_for('dashboard.economy_home') }}">Economy</a>
    <a href="{{ url_for('dashboard.tryouts_home') }}">Tryouts</a>
    <a href="{{ url_for('dashboard.matchmaking') }}">Matchmaking</a>
    {% if show_moderation %}<a href="{{ url_for('dashboard.moderation') }}">Moderation</a>{% endif %}
    {% if is_admin %}<a href="{{ url_for('dashboard.admin_access') }}">Admin{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}</a>{% endif %}
  </nav>
  {% endif %}
  <div class="userbox">
    {% if user %}
      <img src="{{ user.avatar_url }}" alt="">
      {{ user.username }}
      <a href="{{ url_for('dashboard.logout') }}" class="btn small secondary">Log out</a>
    {% else %}
      <a href="{{ url_for('dashboard.login') }}" class="btn small">Log in with Discord</a>
    {% endif %}
  </div>
</header>
<main>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="flash {{ category }}">{{ message }}</div>
  {% endfor %}
  {{ content|safe }}
</main>
<script>
(function () {
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Turn each ".value" readout into a scoreboard-style flip-tile display,
  // leaving the real text available to screen readers via aria-label.
  function buildFlipValue(el) {
    var raw = el.textContent;
    if (!raw || el.querySelector('.flip-wrap')) return;
    el.setAttribute('aria-label', raw);
    var wrap = document.createElement('span');
    wrap.className = 'flip-wrap';
    wrap.setAttribute('aria-hidden', 'true');
    var digitIndex = 0;
    Array.from(raw).forEach(function (ch) {
      var tile = document.createElement('span');
      if (/[0-9]/.test(ch)) {
        tile.className = 'flip-tile';
        if (!reduceMotion) {
          tile.classList.add('flipping');
          tile.style.animationDelay = (digitIndex * 45) + 'ms';
        }
        digitIndex++;
      } else {
        tile.className = 'flip-tile punct';
      }
      tile.textContent = ch;
      wrap.appendChild(tile);
    });
    el.textContent = '';
    el.appendChild(wrap);
  }
  document.querySelectorAll('.value').forEach(buildFlipValue);

  // Ink-stamp ripple wherever a ".btn" is clicked.
  if (!reduceMotion) {
    document.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('.btn');
      if (!btn) return;
      var rect = btn.getBoundingClientRect();
      var size = Math.max(rect.width, rect.height);
      var blot = document.createElement('span');
      blot.className = 'ink-blot';
      blot.style.width = blot.style.height = size + 'px';
      blot.style.left = (e.clientX - rect.left - size / 2) + 'px';
      blot.style.top = (e.clientY - rect.top - size / 2) + 'px';
      btn.appendChild(blot);
      window.setTimeout(function () { blot.remove(); }, 500);
    });
  }
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
        is_admin = uid in ADMIN_DISCORD_IDS
        show_moderation = has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID)
        pending_count = access_col.count_documents({"status": "pending"}) if is_admin else 0
    else:
        status, is_admin, show_moderation, pending_count = None, False, False, 0

    return render_template_string(
        LAYOUT, title=title, content=body_html, user=user,
        status=status, is_admin=is_admin, show_moderation=show_moderation, pending_count=pending_count,
    )


@dash_bp.app_errorhandler(400)
def _bad_request(e):
    return page("Something went wrong", f"""<div class='card center'><h1>⚠️ Something went wrong</h1>
                              <p class='muted'>{e.description or "Please try that again."}</p>
                              <a class="btn" href="{{{{ url_for('dashboard.home') }}}}">Back to dashboard</a></div>"""), 400


@dash_bp.app_errorhandler(403)
def _forbidden(e):
    return page("Forbidden", "<div class='card center'><h1>🚫 Forbidden</h1>"
                              "<p class='muted'>You don't have permission to view this page.</p></div>"), 403


@dash_bp.app_errorhandler(404)
def _not_found(e):
    return page("Not Found", "<div class='card center'><h1>🔍 Not Found</h1>"
                              "<p class='muted'>That page doesn't exist.</p></div>"), 404


# =====================================================================================
# AUTH ROUTES
# =====================================================================================

@dash_bp.route("/")
def home():
    user = _discord_user()
    if not user:
        return page("BLZ-T Dashboard", """
<div class="login-hero">
<h1>⚔️ BLZ-T Dashboard</h1>
<p class="muted">Manage ELO, the economy, tryouts, matchmaking and more — right from the browser.</p>
<a class="btn" href="{{ url_for('dashboard.login') }}">Log in with Discord</a>
<p class="muted" style="margin-top:16px;font-size:13px;">First-time logins need to be approved by a BLZ-T admin before the rest of the dashboard unlocks.</p>
</div>""")

    status = _access_status(user["id"])
    if status != "approved":
        return page("Pending Approval", """
<div class="card center" style="padding:50px 20px;">
{% if status == "denied" %}
<h1>🚫 Access denied</h1>
<p class="muted">An admin has denied dashboard access for your account. If you think this is a mistake, reach out to a BLZ-T admin.</p>
{% else %}
<h1>⏳ Waiting for approval</h1>
<p class="muted">Your login request has been sent. One of the 3 dashboard admins needs to approve it before you can use the rest of the dashboard. Check back soon!</p>
{% endif %}
</div>""", status=status)

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
    <div class="value">{{ balance }} 🪙</div>
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
  <a class="card" href="{{ url_for('dashboard.economy_shop') }}"><strong>🛒 Shop</strong><br><span class="muted">Buy items with your coins</span></a>
  <a class="card" href="{{ url_for('dashboard.economy_games') }}"><strong>🎲 Games</strong><br><span class="muted">RPS, coinflip, slots, guess</span></a>
  {% if is_staff_addelo %}<a class="card" href="{{ url_for('dashboard.tryouts_in') }}"><strong>🟢 Manage IN</strong><br><span class="muted">Excuse tryouters from quota</span></a>{% endif %}
  {% if is_moderator %}<a class="card" href="{{ url_for('dashboard.moderation') }}"><strong>🟥 Moderation DMs</strong><br><span class="muted">Send ban/warn notices</span></a>{% endif %}
</div>""",
                elo=row.elo, rank_name=rank_name, rank_emoji=rank_emoji, pct=pct, progress_label=progress_label,
                balance=econ_doc["balance"], is_tryouter=is_tryouter, ep=ep, quota_ep_target=botmod.TRYOUT_QUOTA_EP,
                queued_modes=queued_modes, is_staff_addelo=has_role(uid, botmod.ADDELO_ROLE_ID),
                is_moderator=has_role(uid, botmod.BANDM_ROLE_ID) or has_role(uid, botmod.BANDM_TEST_ROLE_ID))


@dash_bp.route("/login")
def login():
    if not all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI]):
        abort(500, "Discord login isn't configured yet — DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET / "
                    "DASHBOARD_REDIRECT_URI need to be set in the environment.")
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["next"] = request.args.get("next") or url_for("dashboard.home")
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

    now = datetime.now(timezone.utc)
    if user_id in ADMIN_DISCORD_IDS:
        access_col.update_one(
            {"_id": user_id},
            {"$set": {"username": username, "avatar": avatar_hash, "status": "approved"},
             "$setOnInsert": {"requested_at": now}},
            upsert=True,
        )
    else:
        existing = access_col.find_one({"_id": user_id})
        if existing is None:
            access_col.insert_one({
                "_id": user_id, "username": username, "avatar": avatar_hash,
                "status": "pending", "requested_at": now, "decided_at": None, "decided_by": None,
            })
            logger.info(f">>> [DASHBOARD] New access request from {username} ({user_id})")
        else:
            access_col.update_one({"_id": user_id}, {"$set": {"username": username, "avatar": avatar_hash}})

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
<h1>Access Requests</h1>
<p class="muted">Only the 3 configured admin accounts can see this page. Approve or deny people who've logged in to the dashboard.</p>

<div class="card">
<h2 style="margin-top:0;">Pending ({{ pending|length }})</h2>
{% if pending %}
<table><thead><tr><th>User</th><th>Requested</th><th></th></tr></thead><tbody>
{% for r in pending %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.requested_at.strftime('%Y-%m-%d %H:%M UTC') if r.requested_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='approve') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small success">Approve</button>
    </form>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='deny') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small danger">Deny</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">No pending requests.</p>{% endif %}
</div>

<div class="card">
<h2 style="margin-top:0;">Approved</h2>
{% if approved %}
<table><thead><tr><th>User</th><th>Decided</th><th></th></tr></thead><tbody>
{% for r in approved %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.decided_at.strftime('%Y-%m-%d %H:%M UTC') if r.decided_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='deny') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small danger">Revoke</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody approved yet.</p>{% endif %}
</div>

<div class="card">
<h2 style="margin-top:0;">Denied</h2>
{% if denied %}
<table><thead><tr><th>User</th><th>Decided</th><th></th></tr></thead><tbody>
{% for r in denied %}
<tr>
  <td>{{ r.username }} <span class="muted">({{ r._id }})</span></td>
  <td class="muted">{{ r.decided_at.strftime('%Y-%m-%d %H:%M UTC') if r.decided_at else '' }}</td>
  <td>
    <form class="inline" method="post" action="{{ url_for('dashboard.admin_access_action', uid=r._id, action='approve') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small success">Approve</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">Nobody denied.</p>{% endif %}
</div>"""


@dash_bp.route("/admin/access")
@admin_required
def admin_access():
    pending = list(access_col.find({"status": "pending"}).sort("requested_at", 1))
    approved = list(access_col.find({"status": "approved"}).sort("decided_at", -1))
    denied = list(access_col.find({"status": "denied"}).sort("decided_at", -1))
    return page("Access Requests", ADMIN_ACCESS_TMPL, pending=pending, approved=approved, denied=denied)


@dash_bp.route("/admin/access/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_access_action(uid, action):
    _check_csrf()
    if action not in ("approve", "deny"):
        abort(404)
    if uid in ADMIN_DISCORD_IDS:
        flash("Admins are always approved — nothing to change.", "info")
        return redirect(url_for("dashboard.admin_access"))
    status = "approved" if action == "approve" else "denied"
    access_col.update_one(
        {"_id": uid},
        {"$set": {"status": status, "decided_at": datetime.now(timezone.utc), "decided_by": _discord_user()["id"]}},
        upsert=True,
    )
    flash(f"Request for {uid} set to {status}.", "success")
    return redirect(url_for("dashboard.admin_access"))


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
<p><a href="{{ url_for('dashboard.elo_settings') }}">🎨 Card settings</a></p>"""

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
  <div class="stat"><div class="label">Balance</div><div class="value">{{ balance }} {{ currency }}</div></div>
  <div class="stat">
    <div class="label">Daily</div>
    {% if daily_ready %}
    <form method="post" action="{{ url_for('dashboard.economy_daily') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="btn small" style="margin-top:6px;">Claim {{ daily_amount }} {{ currency }}</button>
    </form>
    {% else %}<div class="muted" style="margin-top:6px;">Ready in {{ daily_wait }}</div>{% endif %}
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

<p><a href="{{ url_for('dashboard.economy_shop') }}">🛒 Shop</a> · <a href="{{ url_for('dashboard.economy_leaderboard') }}">📈 Leaderboard</a> · <a href="{{ url_for('dashboard.economy_games') }}">🎲 Games</a></p>"""

ECONOMY_SHOP_TMPL = """
<h1>🛒 Item Shop</h1>
<p class="muted">Balance: {{ balance }} {{ currency }}</p>
<div class="grid">
{% for it in items %}
<div class="card">
  <strong>{{ it.emoji }} {{ it.name }}</strong> — {{ it.price }} {{ currency }}<br>
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
{% for r in rows %}<tr><td>{{ r.position }}</td><td>{{ r.name }}</td><td>{{ r.balance }} {{ currency }}</td></tr>{% endfor %}
</tbody></table>
{% else %}<p class="empty">No one has any coins yet.</p>{% endif %}
</div>"""

ECONOMY_GAMES_TMPL = """
<h1>🎲 Games</h1>
<p class="muted">Balance: {{ balance }} {{ currency }}</p>
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
    <h2 style="margin-top:0;">Guess the Number (1-10, 8x payout)</h2>
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
    last_daily, last_work = doc.get("last_daily"), doc.get("last_work")
    daily_ready = not last_daily or botmod._aware(last_daily) + botmod.DAILY_COOLDOWN <= now
    work_ready = not last_work or botmod._aware(last_work) + botmod.WORK_COOLDOWN <= now
    daily_wait = None if daily_ready else botmod._fmt_remaining(botmod._aware(last_daily) + botmod.DAILY_COOLDOWN, now)
    work_wait = None if work_ready else botmod._fmt_remaining(botmod._aware(last_work) + botmod.WORK_COOLDOWN, now)
    inventory = [
        {"item": botmod.SHOP_BY_ID[i], "qty": q}
        for i, q in doc.get("inventory", {}).items() if q > 0 and i in botmod.SHOP_BY_ID
    ]
    return page("Economy", ECONOMY_HOME_TMPL, balance=doc["balance"], daily_ready=daily_ready, work_ready=work_ready,
                daily_wait=daily_wait, work_wait=work_wait, daily_amount=botmod.DAILY_AMOUNT,
                inventory=inventory, currency=botmod.CURRENCY)


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
        flash(f"Claimed your daily {botmod.DAILY_AMOUNT} {botmod.CURRENCY}! Balance: {new_bal}.", "success")
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
        flash(f"You {random.choice(jobs)} and earned {earned} {botmod.CURRENCY}! Balance: {new_bal}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/shop")
@approved_required
def economy_shop():
    doc = botmod._get_econ_sync(_discord_user()["id"])
    return page("Shop", ECONOMY_SHOP_TMPL, items=botmod.SHOP_ITEMS, balance=doc["balance"], currency=botmod.CURRENCY)


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
        flash(f"Need {cost} {botmod.CURRENCY}, you have {doc['balance']}.", "error")
        return redirect(url_for("dashboard.economy_shop"))
    botmod._add_balance_sync(uid, -cost)
    botmod._add_item_sync(uid, item, qty)
    flash(f"Bought {qty}x {it['emoji']} {it['name']} for {cost} {botmod.CURRENCY}.", "success")
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
    flash(f"Sold {qty}x {botmod.SHOP_BY_ID[item]['name']} for {refund} {botmod.CURRENCY}. Balance: {new_bal}.", "success")
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
    flash(f"The chest held {reward} {botmod.CURRENCY}! Balance: {new_bal}.", "success")
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
    flash(f"Paid {target_member.display_name} {amount} {botmod.CURRENCY}.", "success")
    return redirect(url_for("dashboard.economy_home"))


@dash_bp.route("/economy/leaderboard")
@approved_required
def economy_leaderboard():
    top = list(botmod.economy_col.find().sort("balance", botmod.DESCENDING).limit(10))
    rows = [{"position": i, "name": display_name_for(d["_id"]), "balance": d["balance"]} for i, d in enumerate(top, start=1)]
    return page("Richest Players", ECONOMY_LEADERBOARD_TMPL, rows=rows, currency=botmod.CURRENCY)


@dash_bp.route("/economy/games")
@approved_required
def economy_games():
    doc = botmod._get_econ_sync(_discord_user()["id"])
    return page("Games", ECONOMY_GAMES_TMPL, balance=doc["balance"], currency=botmod.CURRENCY)


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
    flash(f"It landed on {outcome}! You {'won' if won else 'lost'} {bet} {botmod.CURRENCY}. Balance: {new_bal}.",
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
        result = f"JACKPOT! You won {delta} {botmod.CURRENCY}!"
    elif len(set(spin)) == 2:
        delta = bet
        result = f"Two match! You won {delta} {botmod.CURRENCY}!"
    else:
        delta = -bet
        result = f"No match. You lost {bet} {botmod.CURRENCY}."
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
    new_bal = botmod._add_balance_sync(uid, bet * 8 if won else -bet)
    if won:
        flash(f"Correct! It was {answer}. You won {bet * 8} {botmod.CURRENCY}! Balance: {new_bal}.", "success")
    else:
        flash(f"Wrong, it was {answer}. You lost {bet} {botmod.CURRENCY}. Balance: {new_bal}.", "info")
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
<p><a href="{{ url_for('dashboard.tryouts_home') }}">← Back to Tryouts</a></p>"""

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
