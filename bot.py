# bot.py - BLZ-T Bot completo con AutoMod, Slash Commands, Panel de Logs y todas las APIs necesarias
import discord
from discord.ext import commands
import sqlite3
import threading
import os
import asyncio
import json as json_mod
import aiohttp
import time as time_gs
import urllib.request as urllib_req
import urllib.parse as urllib_parse
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler
import re as re_mod
import unicodedata as ud
import collections
import hashlib
import datetime

load_dotenv()

# --- CONFIGURACIÓN DE LOGGING ---
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('blz-bot')

# --- CONFIGURACIÓN FLASK ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_MODERATION_KEY = os.getenv("OPENAI_API_KEY", "")

# --- GOOGLE SHEETS EP TRACKER ---
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "19YQVEMF2NoDDLAdvqok8Nko3Hw1405G@AmneOcMdUdE")
SHEETS_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")
WARN_DM_ADMIN_IDS = [1898579360720764999, 10754634698659062161]
REGION_ROLES = [
    (1355062394547736673, "EU", 3, 4, 5),
    (1355062394547736675, "NA", 7, 8, 9),
    (1355062394547736674, "ASIA", 11, 12, 13)
]
SHEET_DATA_START_ROW = 15
SHEET_TAB = "Tracker"
_gs_token_cache = {"token": None, "exp": 0}

def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def _gs_get_token():
    now = int(time_gs.time())
    if _gs_token_cache["token"] and now < _gs_token_cache["exp"] - 60:
        return _gs_token_cache["token"]
    if not SHEETS_CREDS_JSON:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS no está configurado")
    try:
        info = json_mod.loads(SHEETS_CREDS_JSON)
    except Exception as e:
        raise RuntimeError(f"JSON de credenciales inválido: {e}")
    try:
        from google.oauth2.service_account import Credentials
        import requests as _requests
        creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        session = _requests.Session()
        from google.auth.transport.requests import Request as GRequest
        creds.refresh(GRequest(session=session))
        if not creds.token:
            raise RuntimeError("Token vacio tras refresh")
        token = creds.token
    except ImportError as e:
        raise RuntimeError(f"Dependencia faltante {e}. Revisa requirements.txt")
    except Exception as e:
        raise RuntimeError(f"Auth Google fallo {type(e).__name__}: {e}")
    _gs_token_cache["token"] = token
    _gs_token_cache["exp"] = now + 3000
    return token

def _gs_get(range_):
    enc = urllib_parse.quote(range_, safe="!")
    _url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib_req.Request(_url, headers={"Authorization": f"Bearer {_gs_get_token()}"})
    with urllib_req.urlopen(req, timeout=10) as r:
        return json_mod.loads(r.read()).get("values", [])

def _gs_put(range_, values):
    enc = urllib_parse.quote(range_, safe="!")
    _url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}?valueInputOption=USER_ENTERED"
    body = json_mod.dumps({"range": range_, "majorDimension": "ROWS", "values": values}).encode()
    req = urllib_req.Request(_url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {_gs_get_token()}",
        "Content-Type": "application/json"
    })
    with urllib_req.urlopen(req, timeout=10) as r:
        return json_mod.loads(r.read())

def _detect_region(member):
    role_ids = [r.id for r in getattr(member, "roles", [])]
    for role_id, label, cu, ce, cq in REGION_ROLES:
        if role_id in role_ids:
            return label, cu, ce, cq
    return None, None, None, None

def _sheet_add_ep(username, region, col_u, col_ep, col_qw):
    try:
        cl_u = col_letter(col_u)
        cl_ep = col_letter(col_ep)
        cl_qw = col_letter(col_qw)
        tab = SHEET_TAB
        col_data = _gs_get(f"'{tab}'!{cl_u}:{cl_u}200")
        target_row = None
        for i, row in enumerate(col_data):
            row_num = i + 1
            if row_num < SHEET_DATA_START_ROW:
                continue
            if row and str(row[0]).strip().lower() == username.lower():
                target_row = row_num
                break
        if target_row:
            ep_data = _gs_get(f"'{tab}'!{cl_ep}{target_row}")
            current = 0
            if ep_data and ep_data[0]:
                try:
                    current = int(ep_data[0][0])
                except (ValueError, TypeError):
                    current = 0
            new_val = current + 1
            _gs_put(f"'{tab}'!{cl_ep}{target_row}", [[str(new_val)]])
            logger.info(f"[SHEETS] {username} ({region}) fila {target_row}: EP {current} -> {new_val}")
            return True, f"EP updated! Total: {new_val}"
        else:
            occupied = [i+1 for i, row in enumerate(col_data) if i+1 >= SHEET_DATA_START_ROW and row and str(row[0]).strip()]
            next_row = (max(occupied) + 1) if occupied else SHEET_DATA_START_ROW
            _gs_put(f"'{tab}'!{cl_u}{next_row}", [[username]])
            _gs_put(f"'{tab}'!{cl_ep}{next_row}", [["1"]])
            _gs_put(f"'{tab}'!{cl_qw}{next_row}", [["0"]])
            logger.info(f"[SHEETS] Nuevo {username} ({region}) fila {next_row}: EP=1")
            return True, "Added to tracker! EP: 1"
    except Exception as e:
        logger.error(f"[SHEETS] Error para {username}: {e}")
        return False, f"{type(e).__name__}: {e}"

raw_id = os.getenv("CATEGORY_ID")
if not raw_id:
    logger.warning("Variable CATEGORY_ID no encontrada. Usando 0.")
    TARGET_CATEGORY_ID = 0
else:
    try:
        TARGET_CATEGORY_ID = int(raw_id)
    except ValueError:
        TARGET_CATEGORY_ID = 0
        logger.error("CATEGORY_ID no es numérico")

# --- SETUP DISCORD BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_messages = True
intents.reactions = True

# Prefix mantenido por compatibilidad de librerías, pero los comandos funcionales son Slash
client = commands.Bot(command_prefix="!", intents=intents)
bot_ready_event = threading.Event()

# --- BASE DE DATOS ---
def get_db_connection():
    DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                channel_name TEXT,
                author_name TEXT,
                author_avatar TEXT,
                content TEXT,
                author_id INTEGER,
                message_id INTEGER UNIQUE,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                components TEXT DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT,
                guild_id TEXT,
                reason TEXT,
                message_content TEXT DEFAULT NULL,
                message_link TEXT DEFAULT NULL,
                moderator_id TEXT,
                moderator_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mod_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT,
                guild_id TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                duration_seconds INTEGER,
                moderator_id TEXT,
                moderator_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB ERROR]: {e}")

def ensure_warnings_columns():
    conn = get_db_connection()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(warnings)").fetchall()]
        if 'message_content' not in cols:
            conn.execute("ALTER TABLE warnings ADD COLUMN message_content TEXT DEFAULT NULL")
            logger.info(">>> [DB MIGRATION] warnings.message_content added")
        if 'message_link' not in cols:
            conn.execute("ALTER TABLE warnings ADD COLUMN message_link TEXT DEFAULT NULL")
            logger.info(">>> [DB MIGRATION] warnings.message_link added")
        conn.commit()
    except Exception as e:
        logger.error(f"!!! [DB MIGRATION warnings]: {e}")
    finally:
        conn.close()

def ensure_author_id_column():
    try:
        conn = get_db_connection()
        cur = conn.execute("PRAGMA table_info(messages)").fetchall()
        cols = [r['name'] for r in cur]
        if 'author_id' not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN author_id INTEGER")
            conn.commit()
            logger.info(">>> [DB MIGRATION] Added column 'author_id'")
        if 'components' not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN components TEXT DEFAULT NULL")
            conn.commit()
            logger.info(">>> [DB MIGRATION] Added column 'components'")
        conn.close()
    except Exception as e:
        logger.error(f"!!! [DB CHECK ERROR]: {e}")

init_db()
ensure_warnings_columns()
ensure_author_id_column()

# --- AUTO MODERACION (Funciones Logicas) ---
_LEET_DIGITS = str.maketrans({'0':'o','1':'i','3':'e','4':'a','5':'s','6':'g','7':'t','8':'b'})

def _norm_text(text: str) -> str:
    t = ud.normalize('NFKD', text)
    t = "".join(c for c in t if not ud.combining(c))
    t = t.lower().translate(_LEET_DIGITS)
    t = re_mod.sub(r'[^a-z0-9 ]+', ' ', t)
    return re_mod.sub(r'\s+', ' ', t).strip()

def _norm_sym(text: str) -> str:
    t = ud.normalize('NFKD', text)
    t = "".join(c for c in t if not ud.combining(c))
    t = t.lower()
    t = t.replace('@','a').replace('$','s').replace('!','i').replace('+','t')
    t = t.translate(_LEET_DIGITS)
    return re_mod.sub(r'[^a-z0-9 ]+', ' ', t).strip()

def _norm_c32(text: str) -> str:
    return re_mod.sub(r'(.)\1{2,}', r'\1\1', _norm_text(text))

def levenshtein(a: str, b: str) -> int:
    if len(a) > len(b):
        a, b = b, a
    row = list(range(len(a) + 1))
    for cb in b:
        nr = [row[0] + 1]
        for i, ca in enumerate(a):
            nr.append(min(row[i] + (ca != cb), nr[-1] + 1, row[i+1] + 1))
        row = nr
    return row[-1]

def _join_single_chars(tokens):
    chunks, buf = [], []
    for t in tokens:
        if len(t) == 1:
            buf.append(t)
        else:
            if len(buf) >= 3:
                chunks.append("".join(buf))
            buf = []
    if len(buf) >= 3:
        chunks.append("".join(buf))
    return chunks

TOKEN_WHITELIST = frozenset((
    'retardant', 'retardation', 'cockpit', 'cocktail', 'cockatoo', 'cockerel', 'cockroach', 'cockatiel',
    'dickens', 'dickinson', 'dickson', 'heilung', 'heilongjiang', 'cumulative', 'cumulatively', 'cumbia',
    'cumulonimbus', 'cumulonimbi', 'cummings', 'cumulus'
))

EXACT_ONLY = frozenset(('spic', 'cock', 'dick', 'cum', 'gay', 'kys', 'fag', 'heil'))

BAD_SINGLE = (
    'nigger', 'nigga', 'niga', 'negger', 'faggot', 'fagot', 'fager', 'fag', 'retard',
    'chink', 'spic', 'wetback', 'kike', 'gook', 'coon', 'jigaboo', 'beaner', 'tranny',
    'porn', 'pron', 'cumshot', 'blowjob', 'handjob', 'pussy', 'cunt', 'cum', 'cock', 'cocksucker', 'dick', 'dickhead',
    'kys', 'nazi', 'kkk', 'heil', 'bitch', 'asshole', 'motherfucker', 'cuck', 'gay'
)

BAD_PHRASES = (
    'kill yourself', 'kill your self', 'hang yourself', 'hang your self', 'white power', 'heil hitler'
)

def contains_bad_word(text: str):
    ns = _norm_text(text)
    nl = _norm_sym(text)
    nc = _norm_c32(text)
    toks = set(ns.split() + nl.split() + nc.split())

    for phrase in BAD_PHRASES:
        pn = _norm_text(phrase)
        pat = r'(?<![a-z0-9])' + re_mod.escape(pn) + r'(?![a-z0-9])'
        if re_mod.search(pat, ns) or re_mod.search(pat, nl):
            return phrase

    for word in BAD_SINGLE:
        wn = _norm_text(word)
        exact = word in EXACT_ONLY
        for tok in toks:
            if tok in TOKEN_WHITELIST: continue
            if exact:
                if tok == wn: return word
            else:
                if tok == wn or tok.startswith(wn): return word

    for chunk in _join_single_chars(ns.split()):
        for word in BAD_SINGLE:
            if _norm_text(word) in chunk:
                return word

    for word in BAD_SINGLE:
        wn = _norm_text(word)
        if len(wn) <= 5: continue
        for tok in toks:
            if tok in TOKEN_WHITELIST: continue
            if len(tok) < 2: continue
            if tok[:2] != wn[:2]: continue
            if abs(len(tok) - len(wn)) >= 2: continue
            if levenshtein(tok, wn) <= 1: return word
    return None

spam_cache = collections.defaultdict(lambda: collections.defaultdict(collections.deque))
SPAM_WINDOW = 10
SPAM_MAX_SAME = 3
WARN_TIMEOUT_AT = 3

def add_warning(user_id, user_name, guild_id, reason, mod_id="BOT", mod_name="AutoMod", message_content=None, message_link=None):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO warnings (user_id, user_name, guild_id, reason, message_content, message_link, moderator_id, moderator_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(user_id), user_name, str(guild_id), reason, message_content, message_link, str(mod_id), mod_name)
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM warnings WHERE user_id=? AND guild_id=?", (str(user_id), str(guild_id))).fetchone()[0]
    conn.close()
    return count

def log_mod_action(user_id, user_name, guild_id, action, reason, duration=None, mod_id="BOT", mod_name="AutoMod"):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO mod_actions (user_id, user_name, guild_id, action, reason, duration_seconds, moderator_id, moderator_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(user_id), user_name, str(guild_id), action, reason, duration, str(mod_id), mod_name)
    )
    conn.commit()
    conn.close()

def get_recent_mod_action(user_id, guild_id, action):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM mod_actions WHERE user_id=? AND guild_id=? AND action=? ORDER BY timestamp DESC LIMIT 1",
        (str(user_id), str(guild_id), action)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

async def notify_warn_admins(member, guild, warn_count):
    if warn_count < 2: return
    for admin_id in WARN_DM_ADMIN_IDS:
        try:
            admin = await client.fetch_user(admin_id)
            embed = discord.Embed(
                title="Warning Alert",
                description=f"**{member.display_name}** ({member.id}) has reached {warn_count} warnings.\nServer: {guild.name}\nAction may be required.",
                color=0xF5A623
            )
            embed.set_footer(text="BLZ-T AutoMod")
            await admin.send(embed=embed)
            logger.info(f">>> [WARN DM] Notified admin {admin_id} about {member.display_name}")
        except Exception as e:
            logger.error(f"!!! [WARN DM] Failed to DM admin {admin_id}: {e}")

async def escalate_user(member, guild, warn_count, reason):
    uid = str(member.id)
    gid = str(guild.id)
    name = member.name
    logger.info(f">>> [ESCALATE] {name} | warn_count={warn_count}")
    had_timeout = get_recent_mod_action(uid, gid, 'timeout')
    had_ban3d = get_recent_mod_action(uid, gid, 'ban_3d')
    
    try:
        if had_ban3d:
            await member.ban(reason=f"AutoMod: permanent ban after 3d ban ({reason})", delete_message_days=0)
            log_mod_action(uid, name, gid, "ban_permanent", reason)
            logger.info(f">>> [AUTOMOD] PERMANENT BAN: {name}")
        elif had_timeout and warn_count >= 1:
            await member.ban(reason=f"AutoMod: 3-day ban after timeout | ({reason})", delete_message_days=0)
            log_mod_action(uid, name, gid, "ban_3d", reason, duration=259200)
            logger.info(f">>> [AUTOMOD] 3D BAN: {name}")
            async def unban():
                await asyncio.sleep(259200)
                try:
                    user = await client.fetch_user(int(uid))
                    await guild.unban(user, reason="AutoMod: 3d ban expired")
                except Exception as e:
                    logger.error(f"!!! [UNBAN ERROR]: {e}")
            asyncio.create_task(unban())
        elif warn_count >= WARN_TIMEOUT_AT:
            until = datetime.datetime.utcnow() + datetime.timedelta(days=1)
            await member.timeout(until, reason=f"AutoMod: {warn_count} warnings ({reason})")
            log_mod_action(uid, name, gid, "timeout", reason, duration=86400)
            logger.info(f">>> [AUTOMOD] TIMEOUT 1D: {name}")
    except discord.Forbidden:
        logger.error(f"!!! [ESCALATE] Missing permissions to act on {name}")
    except Exception as e:
        logger.error(f"!!! [ESCALATE ERROR {name}]: {e}")

async def save_message_to_db(message):
    try:
        conn = get_db_connection()
        comps = None
        if message.components:
            try:
                comps = json_mod.dumps([
                    {
                        'type': row.type.value if hasattr(row.type, 'value') else int(row.type),
                        'components': [{
                            'type': c.type.value if hasattr(c.type, 'value') else int(c.type),
                            'custom_id': getattr(c, 'custom_id', None),
                            'label': getattr(c, 'label', None),
                            'style': c.style.value if hasattr(getattr(c, 'style', None), 'value') else getattr(c, 'style', None),
                            'emoji': {'name': c.emoji.name, 'id': str(c.emoji.id) if c.emoji.id else None} if getattr(c, 'emoji', None) else None,
                            'url': getattr(c, 'url', None),
                            'disabled': getattr(c, 'disabled', False),
                        } for c in row.children]
                    } for row in message.components
                ])
            except Exception as e:
                logger.error(f"!!! [COMPONENTS SERIALIZE]: {e}")
        
        conn.execute("""
            INSERT OR IGNORE INTO messages 
            (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id, timestamp, components) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
            (message.channel.id, message.channel.name, message.author.name, 
            str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
            message.content, getattr(message.author, 'id', None), message.id, message.created_at.isoformat(), comps)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"!!! [SAVE ERROR]: {e}")

async def sync_category_history(limit=200):
    try:
        category = client.get_channel(TARGET_CATEGORY_ID)
        if not category:
            try:
                category = await client.fetch_channel(TARGET_CATEGORY_ID)
            except Exception:
                logger.error(f"Could not fetch category {TARGET_CATEGORY_ID}")
                return
        for c in getattr(category, 'channels', []):
            if isinstance(c, discord.TextChannel):
                logger.info(f">>> [HISTORY] Syncing #{c.name} (limit={limit})")
                try:
                    async for msg in c.history(limit=limit):
                        await save_message_to_db(msg)
                except Exception as e:
                    logger.error(f"!!! [HISTORY FETCH ERROR] #{c.name}: {e}")
        logger.info(">>> [HISTORY]: Sync complete.")
    except Exception as e:
        logger.error(f"!!! [HISTORY ERROR]: {e}")

# --- EVENTOS PRINCIPALES ---
@client.event
async def on_ready():
    logger.info(f">>> [DISCORD]: Conectado como {client.user}")
    logger.info(f">>> [DISCORD]: ID Categoria: {TARGET_CATEGORY_ID}")
    bot_ready_event.set()
    try:
        synced = await client.tree.sync()
        logger.info(f">>> [SLASH] Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"!!! [SLASH SYNC ERROR]: {e}")
    
    try:
        history_limit = int(os.getenv('HISTORY_LIMIT', '200'))
    except ValueError:
        history_limit = 200
    try:
        await sync_category_history(history_limit)
    except Exception as e:
        logger.error(f"!!! [HISTORY ERROR ON READY]: {e}")


# --- HANDLERS COMANDOS ---
async def handle_deadline_interaction(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=False)
    channel = interaction.channel
    mention_str = user.mention
    deadline_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    unix_ts = int(deadline_dt.timestamp())
    embed = discord.Embed(
        title="Deadline - Confirmation Required",
        description=f"{mention_str} you must confirm your availability within the next 24 hours.\n\nReact with ✅ to confirm.\nDeadline: <t:{unix_ts}:R>",
        color=0xF5A623
    )
    embed.set_footer(text="If you do not confirm within 24h, the ticket will be marked as ready to close.")
    
    try:
        sent = await channel.send(embed=embed)
        await sent.add_reaction("✅")
        logger.info(f">>> [DEADLINE] Slash command: sent for {user.display_name}")
    except Exception as e:
        logger.error(f"!!! [DEADLINE SEND ERROR]: {e}")
        await interaction.followup.send("Error al enviar el deadline", ephemeral=True)
        return
        
    async def watch():
        try:
            def check(reaction, u):
                return str(reaction.emoji) == '✅' and reaction.message.id == sent.id and user.id == u.id and not u.bot
            await client.wait_for('reaction_add', check=check, timeout=86400)
            confirmed_embed = discord.Embed(title="Confirmed", description=f"{mention_str} has confirmed their availability.", color=0x26C9B8)
            await sent.edit(embed=confirmed_embed)
            await sent.clear_reactions()
            logger.info(f">>> [DEADLINE] Confirmed by {user.display_name}")
        except asyncio.TimeoutError:
            close_embed = discord.Embed(title="Ticket Ready to Close", description=f"{mention_str} did not confirm within 24h.", color=0xFF6B6B)
            await channel.send(embed=close_embed)
            expired_embed = discord.Embed(title="Deadline Expired", description=f"{mention_str} did not respond.", color=0x888888)
            await sent.edit(embed=expired_embed)
            await sent.clear_reactions()
        except Exception as e:
            logger.error(f"!!! [DEADLINE WATCH ERROR]: {e}")
            
    asyncio.create_task(watch())
    await interaction.followup.send("Deadline enviado.", ephemeral=True)

async def handle_done_interaction(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    member = interaction.user
    region, col_u, col_ep, col_qw = _detect_region(member)
    if not region:
        await interaction.followup.send("No tienes rol de región (EU/NA/ASIA). Contacta a un moderador", ephemeral=True)
        return
    
    embed = discord.Embed(title="Updating tracker", description=f"Registrando EP para {member.display_name} ({region})", color=0xF5A623)
    msg = await interaction.channel.send(embed=embed)
    
    try:
        loop = asyncio.get_running_loop()
        success, msg_text = await asyncio.wait_for(
            loop.run_in_executor(None, _sheet_add_ep, member.display_name, region, col_u, col_ep, col_qw),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        success, msg_text = False, "Timeout al conectar con Google Sheets"
    except Exception as e:
        success, msg_text = False, f"{type(e).__name__}: {e}"
        
    if success:
        ok_embed = discord.Embed(title="EP Recorded!", description=f"{member.display_name} Region: {region}\n{msg_text}", color=0x26C9B8)
        ok_embed.set_footer(text="BLZ-T EP Tracker")
        await msg.edit(embed=ok_embed)
    else:
        await msg.edit(embed=discord.Embed(title="Error", description=f"{msg_text[:900]}", color=0xFF6868))
    await interaction.followup.send("Comando procesado.", ephemeral=True)

# --- SLASH COMMANDS ---
@client.tree.command(name="done", description="Registra tu EP semanal (debes tener rol EU/NA/ASIA)")
async def slash_done(interaction: discord.Interaction):
    await handle_done_interaction(interaction)

@client.tree.command(name="deadline", description="Envia un deadline de 24h a un usuario (solo staff)")
async def slash_deadline(interaction: discord.Interaction, user: discord.Member):
    allowed_roles = [1355062394547736675, 1355062394547736673, 1483349943962964068]
    if not any(role.id in allowed_roles for role in interaction.user.roles):
        await interaction.response.send_message("No tienes permiso para usar este comando", ephemeral=True)
        return
    await handle_deadline_interaction(interaction, user)


# --- ON_MESSAGE (Automod + logs, sin prefijos ! ) ---
@client.event
async def on_message(message):
    if message.author.bot:
        return

    # Guardar SIEMPRE los mensajes si el canal tiene categoría target
    # (antes de cualquier borrado de automod)
    if hasattr(message.channel, 'category') and message.channel.category and message.channel.category.id == TARGET_CATEGORY_ID:
        await save_message_to_db(message)

    guild = getattr(message.channel, 'guild', None)
    
    # 1. Automod - Bad Words
    if guild and message.content:
        found_word = contains_bad_word(message.content)
        if found_word:
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"!!! [AUTOMOD DELETE ERROR]: {e}")
            msg_link = f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}"
            warn_count = add_warning(
                message.author.id, message.author.name, guild.id, 
                reason=f"Bad word: {found_word}", 
                message_content=message.content[:500], 
                message_link=msg_link
            )
            logger.info(f">>> [AUTOMOD] {message.author.name} warned ({warn_count}) word={found_word}")
            try:
                await message.channel.send(f"{message.author.mention} Your message was removed. Warning {warn_count}", delete_after=8)
            except Exception:
                pass
            
            member = guild.get_member(message.author.id)
            if member:
                await notify_warn_admins(member, guild, warn_count)
                await escalate_user(member, guild, warn_count, f"Bad word: {found_word}")
            return

    # 2. Automod - Spam Detection
    now = time_gs.time()
    gid = str(guild.id) if guild else "0"
    uid = str(message.author.id)
    norm = re_mod.sub(r'\s+', '', message.content.lower().strip())
    h = hashlib.md5(norm.encode()).hexdigest()
    dq = spam_cache[gid][uid]
    
    while dq and now - dq[0][1] > SPAM_WINDOW:
        dq.popleft()
    dq.append((h, now))
    
    same_count = sum(1 for hh, _ in dq if hh == h)
    if same_count > SPAM_MAX_SAME:
        dq.clear()
        try:
            await message.delete()
        except Exception:
            pass
        msg_link = f"https://discord.com/channels/{gid}/{message.channel.id}/{message.id}"
        warn_count = add_warning(
            message.author.id, message.author.name, gid, 
            reason=f"Spam ({same_count} identical messages)", 
            message_content=message.content[:500], 
            message_link=msg_link
        )
        logger.info(f">>> [AUTOMOD SPAM] {message.author.name} warned ({warn_count})")
        try:
            await message.channel.send(f"{message.author.mention} Spam detected. Warning {warn_count}", delete_after=8)
        except Exception:
            pass
        
        if guild:
            member = guild.get_member(message.author.id)
            if member:
                await notify_warn_admins(member, guild, warn_count)
                await escalate_user(member, guild, warn_count, "Spam")
        return

    # 3. OpenAI Moderation
    if guild and message.content and OPENAI_MODERATION_KEY:
        async def openai_moderate(msg):
            if not OPENAI_MODERATION_KEY or not aiohttp: return
            text = msg.content.strip()
            if not text or len(text) < 3: return
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://api.openai.com/v1/moderations", headers={"Authorization": f"Bearer {OPENAI_MODERATION_KEY}", "Content-Type": "application/json"}, json={"input": text}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("results", [{}])[0]
                            scores = result.get("category_scores", {})
                            threshold = float(os.getenv("MOD_AI_THRESHOLD", "0.80"))
                            over_threshold = {c: scores[c] for c in scores if scores[c] >= threshold}
                            if over_threshold:
                                top_cat = max(over_threshold, key=over_threshold.get)
                                top_score = over_threshold[top_cat]
                                warn_count = add_warning(msg.author.id, msg.author.name, gid, reason=f"IA: {top_cat} ({top_score:.0%})", message_content=text[:500])
                                await msg.delete()
                                await msg.channel.send(f"{msg.author.mention} Tu mensaje fue eliminado por IA. Advertencia {warn_count}", delete_after=8)
            except Exception:
                pass
        asyncio.create_task(openai_moderate(message))
        
    await client.process_commands(message)

# --- RUTAS FLASK API Y DASHBOARD ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/channels")
def get_channels():
    if not bot_ready_event.is_set():
        return jsonify({}), 503
    async def get_channels_async():
        try:
            category = client.get_channel(TARGET_CATEGORY_ID)
            if not category:
                try:
                    category = await client.fetch_channel(TARGET_CATEGORY_ID)
                except:
                    pass
            if category:
                text_channels = [c for c in category.channels if isinstance(c, discord.TextChannel)]
                text_channels.sort(key=lambda x: x.position)
                return [{"id": str(c.id), "name": c.name} for c in text_channels]
            return []
        except:
            return []
            
    try:
        future = asyncio.run_coroutine_threadsafe(get_channels_async(), client.loop)
        channels = future.result(timeout=5)
        if channels:
            return jsonify(channels)
            
        conn = get_db_connection()
        db_chans = conn.execute("SELECT DISTINCT channel_name as name, channel_id as id FROM messages").fetchall()
        conn.close()
        return jsonify([{"id": str(row["id"]), "name": row["name"]} for row in db_chans])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/botinfo")
def bot_info():
    try:
        if not bot_ready_event.is_set():
            return jsonify({"ready": False}), 503
        name = str(client.user.name) if client.user else None
        uid = str(client.user.id) if client.user else None
        return jsonify({"ready": True, "name": name, "id": uid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/messages")
def get_messages():
    try:
        channel_id = request.args.get("channel_id")
        limit_q = request.args.get("limit")
        since_id_q = request.args.get("since_id")
        conn = get_db_connection()
        
        cid = None
        if channel_id:
            try:
                cid = int(channel_id)
            except ValueError:
                pass
                
        since_id = None
        if since_id_q:
            try:
                since_id = int(since_id_q)
            except ValueError:
                pass
                
        if since_id is not None and cid is not None:
            rows = conn.execute("SELECT * FROM messages WHERE channel_id=? AND message_id > ? ORDER BY timestamp ASC", (cid, since_id)).fetchall()
        elif cid is not None:
            try:
                lim = int(limit_q) if limit_q else 100
            except ValueError:
                lim = 100
            rows = conn.execute("SELECT * FROM (SELECT * FROM messages WHERE channel_id=? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC", (cid, lim)).fetchall()
        else:
            try:
                lim = int(limit_q) if limit_q else 50
            except ValueError:
                lim = 50
            rows = conn.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (lim,)).fetchall()
            
        conn.close()
        results = []
        for row in rows:
            d = dict(row)
            d['channel_id'] = str(d['channel_id']) if d.get('channel_id') else None
            d['message_id'] = str(d['message_id']) if d.get('message_id') else None
            d['author_id'] = str(d['author_id']) if d.get('author_id') else None
            results.append(d)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/send", methods=['POST'])
def send_message():
    data = request.json
    channel_id = data.get("channel_id")
    content = data.get("content")
    if not channel_id or not content:
        return jsonify({"error": "Faltan datos"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot desconectado"}), 503
        
    async def send_async():
        try:
            c_id = int(channel_id)
            channel = await client.fetch_channel(c_id)
            sent = await channel.send(content)
            # Bot messages are skipped by on_message (author.bot check),
            # so we must manually save them to the DB here.
            await save_message_to_db(sent)
            return {
                "success": True, 
                "message_id": str(sent.id), 
                "author_id": str(sent.author.id), 
                "author_name": sent.author.name, 
                "author_avatar": str(sent.author.avatar.url) if sent.author.avatar else None,
                "channel_name": channel.name, 
                "timestamp": sent.created_at.isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
            
    future = asyncio.run_coroutine_threadsafe(send_async(), client.loop)
    try:
        result = future.result(timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- NUEVO: ENDPOINT DEADLINE DESDE WEB --
@app.route("/api/deadline", methods=["POST"])
def api_deadline():
    data = request.json or {}
    target_id  = data.get("target_id")
    channel_id = data.get("channel_id")

    if not target_id or not channel_id:
        return jsonify({"error": "Faltan parámetros"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot no listo"}), 503

    async def trigger_deadline():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))

            deadline_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            unix_ts     = int(deadline_dt.timestamp())

            # Intentar obtener el nombre del usuario para el embed
            try:
                target_user = await client.fetch_user(int(target_id))
                mention_str = target_user.mention
            except Exception:
                mention_str = f"<@{target_id}>"

            embed = discord.Embed(
                title="Deadline - Confirmation Required",
                description=(
                    f"{mention_str} you must confirm your availability within the next 24 hours.\n\n"
                    f"React with ✅ to confirm.\nDeadline: <t:{unix_ts}:R>"
                ),
                color=0xF5A623
            )
            embed.set_footer(text="BLZ-T AutoMod · Web Panel")
            sent = await channel.send(embed=embed)
            await sent.add_reaction("✅")
            logger.info(f">>> [DEADLINE WEB] Sent for user {target_id} in channel {channel_id}")

            # Watcher igual que el slash command
            async def watch():
                try:
                    def check(reaction, u):
                        return (str(reaction.emoji) == '✅'
                                and reaction.message.id == sent.id
                                and str(u.id) == str(target_id)
                                and not u.bot)
                    await client.wait_for('reaction_add', check=check, timeout=86400)
                    confirmed = discord.Embed(
                        title="Confirmed",
                        description=f"{mention_str} has confirmed their availability.",
                        color=0x26C9B8
                    )
                    await sent.edit(embed=confirmed)
                    await sent.clear_reactions()
                    logger.info(f">>> [DEADLINE WEB] Confirmed by {target_id}")
                except asyncio.TimeoutError:
                    close_embed = discord.Embed(
                        title="Ticket Ready to Close",
                        description=f"{mention_str} did not confirm within 24h.",
                        color=0xFF6B6B
                    )
                    await channel.send(embed=close_embed)
                    expired = discord.Embed(
                        title="Deadline Expired",
                        description=f"{mention_str} did not respond.",
                        color=0x888888
                    )
                    await sent.edit(embed=expired)
                    await sent.clear_reactions()
                except Exception as e:
                    logger.error(f"!!! [DEADLINE WEB WATCH]: {e}")

            asyncio.create_task(watch())
        except Exception as e:
            logger.error(f"!!! [API DEADLINE ERROR]: {e}")

    asyncio.run_coroutine_threadsafe(trigger_deadline(), client.loop)
    return jsonify({"status": "Deadline enviado ✅"})

# ─── /done DESDE WEB ─────────────────────────────────────────────────────────
@app.route("/api/done", methods=["POST"])
def api_done():
    data       = request.json or {}
    user_id    = data.get("user_id")
    channel_id = data.get("channel_id")
    if not user_id or not channel_id:
        return jsonify({"error": "Faltan user_id y channel_id"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot no listo"}), 503

    async def run_done():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))
            guild = getattr(channel, 'guild', None) or (client.guilds[0] if client.guilds else None)
            member = guild.get_member(int(user_id)) if guild else None
            if not member:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    return {"error": "No se pudo encontrar al miembro en el servidor"}

            region, col_u, col_ep, col_qw = _detect_region(member)
            if not region:
                return {"error": "No tienes rol de región (EU/NA/ASIA). Contacta a un moderador."}

            embed = discord.Embed(
                title="Updating tracker",
                description=f"Registrando EP para {member.display_name} ({region})",
                color=0xF5A623
            )
            msg = await channel.send(embed=embed)

            loop = asyncio.get_running_loop()
            try:
                success, msg_text = await asyncio.wait_for(
                    loop.run_in_executor(None, _sheet_add_ep, member.display_name, region, col_u, col_ep, col_qw),
                    timeout=20.0
                )
            except asyncio.TimeoutError:
                success, msg_text = False, "Timeout al conectar con Google Sheets"
            except Exception as e:
                success, msg_text = False, f"{type(e).__name__}: {e}"

            if success:
                ok_embed = discord.Embed(
                    title="EP Recorded!",
                    description=f"{member.display_name} · {region}\n{msg_text}",
                    color=0x26C9B8
                )
                ok_embed.set_footer(text="BLZ-T EP Tracker")
                await msg.edit(embed=ok_embed)
                return {"success": True, "text": msg_text}
            else:
                await msg.edit(embed=discord.Embed(title="Error", description=msg_text[:900], color=0xFF6868))
                return {"error": msg_text}
        except Exception as e:
            logger.error(f"!!! [API DONE ERROR]: {e}")
            return {"error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(run_done(), client.loop)
        result = future.result(timeout=25)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── MOD PANEL: usuarios con historial de warns ───────────────────────────────
@app.route("/api/mod/users")
def mod_users():
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT user_id, user_name, guild_id,
                   COUNT(*) as warn_count, MAX(timestamp) as last_warn
            FROM warnings
            GROUP BY user_id, guild_id
            ORDER BY warn_count DESC, last_warn DESC
            LIMIT 100
        """).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            action = conn.execute(
                "SELECT * FROM mod_actions WHERE user_id=? AND guild_id=? ORDER BY timestamp DESC LIMIT 1",
                (d['user_id'], d['guild_id'])
            ).fetchone()
            d['last_action'] = dict(action) if action else None
            warns = conn.execute(
                "SELECT * FROM warnings WHERE user_id=? AND guild_id=? ORDER BY timestamp DESC LIMIT 10",
                (d['user_id'], d['guild_id'])
            ).fetchall()
            d['recent_warnings'] = [dict(w) for w in warns]
            results.append(d)
        conn.close()
        return jsonify(results)
    except Exception as e:
        logger.error(f"!!! [MOD USERS ERROR]: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/mod/action_log")
def mod_action_log():
    try:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM mod_actions ORDER BY timestamp DESC LIMIT 100").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/mod/warn_history/<user_id>")
def warn_history(user_id):
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM warnings WHERE user_id=? ORDER BY timestamp DESC", (user_id,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/mod/action", methods=["POST"])
def mod_do_action():
    data = request.json or {}
    action   = data.get("action")
    user_id  = data.get("user_id")
    user_name = data.get("user_name", "Unknown")
    guild_id = data.get("guild_id")
    reason   = data.get("reason", "Manual action from web panel")
    if not all([action, user_id, guild_id]):
        return jsonify({"error": "Missing parameters"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot not ready"}), 503

    async def execute_action():
        try:
            guild = None
            for g in client.guilds:
                if str(g.id) == str(guild_id):
                    guild = g; break
            if not guild and client.guilds:
                guild = client.guilds[0]
            member = guild.get_member(int(user_id)) if guild else None

            if action == "warn":
                count = add_warning(user_id, user_name, guild_id, reason, mod_id="WEB", mod_name="WebPanel")
                if member:
                    try:
                        e = discord.Embed(title="Warning Received",
                            description=f"You have received a warning in **{guild.name}**.\n\n**Reason:** {reason}",
                            color=0xF5A623)
                        e.set_footer(text="BLZ-T AutoMod")
                        await member.send(embed=e)
                    except Exception: pass
                return {"success": True, "warn_count": count}

            elif action == "timeout":
                if not member: return {"error": "Member not found in server"}
                until = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
                await member.timeout(until, reason=reason)
                log_mod_action(user_id, user_name, guild_id, "timeout", reason, duration=86400, mod_id="WEB", mod_name="WebPanel")
                return {"success": True}

            elif action == "kick":
                if not member: return {"error": "Member not found in server"}
                await member.kick(reason=reason)
                log_mod_action(user_id, user_name, guild_id, "kick", reason, mod_id="WEB", mod_name="WebPanel")
                return {"success": True}

            elif action == "ban":
                if not member: return {"error": "Member not found in server (or already banned)"}
                await member.ban(reason=reason, delete_message_days=0)
                log_mod_action(user_id, user_name, guild_id, "ban", reason, mod_id="WEB", mod_name="WebPanel")
                return {"success": True}

            elif action == "clear":
                conn = get_db_connection()
                conn.execute("DELETE FROM warnings WHERE user_id=? AND guild_id=?", (str(user_id), str(guild_id)))
                conn.commit(); conn.close()
                log_mod_action(user_id, user_name, guild_id, "clear_warns", reason, mod_id="WEB", mod_name="WebPanel")
                return {"success": True}

            return {"error": f"Unknown action: {action}"}
        except discord.Forbidden as e:
            return {"error": f"Permission denied: {e}"}
        except Exception as e:
            return {"error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(execute_action(), client.loop)
        return jsonify(future.result(timeout=10))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── MENCIONES: lookup de usuarios/roles ─────────────────────────────────────
@app.route("/api/mention_lookup", methods=["POST"])
def mention_lookup():
    data = request.json or {}
    user_ids = data.get("users", [])
    role_ids = data.get("roles", [])
    if not bot_ready_event.is_set():
        return jsonify({"users": {}, "roles": {}})

    async def lookup_async():
        users, roles = {}, {}
        for uid in user_ids[:20]:
            try:
                user = await client.fetch_user(int(uid))
                users[str(uid)] = {
                    "display": user.display_name,
                    "username": user.name,
                    "avatar": str(user.avatar.url) if user.avatar else None
                }
            except Exception:
                users[str(uid)] = {"display": f"User_{str(uid)[:4]}", "username": str(uid), "avatar": None}
        for guild in client.guilds:
            for rid in role_ids[:20]:
                try:
                    role = guild.get_role(int(rid))
                    if role:
                        color = f"#{role.color.value:06x}" if role.color.value else "#7289da"
                        roles[str(rid)] = {"name": role.name, "color": color}
                except Exception:
                    pass
        return users, roles

    try:
        future = asyncio.run_coroutine_threadsafe(lookup_async(), client.loop)
        u, r = future.result(timeout=10)
        return jsonify({"users": u, "roles": r})
    except Exception as e:
        logger.error(f"!!! [MENTION LOOKUP]: {e}")
        return jsonify({"users": {}, "roles": {}}), 500

# ─── MIEMBROS Y ROLES para autocompletado ────────────────────────────────────
@app.route("/api/members")
def get_members():
    if not bot_ready_event.is_set():
        return jsonify({"members": [], "roles": []}), 503

    async def fetch_async():
        members_list, roles_list, seen = [], [], set()
        for guild in client.guilds:
            for member in guild.members:
                if not member.bot and str(member.id) not in seen:
                    seen.add(str(member.id))
                    members_list.append({
                        "id": str(member.id),
                        "username": member.name,
                        "display": member.display_name,
                        "avatar": str(member.avatar.url) if member.avatar else None
                    })
            for role in guild.roles:
                if role.name != "@everyone":
                    color = f"#{role.color.value:06x}" if role.color.value else "#7289da"
                    roles_list.append({"id": str(role.id), "name": role.name, "color": color})
        return {"members": members_list[:300], "roles": roles_list[:50]}

    try:
        future = asyncio.run_coroutine_threadsafe(fetch_async(), client.loop)
        return jsonify(future.result(timeout=8))
    except Exception as e:
        logger.error(f"!!! [MEMBERS ERROR]: {e}")
        return jsonify({"error": str(e)}), 500

# ─── LOGS: endpoint mejorado con filtro de líneas ────────────────────────────
@app.route("/api/logs")
def api_logs():
    try:
        lines_param = request.args.get("lines", "200")
        lines = min(int(lines_param), 2000)
    except (ValueError, TypeError):
        lines = 200
    try:
        if not os.path.exists(log_file):
            return "No log file found", 404
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception as e:
        return str(e), 500

# ─── STATS BÁSICAS ─────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    try:
        conn = get_db_connection()
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        total_warns = conn.execute("SELECT COUNT(*) FROM warnings").fetchone()[0]
        total_actions = conn.execute("SELECT COUNT(*) FROM mod_actions").fetchone()[0]
        unique_warned = conn.execute("SELECT COUNT(DISTINCT user_id) FROM warnings").fetchone()[0]
        conn.close()
        return jsonify({
            "total_messages": total_msgs,
            "total_warnings": total_warns,
            "total_mod_actions": total_actions,
            "unique_warned_users": unique_warned
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── BORRAR MENSAJE ──────────────────────────────────────────────────────────
@app.route("/api/delete", methods=["POST"])
def delete_message():
    data = request.json or {}
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    if not channel_id or not message_id:
        return jsonify({"error": "Faltan channel_id y message_id"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot no listo"}), 503

    async def do_delete():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
            # Borrar de la base de datos también
            conn = get_db_connection()
            conn.execute("DELETE FROM messages WHERE message_id=?", (str(message_id),))
            conn.commit()
            conn.close()
            return {"success": True}
        except discord.NotFound:
            return {"error": "Mensaje no encontrado"}
        except discord.Forbidden:
            return {"error": "Sin permiso para borrar este mensaje"}
        except Exception as e:
            return {"error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(do_delete(), client.loop)
        return jsonify(future.result(timeout=10))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── EDITAR MENSAJE ──────────────────────────────────────────────────────────
@app.route("/api/edit", methods=["POST"])
def edit_message():
    data = request.json or {}
    channel_id  = data.get("channel_id")
    message_id  = data.get("message_id")
    new_content = data.get("content", "").strip()
    if not channel_id or not message_id or not new_content:
        return jsonify({"error": "Faltan channel_id, message_id o content"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot no listo"}), 503

    async def do_edit():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=new_content)
            # Actualizar en base de datos
            conn = get_db_connection()
            conn.execute("UPDATE messages SET content=? WHERE message_id=?",
                         (new_content, str(message_id)))
            conn.commit()
            conn.close()
            return {"success": True}
        except discord.NotFound:
            return {"error": "Mensaje no encontrado"}
        except discord.Forbidden:
            return {"error": "Solo puedes editar mensajes del bot"}
        except Exception as e:
            return {"error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(do_edit(), client.loop)
        return jsonify(future.result(timeout=10))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── REACCIONAR A MENSAJE ────────────────────────────────────────────────────
@app.route("/api/react", methods=["POST"])
def react_message():
    data = request.json or {}
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    emoji      = data.get("emoji", "👍")
    if not channel_id or not message_id:
        return jsonify({"error": "Faltan channel_id y message_id"}), 400
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot no listo"}), 503

    async def do_react():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            return {"success": True}
        except discord.NotFound:
            return {"error": "Mensaje no encontrado"}
        except discord.HTTPException as e:
            return {"error": f"Emoji inválido o error HTTP: {e}"}
        except Exception as e:
            return {"error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(do_react(), client.loop)
        return jsonify(future.result(timeout=10))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    client.run(TOKEN)
