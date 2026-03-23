import discord
import sqlite3
import threading
import os
import asyncio
import json as _json_mod
try:
    import aiohttp as _aiohttp
except ImportError:
    _aiohttp = None
import time as _time_gs
import urllib.request as _urllib_req
import urllib.parse as _urllib_parse
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_MODERATION_KEY  = os.getenv("OPENAI_API_KEY", "")  # opcional

# ── Google Sheets — EP Tracker (HTTP API pura, sin librerías externas) ────────
SHEET_ID           = os.getenv("GOOGLE_SHEET_ID", "19YQvEMF2NoDDLAdvqok8Nko3Hw14o5G9AmheOcMdUdE")
SHEETS_CREDS_JSON  = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")
WARN_DM_ADMIN_IDS  = [898579360720764999, 1075463469865906216]

# (role_id, label, col_username, col_eps, col_qw) — columnas 1-indexed
_REGION_ROLES = [
    (1355062394547736673, "EU",   3,  4,  5),
    (1355062394547736675, "NA",   7,  8,  9),
    (1355062394547736674, "ASIA", 11, 12, 13),
]
_SHEET_DATA_START_ROW = 15
_SHEET_TAB            = "Tracker"

# ── Token cache ──────────────────────────────────────────────────────────────
_gs_token_cache = {"token": None, "exp": 0}

def _col_letter(n):
    """Convierte número de columna (1-indexed) a letra de Sheets: 1→A, 3→C, 27→AA."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def _gs_get_token():
    """Obtiene OAuth2 access token usando google-auth (maneja JWT internamente)."""
    now = int(_time_gs.time())
    if _gs_token_cache["token"] and now < _gs_token_cache["exp"] - 60:
        return _gs_token_cache["token"]

    if not SHEETS_CREDS_JSON:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS no está configurado en Render")

    try:
        info = _json_mod.loads(SHEETS_CREDS_JSON)
    except Exception as e:
        raise RuntimeError(f"JSON de credenciales inválido: {e}")

    try:
        from google.oauth2.service_account import Credentials
        import requests as _requests
        creds = Credentials.from_service_account_info(info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets"
        ])
        session = _requests.Session()
        from google.auth.transport.requests import Request as GRequest
        creds.refresh(GRequest(session=session))
        if not creds.token:
            raise RuntimeError("Token vacío tras refresh")
        token = creds.token
    except ImportError as e:
        raise RuntimeError(f"Dependencia faltante: {e}. Revisa requirements.txt")
    except Exception as e:
        raise RuntimeError(f"Auth Google falló: {type(e).__name__}: {e}")

    _gs_token_cache["token"] = token
    _gs_token_cache["exp"]   = now + 3600
    return token

def _gs_get(range_):
    """GET de valores desde Google Sheets API v4."""
    _enc = _urllib_parse.quote(range_, safe="!'")
    _url = "https://sheets.googleapis.com/v4/spreadsheets/" + SHEET_ID + "/values/" + _enc
    req = _urllib_req.Request(_url, headers={"Authorization": "Bearer " + _gs_get_token()})
    with _urllib_req.urlopen(req, timeout=10) as r:
        return _json_mod.loads(r.read()).get("values", [])

def _gs_put(range_, values):
    """PUT de valores en Google Sheets API v4."""
    _enc = _urllib_parse.quote(range_, safe="!'")
    _url = ("https://sheets.googleapis.com/v4/spreadsheets/" + SHEET_ID
            + "/values/" + _enc + "?valueInputOption=USER_ENTERED")
    body = _json_mod.dumps({"range": range_, "majorDimension": "ROWS", "values": values}).encode()
    req  = _urllib_req.Request(_url, data=body, method="PUT",
                               headers={"Authorization": "Bearer " + _gs_get_token(),
                                        "Content-Type": "application/json"})
    with _urllib_req.urlopen(req, timeout=10) as r:
        return _json_mod.loads(r.read())

def _detect_region(member):
    """Devuelve (label, col_u, col_ep, col_qw) según primer rol de región del miembro."""
    role_ids = [r.id for r in getattr(member, "roles", [])]
    for role_id, label, cu, ce, cq in _REGION_ROLES:
        if role_id in role_ids:
            return label, cu, ce, cq
    return None, None, None, None

def _sheet_add_ep(username, region, col_u, col_ep, col_qw):
    """
    Incrementa EP en Google Sheets. Toma strings/ints ya extraídos (thread-safe).
    Devuelve (success: bool, message: str).
    """
    try:
        cl_u  = _col_letter(col_u)
        cl_ep = _col_letter(col_ep)
        cl_qw = _col_letter(col_qw)
        tab   = _SHEET_TAB

        # Leer columna USERNAME completa
        col_data = _gs_get(f"'{tab}'!{cl_u}1:{cl_u}200")

        # Buscar fila existente (case-insensitive, desde fila de datos)
        target_row = None
        for i, row in enumerate(col_data):
            row_num = i + 1
            if row_num < _SHEET_DATA_START_ROW:
                continue
            if row and str(row[0]).strip().lower() == username.lower():
                target_row = row_num
                break

        if target_row:
            ep_data = _gs_get(f"'{tab}'!{cl_ep}{target_row}")
            current = 0
            if ep_data and ep_data[0]:
                try: current = int(ep_data[0][0])
                except (ValueError, TypeError): current = 0
            new_val = current + 1
            _gs_put(f"'{tab}'!{cl_ep}{target_row}", [[str(new_val)]])
            print(f">>> [SHEETS] {username} ({region}) fila {target_row}: EP {current}→{new_val}")
            return True, f"EP updated! Total: **{new_val}**"

        else:
            occupied = [i + 1 for i, row in enumerate(col_data)
                        if i + 1 >= _SHEET_DATA_START_ROW and row and str(row[0]).strip()]
            next_row = (max(occupied) + 1) if occupied else _SHEET_DATA_START_ROW
            _gs_put(f"'{tab}'!{cl_u}{next_row}",  [[username]])
            _gs_put(f"'{tab}'!{cl_ep}{next_row}", [["1"]])
            _gs_put(f"'{tab}'!{cl_qw}{next_row}", [["0"]])
            print(f">>> [SHEETS] Nuevo: {username} ({region}) fila {next_row}, EP=1")
            return True, "Added to tracker! EP: **1**"

    except Exception as e:
        print(f"!!! [SHEETS] Error para {username}: {e}")
        return False, f"{type(e).__name__}: {e}"


# Configuración de Categoría
try:
    raw_id = os.getenv("CATEGORY_ID")
    if not raw_id:
        print("!!! [ALERTA] Variable CATEGORY_ID no encontrada. Usando 0.")
        TARGET_CATEGORY_ID = 0
    else:
        TARGET_CATEGORY_ID = int(raw_id)
except ValueError:
    TARGET_CATEGORY_ID = 0
    print("!!! [ERROR] CATEGORY_ID no es numérico.")

# --- SETUP DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_messages = True
intents.reactions = True   # needed for wait_for reaction_add
client = discord.Client(intents=intents)

bot_loop = None
bot_ready_event = threading.Event()
# deadline_tasks: {message_id: asyncio.Task}
deadline_tasks = {}

# --- BASE DE DATOS ---
def get_db_connection():
    # Use a reproducible absolute path for the DB so all processes hit the same file
    DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        conn.execute('''
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
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT,
                guild_id TEXT,
                reason TEXT,
                moderator_id TEXT,
                moderator_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
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
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR]: {e}")


def ensure_author_id_column():
    """Ensure `author_id` and `components` columns exist on messages table."""
    try:
        conn = get_db_connection()
        cur = conn.execute("PRAGMA table_info(messages)").fetchall()
        cols = [r['name'] for r in cur]
        if 'author_id' not in cols:
            conn.execute('ALTER TABLE messages ADD COLUMN author_id INTEGER')
            conn.commit()
            print('>>> [DB MIGRATION] Added column author_id')
        if 'components' not in cols:
            conn.execute('ALTER TABLE messages ADD COLUMN components TEXT DEFAULT NULL')
            conn.commit()
            print('>>> [DB MIGRATION] Added column components')
        conn.close()
    except Exception as e:
        print(f'!!! [DB CHECK ERROR]: {e}')

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    print(f'>>> [DISCORD]: ID Categoría: {TARGET_CATEGORY_ID}')
    bot_ready_event.set()
    # Register persistent views if discord.ui is available
    try:
        import discord.ui as _ui
        print('>>> [DISCORD]: discord.ui available — deadline views enabled')
    except ImportError:
        print('!!! [DISCORD]: discord.ui not available — upgrade to discord.py 2.0+')
    # Backfill recent history from the target category so the web UI
    # can display past messages without waiting for new messages.
    try:
        history_limit = int(os.getenv('HISTORY_LIMIT', '200'))
    except ValueError:
        history_limit = 200
    try:
        await sync_category_history(history_limit)
    except Exception as e:
        print(f"!!! [HISTORY ERROR ON READY]: {e}")


# ── Active deadline tasks: msg_id -> asyncio.Task ──
_deadline_tasks = {}

# ── Moderation: bad word list ──
# ── AutoMod: bad word detection system ─────────────────────────────────────
#
# Diseño: matching por TOKEN (no substring ciego).
# Esto elimina los falsos positivos clásicos:
#   "document"   → no detecta "cum"   (substring)
#   "raccoon"    → no detecta "coon"  (substring)
#   "spicy"      → no detecta "spic"  (substring)
#   "cockpit"    → no detecta "cock"  (prefix/whitelist)
#   "Dickens"    → no detecta "dick"  (whitelist)
#   "bigger"     → no detecta "nigger" (edit-distance con ancla)
#   "heilung"    → no detecta "heil"  (exact-only + whitelist)
#   "retardant"  → no detecta "retard" (whitelist)
#   "day/bay/say"→ no detecta "gay"   (exact-only, sin edit-dist)
#
# Pasadas:
#   0. Frases multi-palabra con límite de palabra
#   1. Por token: exact-only para palabras ambiguas, prefix para las demás
#   2. Evasión por espaciado ("n i g g e r", "f.a.g")
#   3. Edit-distance solo en palabras largas (>5 chars), ancla de 2 letras iniciales
# Normalizaciones extra:
#   · _norm_sym: @→a, $→s para "f@ggot", "a$$hole"
#   · _norm_c32: colapsa 3+ chars repetidos → 2 para "nigggger", "biiiitch"

import re as _re_mod
import unicodedata as _ud

_LEET_DIGITS = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a',
    '5': 's', '6': 'g', '7': 't', '8': 'b', '9': 'g',
})


def _norm(text: str) -> str:
    """Unicode→ASCII, minúsculas, leet de dígitos, quitar no-alfanumérico."""
    t = _ud.normalize('NFKD', text)
    t = ''.join(c for c in t if not _ud.combining(c))
    t = t.lower().translate(_LEET_DIGITS)
    return _re_mod.sub(r'[^a-z0-9]', ' ', t).strip()


def _norm_sym(text: str) -> str:
    """Como _norm pero también @→a, $→s, !→i para evasión con símbolos."""
    t = _ud.normalize('NFKD', text)
    t = ''.join(c for c in t if not _ud.combining(c))
    t = t.lower()
    t = t.replace('@', 'a').replace('$', 's').replace('!', 'i').replace('+', 't')
    t = t.translate(_LEET_DIGITS)
    return _re_mod.sub(r'[^a-z0-9]', ' ', t).strip()


def _norm_c32(text: str) -> str:
    """Colapsa runs de 3+ chars iguales a 2. niggggger→nigger, biiiitch→biitch."""
    return _re_mod.sub(r'(.)\1{2,}', r'\1\1', _norm(text))


def _levenshtein(a: str, b: str) -> int:
    if len(a) > len(b): a, b = b, a
    row = list(range(len(a) + 1))
    for cb in b:
        nr = [row[0] + 1]
        for i, ca in enumerate(a):
            nr.append(min(row[i] + (ca != cb), nr[-1] + 1, row[i + 1] + 1))
        row = nr
    return row[-1]


def _join_single_chars(tokens):
    """Junta chars sueltos consecutivos → detecta 'n i g g e r' y 'f.a.g'."""
    chunks, buf = [], []
    for t in tokens:
        if len(t) == 1:
            buf.append(t)
        else:
            if len(buf) >= 3:
                chunks.append(''.join(buf))
            buf = []
    if len(buf) >= 3:
        chunks.append(''.join(buf))
    return chunks


# Tokens legítimos que empiezan por una palabra mala → nunca se flagean
# (retardant, cockpit, Dickens, heilung, cumulative, etc.)
_TOKEN_WHITELIST = frozenset({
    # retard prefix — química/ingeniería
    'retardant', 'retardation',
    # cock prefix — animales y objetos
    'cockpit', 'cocktail', 'cockatoo', 'cockerel', 'cockroach', 'cockatiel',
    # dick prefix — apellidos
    'dickens', 'dickinson', 'dickson',
    # heil prefix — música y geografía
    'heilung', 'heilongjiang',
    # cum prefix — palabras reales
    'cumulative', 'cumulatively', 'cumbia',
    'cumulonimbus', 'cumulonimbi', 'cummings', 'cumulus',
})

# Palabras donde SOLO se acepta el token exacto (no prefijo, no edit-dist).
# Razón: su prefijo coincide con palabras comunes (spicy→spic, cockpit→cock,
# cumulative→cum, "day"→gay por edit, etc.)
_EXACT_ONLY = frozenset({
    'spic',   # spicy
    'cock',   # cockpit, cocktail  (además en whitelist)
    'dick',   # dickens            (además en whitelist)
    'cum',    # cumulative, cumbia (además en whitelist)
    'gay',    # day, bay, say por edit-dist
    'kys',    # keys por edit-dist
    'fag',    # standalone; faggot está por separado
    'heil',   # heilung, heilongjiang (además en whitelist)
})

# Palabras individuales prohibidas
_BAD_SINGLE = [
    # Slurs raciales
    'nigger', 'nigga', 'niga', 'negger',
    'faggot', 'fagot', 'faget', 'fag',
    'retard',
    'chink', 'spic', 'wetback', 'kike', 'gook', 'coon', 'jigaboo', 'beaner',
    'tranny',
    # Sexual explícito
    'porn', 'pron', 'cumshot', 'blowjob', 'handjob',
    'pussy', 'cunt', 'cum', 'cock', 'cocksucker', 'dick', 'dickhead',
    # Daño / acoso
    'kys',
    # Simbología de odio
    'nazi', 'kkk', 'heil',
    # Palabrotas fuertes
    'bitch', 'asshole', 'motherfucker', 'cuck',
    # Solicitada
    'gay',
]

# Frases multi-palabra
_BAD_PHRASES = [
    'kill yourself', 'kill your self',
    'hang yourself', 'hang your self',
    'white power',
    'heil hitler',
]

# Alias para compatibilidad con código existente
BAD_WORDS = _BAD_SINGLE


def contains_bad_word(text: str):
    """
    Detector de palabras prohibidas basado en tokens. Devuelve la palabra/frase
    detectada o None si el mensaje es limpio.
    """
    ns   = _norm(text)       # normalización base
    nl   = _norm_sym(text)   # con sustituciones de símbolo (@, $, !)
    nc   = _norm_c32(text)   # con colapso de chars repetidos (niggggger)
    toks = set(ns.split() + nl.split() + nc.split())

    # ── Pasada 0: frases, con límite de palabra ──
    for phrase in _BAD_PHRASES:
        pn  = _norm(phrase)
        pat = r'(?<![a-z0-9])' + _re_mod.escape(pn) + r'(?![a-z0-9])'
        if _re_mod.search(pat, ns) or _re_mod.search(pat, nl):
            return phrase

    # ── Pasada 1: por token individual ──
    for word in _BAD_SINGLE:
        wn    = _norm(word)
        exact = word in _EXACT_ONLY
        for tok in toks:
            if tok in _TOKEN_WHITELIST:
                continue
            if exact:
                if tok == wn:
                    return word
            else:
                # Prefijo detecta derivados: niggers, retarded, bitching, nazism
                if tok == wn or tok.startswith(wn):
                    return word

    # ── Pasada 2: evasión por espaciado ("n i g g e r", "f.a.g") ──
    for chunk in _join_single_chars(ns.split()):
        for word in _BAD_SINGLE:
            if _norm(word) in chunk:
                return word

    # ── Pasada 3: edit-distance para typos deliberados ──
    # Solo palabras largas (>5 chars), primeras 2 letras deben coincidir (ancla),
    # máximo 1 edición. Esto captura niegger/niggar pero no bigger/trigger.
    for word in _BAD_SINGLE:
        wn = _norm(word)
        if len(wn) <= 5:
            continue
        for tok in toks:
            if tok in _TOKEN_WHITELIST:
                continue
            if len(tok) < 2:
                continue
            if tok[:2] != wn[:2]:
                continue   # ancla: bi≠ni → bigger nunca matchea nigger
            if abs(len(tok) - len(wn)) > 1:
                continue
            if _levenshtein(tok, wn) <= 1:
                return word

    return None


# _spam_cache[guild_id][user_id] = deque of (content_hash, timestamp)
import collections, hashlib, time as _time
_spam_cache = collections.defaultdict(lambda: collections.defaultdict(collections.deque))
SPAM_WINDOW   = 10   # seconds
SPAM_MAX_SAME = 3    # max identical messages in window

# Auto-escalation thresholds
WARN_TIMEOUT_AT  = 3   # warns before 1-day timeout
WARN_BAN_3D_AT   = 1   # warns after returning from timeout → 3-day ban
WARN_PERMA_AT    = 1   # warns after returning from 3d ban → permanent ban


def add_warning(user_id, user_name, guild_id, reason, mod_id='BOT', mod_name='AutoMod'):
    """Insert a warning and return new total warn count for this user."""
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO warnings (user_id, user_name, guild_id, reason, moderator_id, moderator_name) VALUES (?,?,?,?,?,?)',
        (str(user_id), user_name, str(guild_id), reason, str(mod_id), mod_name)
    )
    conn.commit()
    count = conn.execute('SELECT COUNT(*) FROM warnings WHERE user_id=? AND guild_id=?',
                         (str(user_id), str(guild_id))).fetchone()[0]
    conn.close()
    return count


def log_mod_action(user_id, user_name, guild_id, action, reason, duration=None, mod_id='BOT', mod_name='AutoMod'):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO mod_actions (user_id, user_name, guild_id, action, reason, duration_seconds, moderator_id, moderator_name) VALUES (?,?,?,?,?,?,?,?)',
        (str(user_id), user_name, str(guild_id), action, reason, duration, str(mod_id), mod_name)
    )
    conn.commit()
    conn.close()


def get_recent_mod_action(user_id, guild_id, action):
    """Return the most recent mod_action of given type for a user, or None."""
    conn = get_db_connection()
    row = conn.execute(
        'SELECT * FROM mod_actions WHERE user_id=? AND guild_id=? AND action=? ORDER BY timestamp DESC LIMIT 1',
        (str(user_id), str(guild_id), action)
    ).fetchone()
    conn.close()
    return dict(row) if row else None



async def _notify_warn_admins(member, guild, warn_count):
    """DM los admins configurados cuando un usuario llega a exactamente 2 warnings."""
    if warn_count != 2:
        return
    for admin_id in WARN_DM_ADMIN_IDS:
        try:
            admin = await client.fetch_user(admin_id)
            embed = discord.Embed(
                title="⚠️ Warning Alert",
                description=(
                    f"**{member.display_name}** (`{member.id}`) has reached **2 warnings**.\n\n"
                    f"Server: **{guild.name}**\n"
                    f"Action may be required."
                ),
                color=0xF5A623
            )
            embed.set_footer(text="BLZ-T AutoMod")
            await admin.send(embed=embed)
            print(f">>> [WARN DM] Notified admin {admin_id} about {member.display_name}")
        except Exception as e:
            print(f"!!! [WARN DM] Failed to DM admin {admin_id}: {e}")

async def escalate_user(member, guild, warn_count, reason):
    """Apply timeout or ban based on warn count and prior action history."""
    import datetime
    uid  = str(member.id)
    gid  = str(guild.id)
    name = member.name

    print(f'>>> [ESCALATE] {name} | warn_count={warn_count} | WARN_TIMEOUT_AT={WARN_TIMEOUT_AT}')

    had_timeout  = get_recent_mod_action(uid, gid, 'timeout')
    had_ban3d    = get_recent_mod_action(uid, gid, 'ban_3d')

    try:
        if had_ban3d:
            # Permanent ban — already had a 3d ban before
            await member.ban(reason=f'AutoMod: permanent ban after 3d ban | {reason}', delete_message_days=0)
            log_mod_action(uid, name, gid, 'ban_permanent', reason)
            print(f'>>> [AUTOMOD] PERMANENT BAN: {name}')

        elif had_timeout and warn_count >= 1:
            # 3-day ban — already had a timeout before
            await member.ban(reason=f'AutoMod: 3-day ban after timeout | {reason}', delete_message_days=0)
            log_mod_action(uid, name, gid, 'ban_3d', reason, duration=259200)
            print(f'>>> [AUTOMOD] 3D BAN: {name}')
            async def _unban():
                await asyncio.sleep(259200)
                try:
                    user = await client.fetch_user(int(uid))
                    await guild.unban(user, reason='AutoMod: 3d ban expired')
                except Exception as e:
                    print(f'!!! [UNBAN ERROR]: {e}')
            asyncio.create_task(_unban())

        elif warn_count >= WARN_TIMEOUT_AT:
            # 1-day timeout
            until = datetime.datetime.utcnow() + datetime.timedelta(days=1)
            await member.timeout(until, reason=f'AutoMod: {warn_count} warnings | {reason}')
            log_mod_action(uid, name, gid, 'timeout', reason, duration=86400)
            print(f'>>> [AUTOMOD] TIMEOUT 1D: {name} (warn_count={warn_count})')
        else:
            print(f'>>> [ESCALATE] No action yet for {name} (need {WARN_TIMEOUT_AT} warns, have {warn_count})')

    except discord.Forbidden:
        print(f'!!! [ESCALATE] Missing permissions to act on {name}')
    except Exception as e:
        print(f'!!! [ESCALATE ERROR] {name}: {e}')


async def handle_deadline(message, username_raw):
    """Handle !deadline <user>: send 24h confirmation embed, auto-close if no reaction."""
    import datetime, asyncio

    channel = message.channel
    guild   = getattr(channel, 'guild', None)
    if not guild:
        print('!!! [DEADLINE] No guild found')
        return

    # Delete the command message silently
    try:
        await message.delete()
    except Exception as e:
        print(f'!!! [DEADLINE] Could not delete command: {e}')

    # ── Resolve target user ──
    target_user = None
    username_clean = username_raw.lstrip('@').strip()

    import re
    m_id = re.match(r'<@!?(\d+)>', username_raw)
    if m_id:
        uid = int(m_id.group(1))
        try:
            target_user = guild.get_member(uid) or await guild.fetch_member(uid)
        except Exception:
            pass
    else:
        if not guild.chunked:
            try:
                await guild.chunk()
            except Exception:
                pass
        q = username_clean.lower()
        for mem in guild.members:
            if mem.display_name.lower() == q or mem.name.lower() == q:
                target_user = mem
                break
        if not target_user:
            for mem in guild.members:
                if q in mem.display_name.lower() or q in mem.name.lower():
                    target_user = mem
                    break
        if not target_user:
            try:
                results = await guild.query_members(query=username_clean, limit=3)
                if results:
                    target_user = results[0]
            except Exception:
                pass

    print(f'>>> [DEADLINE] Target resolved: {target_user}')

    mention_str  = target_user.mention if target_user else f'@{username_clean}'
    target_id    = target_user.id if target_user else None

    # ── Build and send the deadline embed ──
    deadline_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    unix_ts     = int(deadline_dt.timestamp())

    embed = discord.Embed(
        title='Deadline — Confirmation Required',
        description=(
            mention_str + ' you must confirm your availability within the next **24 hours**.'
            + '\n\nReact with ✅ to confirm.'
            + '\nDeadline: <t:' + str(unix_ts) + ':R>'
        ),
        color=0xF5A623
    )
    embed.set_footer(text='If you do not confirm within 24h, the ticket will be marked as ready to close.')

    try:
        sent = await channel.send(embed=embed)
        await sent.add_reaction('✅')
        print(f'>>> [DEADLINE] Embed sent, id={sent.id}')
    except Exception as e:
        print(f'!!! [DEADLINE SEND ERROR]: {e}')
        return

    # ── Watch for ✅ reaction from the target user for 24h ──
    async def watch_reaction():
        try:
            def check(reaction, user):
                return (
                    str(reaction.emoji) == '✅'
                    and reaction.message.id == sent.id
                    and (target_id is None or user.id == target_id)
                    and not user.bot
                )
            await client.wait_for('reaction_add', check=check, timeout=86400)
            # Confirmed!
            confirmed_embed = discord.Embed(
                title='Confirmed',
                description=mention_str + ' has confirmed their availability.',
                color=0x26C9B8
            )
            try:
                await sent.edit(embed=confirmed_embed)
                await sent.clear_reactions()
            except Exception:
                pass
            print(f'>>> [DEADLINE] Confirmed by {mention_str}')
        except asyncio.TimeoutError:
            # 24h passed without confirmation
            close_embed = discord.Embed(
                title='Ticket Ready to Close',
                description=mention_str + ' did not confirm within 24 hours. The ticket is ready to be closed.',
                color=0xFF6B6B
            )
            try:
                await channel.send(embed=close_embed)
                expired_embed = discord.Embed(
                    title='Deadline Expired',
                    description=mention_str + ' did not respond within 24 hours.',
                    color=0x888888
                )
                await sent.edit(embed=expired_embed)
                await sent.clear_reactions()
            except Exception as e:
                print(f'!!! [DEADLINE TIMEOUT ERROR]: {e}')
        except Exception as e:
            print(f'!!! [DEADLINE WATCH ERROR]: {e}')
        finally:
            _deadline_tasks.pop(sent.id, None)

    task = asyncio.create_task(watch_reaction())
    _deadline_tasks[sent.id] = task


async def handle_done(message):
    """
    Detecta !done: envía embed de confirmación con reacciones ✅/❌.
    Si el usuario confirma, incrementa su EP en Google Sheets.
    """
    member = message.guild.get_member(message.author.id) if message.guild else None
    if not member:
        return

    region, col_u, col_ep, col_qw = _detect_region(member)
    region_label = f" ({region})" if region else " (no region role)"

    embed = discord.Embed(
        title="📋 Tryout Completion",
        description=(
            f"Hey {message.author.mention}! Have you **completed your tryout** this week?{region_label}\n\n"
            "React with ✅ to confirm or ❌ to cancel."
        ),
        color=0x26C9B8
    )
    embed.set_footer(text="BLZ-T EP Tracker • You have 60 seconds to respond")

    try:
        sent = await message.channel.send(embed=embed)
    except Exception as e:
        print(f"!!! [DONE] Could not send embed: {e}")
        return

    await sent.add_reaction("✅")
    await sent.add_reaction("❌")

    def check(reaction, user):
        return (
            user.id == message.author.id
            and str(reaction.emoji) in ("✅", "❌")
            and reaction.message.id == sent.id
        )

    try:
        reaction, _ = await client.wait_for("reaction_add", timeout=60.0, check=check)
    except asyncio.TimeoutError:
        timeout_embed = discord.Embed(
            title="⏰ Timed out",
            description="No response received. Use `!done` again when ready.",
            color=0x888888
        )
        await sent.edit(embed=timeout_embed)
        await sent.clear_reactions()
        return

    if str(reaction.emoji) == "❌":
        cancel_embed = discord.Embed(
            title="❌ Cancelled",
            description="No problem! Use `!done` when you have completed your tryout.",
            color=0xFF6B6B
        )
        await sent.edit(embed=cancel_embed)
        await sent.clear_reactions()
        return

    # ✅ confirmed
    if not region:
        no_region_embed = discord.Embed(
            title="❌ No Region Role",
            description="You do not have an EU, NA, or ASIA role assigned. Ask a moderator.",
            color=0xFF6B6B
        )
        await sent.edit(embed=no_region_embed)
        await sent.clear_reactions()
        return

    processing_embed = discord.Embed(
        title="⏳ Updating tracker...",
        description=f"Recording EP for **{member.display_name}** ({region}), please wait.",
        color=0xF5A623
    )
    try:
        await sent.edit(embed=processing_embed)
        await sent.clear_reactions()
    except Exception:
        pass

    username = member.display_name
    try:
        loop = asyncio.get_running_loop()
        success, msg_text = await asyncio.wait_for(
            loop.run_in_executor(None, _sheet_add_ep, username, region, col_u, col_ep, col_qw),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        success, msg_text = False, "Timeout: Google Sheets tardó demasiado (>20s)"
    except Exception as exc:
        success, msg_text = False, f"{type(exc).__name__}: {exc}"

    print(f">>> [DONE] {member.display_name} ({region}) | success={success} | {msg_text}")

    if success:
        final_embed = discord.Embed(
            title="✅ EP Recorded!",
            description=(
                f"**{member.display_name}** — Region: **{region}**\n"
                f"{msg_text}"
            ),
            color=0x26C9B8
        )
        final_embed.set_footer(text="BLZ-T EP Tracker")
    else:
        final_embed = discord.Embed(
            title="❌ Error updating tracker",
            description=f"```{msg_text}```\nContact an admin if this persists.",
            color=0xFF6B6B
        )

    try:
        await sent.edit(embed=final_embed)
    except Exception as exc2:
        print(f"!!! [DONE] Could not edit final embed: {exc2}")


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Moderation API — segunda capa de detección (capa IA)
#
# Flujo híbrido:
#   1. contains_bad_word()  → acción INMEDIATA (sin latencia)
#   2. _openai_moderate()   → corre en background tras guardar el mensaje;
#                             actúa solo si el algoritmo lo dejó pasar
#
# Umbral de confianza: solo se actúa si score >= _MOD_THRESHOLD.
# Sube el umbral (0.85-0.95) para menos falsos positivos de la IA.
# Bájalo (0.60-0.75) para más sensibilidad.
# ─────────────────────────────────────────────────────────────────────────────
_MODERATION_URL = "https://api.openai.com/v1/moderations"
_MOD_THRESHOLD  = float(os.getenv("MOD_AI_THRESHOLD", "0.80"))  # configurable por env var

# Categorías que activan borrado + advertencia
_MOD_CATEGORIES = frozenset({
    "hate",
    "hate/threatening",
    "harassment",
    "harassment/threatening",
    "self-harm",
    "self-harm/intent",
    "self-harm/instructions",
    "sexual/minors",        # contenido sexual con menores: siempre eliminar
    "violence/graphic",     # gore / imágenes violentas explícitas
})

# Categorías que solo se REGISTRAN en el log pero NO borran ni advierten
# (para auditoría sin castigar contenido borderline)
_MOD_LOG_ONLY = frozenset({
    "sexual",    # contenido sexual adulto — log, no ban automático
    "violence",  # violencia genérica — frecuente en gaming
})

_MOD_LABELS = {
    "hate":                    "discurso de odio",
    "hate/threatening":        "amenaza con odio",
    "harassment":              "acoso",
    "harassment/threatening":  "acoso con amenaza",
    "self-harm":               "daño a uno mismo",
    "self-harm/intent":        "intención de autolesión",
    "self-harm/instructions":  "instrucciones de autolesión",
    "sexual/minors":           "contenido inapropiado con menores",
    "violence/graphic":        "violencia gráfica",
    "sexual":                  "contenido sexual",
    "violence":                "contenido violento",
}

async def _openai_moderate(message):
    """
    Llama a la OpenAI Moderation API en background.
    Solo actúa si el score supera _MOD_THRESHOLD para la categoría detectada.
    Registra TODAS las decisiones en mod_actions para auditoría.
    """
    if not OPENAI_MODERATION_KEY or not _aiohttp:
        return
    text = (message.content or "").strip()
    if not text or len(text) < 3:
        return
    guild = getattr(message.channel, "guild", None)
    if not guild:
        return

    try:
        async with _aiohttp.ClientSession() as session:
            async with session.post(
                _MODERATION_URL,
                headers={"Authorization": f"Bearer {OPENAI_MODERATION_KEY}",
                         "Content-Type": "application/json"},
                json={"input": text},
                timeout=_aiohttp.ClientTimeout(total=6),
            ) as resp:
                if resp.status != 200:
                    print(f">>> [AI-MOD] HTTP {resp.status}")
                    return
                data = await resp.json()
    except asyncio.TimeoutError:
        print(">>> [AI-MOD] Timeout (>6s)")
        return
    except Exception as exc:
        print(f">>> [AI-MOD] Error: {exc}")
        return

    result  = data.get("results", [{}])[0]
    cats    = result.get("categories",       {})
    scores  = result.get("category_scores",  {})

    # ── Categorías por encima del umbral (activas o no según la API) ──
    # Usamos el score directamente en vez del booleano "flagged":
    # así podemos ajustar la sensibilidad con _MOD_THRESHOLD.
    over_threshold = {
        c: scores[c]
        for c in scores
        if scores[c] >= _MOD_THRESHOLD
    }

    if not over_threshold:
        return  # mensaje limpio según la IA

    top_cat   = max(over_threshold, key=over_threshold.get)
    top_score = over_threshold[top_cat]
    label     = _MOD_LABELS.get(top_cat, top_cat)

    # ── Categorías que solo se loguean (no acción) ──
    if top_cat in _MOD_LOG_ONLY and top_cat not in _MOD_CATEGORIES:
        print(f">>> [AI-MOD] LOG-ONLY {message.author} | {top_cat} ({top_score:.0%}) | '{text[:60]}'")
        log_mod_action(
            message.author.id, message.author.name, guild.id,
            action="ai_flagged_log",
            reason=f"AI log-only: {top_cat} ({top_score:.0%})",
        )
        return

    # ── Categoría de acción: borrar + advertir ──
    print(f">>> [AI-MOD] FLAGGED {message.author} | {top_cat} ({top_score:.0%}) | '{text[:60]}'")

    deleted = False
    try:
        await message.delete()
        deleted = True
    except discord.NotFound:
        deleted = True   # ya fue borrado (por otro mod, por ejemplo)
    except discord.Forbidden:
        print(f"!!! [AI-MOD] Sin permiso en #{message.channel.name}")
    except Exception as exc:
        print(f"!!! [AI-MOD] Delete error: {exc}")

    warn_count = add_warning(
        message.author.id, message.author.name, guild.id,
        reason=f"IA: {label} ({top_score:.0%})",
    )
    log_mod_action(
        message.author.id, message.author.name, guild.id,
        action="ai_automod",
        reason=f"AI: {top_cat} ({top_score:.0%}) | msg: {text[:120]}",
    )
    print(f">>> [AI-MOD] {message.author.name} warned ({warn_count}) | deleted={deleted}")

    try:
        await message.channel.send(
            f"⚠️ {message.author.mention} Tu mensaje fue eliminado: **{label}**. "
            f"Advertencia **{warn_count}**.",
            delete_after=8,
        )
    except Exception as exc:
        print(f"!!! [AI-MOD] Warn msg error: {exc}")

    member = guild.get_member(message.author.id)
    if member:
        await escalate_user(member, guild, warn_count, f"IA: {top_cat}")


@client.event
async def on_message(message):
    # NOTA: Eliminamos la restricción de 'client.user' para ver mensajes de la web

    # ── Skip bot messages for automod ──
    if message.author.bot:
        # Still process deadline command if bot sends it
        if message.content and message.content.strip().startswith('!deadline'):
            pass  # fall through to deadline handler below
        else:
            # Save to DB and return
            if hasattr(message.channel, 'category') and message.channel.category:
                if message.channel.category.id == TARGET_CATEGORY_ID:
                    try:
                        conn = get_db_connection()
                        conn.execute(
                            'INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id) VALUES (?,?,?,?,?,?,?)',
                            (message.channel.id, message.channel.name, message.author.name,
                             str(message.author.avatar.url) if message.author.avatar else '',
                             message.content, getattr(message.author,'id',None), message.id)
                        )
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass
            return

    # ── AutoMod: bad word detection (all guild channels) ──
    guild = getattr(message.channel, 'guild', None)
    if guild and message.content:
        found_word = contains_bad_word(message.content)
        print(f'>>> [AUTOMOD] msg="{message.content!r}" | found={found_word!r}')
        if found_word:
            # Delete message first — needs Manage Messages permission
            deleted = False
            try:
                await message.delete()
                deleted = True
            except discord.Forbidden:
                print(f'!!! [AUTOMOD] Missing Manage Messages permission in #{message.channel.name}')
            except Exception as e:
                print(f'!!! [AUTOMOD DELETE ERROR]: {e}')

            warn_count = add_warning(
                message.author.id, message.author.name, guild.id,
                reason=f'Bad word: {found_word}'
            )
            print(f'>>> [AUTOMOD] {message.author.name} warned ({warn_count} total) | deleted={deleted}')
            try:
                await message.channel.send(
                    f'⚠️ {message.author.mention} Your message was removed for violating community rules. '
                    f'**Warning {warn_count}**.',
                    delete_after=8
                )
            except Exception as e:
                print(f'!!! [AUTOMOD WARN MSG ERROR]: {e}')
            member = guild.get_member(message.author.id)
            if member:
                await _notify_warn_admins(member, guild, warn_count)
                await escalate_user(member, guild, warn_count, f'Bad word: {found_word}')
            return  # stop processing — don't fall through to spam or DB save

    # ── AutoMod: spam detection (all guild channels) ──
    if guild and message.content is not None:
        import hashlib as _hs, time as _t
        now = _t.time()
        gid = str(guild.id)
        uid = str(message.author.id)
        # Hash content (normalise whitespace + lowercase)
        import re as _re2
        norm = _re2.sub(r'\s+', ' ', message.content.lower().strip())
        h    = _hs.md5(norm.encode()).hexdigest()

        dq = _spam_cache[gid][uid]
        # Remove old entries outside window
        while dq and now - dq[0][1] > SPAM_WINDOW:
            dq.popleft()
        dq.append((h, now))

        # Count how many recent messages share this hash
        same_count = sum(1 for hh, _ in dq if hh == h)
        if same_count >= SPAM_MAX_SAME:
            dq.clear()  # reset cache immediately

            # Delete triggering message right away (no API round-trip first)
            try:
                await message.delete()
            except Exception:
                pass

            warn_count = add_warning(
                message.author.id, message.author.name, guild.id,
                reason=f'Spam: {same_count} identical messages'
            )
            print(f'>>> [AUTOMOD SPAM] {message.author.name} warned ({warn_count}) | same={same_count}')

            member = guild.get_member(message.author.id)

            try:
                await message.channel.send(
                    f'⚠️ {message.author.mention} Spam detected and removed. **Warning {warn_count}**.',
                    delete_after=8
                )
            except Exception:
                pass

            if member:
                await _notify_warn_admins(member, guild, warn_count)
                await escalate_user(member, guild, warn_count, 'Spam')

            # Bulk delete older duplicates in background (non-blocking)
            async def _bulk_delete():
                try:
                    to_delete = []
                    async for msg in message.channel.history(limit=30):
                        if msg.author.id == message.author.id:
                            m_norm = _re2.sub(r'\s+', ' ', (msg.content or '').lower().strip())
                            if _hs.md5(m_norm.encode()).hexdigest() == h:
                                to_delete.append(msg)
                    for m in to_delete:
                        try:
                            await m.delete()
                        except Exception:
                            pass
                except Exception:
                    pass
            asyncio.create_task(_bulk_delete())
            return

    # ── !done — EP Tracker ──
    if message.content and message.content.strip().lower() == '!done':
        if message.guild:
            asyncio.create_task(handle_done(message))
        return

    # ── Segunda capa: OpenAI Moderation (background, no bloqueante) ──
    if guild and message.content and OPENAI_MODERATION_KEY:
        asyncio.create_task(_openai_moderate(message))

    # ── !deadline command ──
    if message.content and message.content.strip().startswith('!deadline'):
        print(f'>>> [DEADLINE] Triggered by {message.author} | content: {message.content!r}')
        parts = message.content.strip().split(None, 1)
        if len(parts) >= 2:
            ALLOWED_ROLE_IDS = {1355062394547736675, 1355062394547736673, 1483349943962964068}
            author_role_ids  = {r.id for r in getattr(message.author, 'roles', [])}
            is_bot_self      = (client.user and message.author.id == client.user.id)
            print(f'>>> [DEADLINE] is_bot_self={is_bot_self} | role_ids={author_role_ids} | allowed={ALLOWED_ROLE_IDS}')
            if is_bot_self or (author_role_ids & ALLOWED_ROLE_IDS):
                await handle_deadline(message, parts[1].strip())
            else:
                try:
                    await message.reply('You do not have permission to use this command.', delete_after=5)
                except Exception:
                    pass
        else:
            try:
                await message.reply('Usage: `!deadline <user>`', delete_after=5)
            except Exception:
                pass
        return

    if hasattr(message.channel, 'category') and message.channel.category:
        if message.channel.category.id == TARGET_CATEGORY_ID:
            try:
                conn = get_db_connection()
                import json as _json
                _comps = None
                try:
                    if message.components:
                        _comps = _json.dumps([
                            {'type': row.type.value if hasattr(row.type,'value') else int(row.type),
                             'components': [
                                 {'type': c.type.value if hasattr(c.type,'value') else int(c.type),
                                  'custom_id': getattr(c, 'custom_id', None),
                                  'label': getattr(c, 'label', None),
                                  'style': c.style.value if hasattr(getattr(c,'style',None),'value') else getattr(c,'style',None),
                                  'emoji': {'name': c.emoji.name, 'id': str(c.emoji.id) if c.emoji.id else None} if getattr(c,'emoji',None) else None,
                                  'url': getattr(c, 'url', None),
                                  'disabled': getattr(c, 'disabled', False),
                                 } for c in row.children
                             ]} for row in message.components
                        ])
                except Exception as _e:
                    print(f'!!! [COMPONENTS SERIALIZE]: {_e}')
                conn.execute('''
                    INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id, components)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    message.channel.id,
                    message.channel.name,
                    message.author.name,
                    str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                    message.content,
                    getattr(message.author, 'id', None),
                    message.id,
                    _comps
                ))
                conn.commit()
                conn.close()
                print(f">>> [MSG SAVED]: {message.author.name} -> #{message.channel.name}")
            except Exception as e:
                print(f"!!! [SAVE ERROR]: {e}")


async def sync_category_history(limit=200):
    """Fetch recent messages from each text channel in the target category and
    insert them into the local SQLite DB. Uses `message_id` UNIQUE constraint
    + `INSERT OR IGNORE` to avoid duplicates.
    """
    try:
        category = client.get_channel(TARGET_CATEGORY_ID)
        if not category:
            try:
                category = await client.fetch_channel(TARGET_CATEGORY_ID)
            except Exception:
                print(f"!!! [HISTORY] Could not fetch category {TARGET_CATEGORY_ID}")
                return

        for c in getattr(category, 'channels', []):
            if isinstance(c, discord.TextChannel):
                print(f">>> [HISTORY] Syncing #{c.name} (limit={limit})")
                try:
                    async for msg in c.history(limit=limit):
                        try:
                            conn = get_db_connection()
                            import json as _json2
                            _comps2 = None
                            try:
                                if msg.components:
                                    _comps2 = _json2.dumps([
                                        {'type': row.type.value if hasattr(row.type,'value') else int(row.type),
                                         'components': [
                                             {'type': c2.type.value if hasattr(c2.type,'value') else int(c2.type),
                                              'custom_id': getattr(c2,'custom_id',None),
                                              'label': getattr(c2,'label',None),
                                              'style': c2.style.value if hasattr(getattr(c2,'style',None),'value') else getattr(c2,'style',None),
                                              'emoji': {'name': c2.emoji.name,'id': str(c2.emoji.id) if c2.emoji.id else None} if getattr(c2,'emoji',None) else None,
                                              'url': getattr(c2,'url',None),
                                              'disabled': getattr(c2,'disabled',False),
                                             } for c2 in row.children
                                         ]} for row in msg.components
                                    ])
                            except Exception: pass
                            conn.execute('''
                                INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id, timestamp, components)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                c.id,
                                c.name,
                                msg.author.name,
                                str(msg.author.avatar.url) if msg.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                                msg.content,
                                getattr(msg.author, 'id', None),
                                msg.id,
                                msg.created_at.isoformat(),
                                _comps2
                            ))
                            conn.commit()
                            conn.close()
                        except Exception as e:
                            print(f"!!! [HISTORY SAVE ERROR]: {e}")
                except Exception as e:
                    print(f"!!! [HISTORY FETCH ERROR] #{c.name}: {e}")
        print(">>> [HISTORY]: Sync complete.")
    except Exception as e:
        print(f"!!! [HISTORY ERROR]: {e}")

# --- RUTAS FLASK ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/channels')
def get_channels():
    if not bot_ready_event.is_set(): return jsonify([]), 503

    async def get_channels_async():
        try:
            category = client.get_channel(TARGET_CATEGORY_ID)
            if not category:
                try: category = await client.fetch_channel(TARGET_CATEGORY_ID)
                except: pass
            
            if category:
                text_channels = [c for c in category.channels if isinstance(c, discord.TextChannel)]
                text_channels.sort(key=lambda x: x.position)
                # IMPORTANTE: ID como string
                return [{"id": str(c.id), "name": c.name} for c in text_channels]
            return []
        except: return []

    try:
        future = asyncio.run_coroutine_threadsafe(get_channels_async(), bot_loop)
        channels = future.result(timeout=5)
        
        if channels: return jsonify(channels)
        
        conn = get_db_connection()
        db_chans = conn.execute('SELECT DISTINCT channel_name as name, channel_id as id FROM messages').fetchall()
        conn.close()
        return jsonify([{"id": str(row["id"]), "name": row["name"]} for row in db_chans])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/botinfo')
def bot_info():
    """Return basic bot info for the web UI (name and id)."""
    try:
        if not bot_ready_event.is_set():
            return jsonify({"ready": False}), 503
        name = str(client.user.name) if client.user else None
        uid = str(client.user.id) if client.user else None
        return jsonify({"ready": True, "name": name, "id": uid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/mention_lookup', methods=['POST'])
def mention_lookup():
    """Resolve user and role IDs to human-friendly strings.
    Expects JSON: { users: [id,...], roles: [id,...] }
    Returns: { users: {id: display}, roles: {id: display} }
    """
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "Bot not ready"}), 503

    payload = request.get_json(force=True) or {}
    user_ids = payload.get('users') or []
    role_ids = payload.get('roles') or []

    async def _lookup():
        out = {'users': {}, 'roles': {}}
        # Resolve guild from target category so we can lookup members/roles
        try:
            category = client.get_channel(TARGET_CATEGORY_ID) or await client.fetch_channel(TARGET_CATEGORY_ID)
            guild = getattr(category, 'guild', None)
        except Exception:
            guild = None

        # Resolve users
        for u in set(user_ids):
            try:
                uid = int(u)
            except Exception:
                continue
            # Try to resolve as a guild Member first (so we can get display_name/nick)
            member_display = None
            member = None
            if guild:
                try:
                    member = guild.get_member(uid)
                    if not member:
                        try:
                            member = await guild.fetch_member(uid)
                        except Exception:
                            member = None
                    if member:
                        member_display = f"@{member.display_name}"
                except Exception:
                    member_display = None

            if member_display:
                # member_display already contains an '@' prefix
                # Also include the global tag if available
                try:
                    uobj = member.user if hasattr(member, 'user') else None
                    tag = None
                    if uobj and getattr(uobj, 'discriminator', None):
                        tag = f"{uobj.name}#{uobj.discriminator}"
                except Exception:
                    tag = None
                out['users'][str(uid)] = { 'display': member_display, 'tag': tag }
                continue

            # Fallback to user object (global)
            user = client.get_user(uid)
            if not user:
                try:
                    user = await client.fetch_user(uid)
                except Exception:
                    user = None
            if user:
                out['users'][str(uid)] = { 'display': f"@{user.name}", 'tag': f"{user.name}#{user.discriminator}" }

        # Resolve roles if we have a guild
        if guild:
            for r in set(role_ids):
                try:
                    rid = int(r)
                except Exception:
                    continue
                try:
                    role = guild.get_role(rid)
                    if role:
                        out['roles'][str(rid)] = f"@{role.name}"
                except Exception:
                    # ignore
                    pass

        return out

    try:
        future = asyncio.run_coroutine_threadsafe(_lookup(), bot_loop)
        res = future.result(timeout=10)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages')
def get_messages():
    try:
        # Support optional channel filtering and limit to return more/older messages
        channel_id = request.args.get('channel_id')
        limit_q = request.args.get('limit')
        since_id_q = request.args.get('since_id')

        conn = get_db_connection()
        if channel_id:
            # prefer numeric id
            try:
                cid = int(channel_id)
            except ValueError:
                cid = None

            # If caller passed since_id, return messages with message_id > since_id (new messages only)
            if since_id_q:
                try:
                    since_id = int(since_id_q)
                except ValueError:
                    since_id = None
                if since_id is not None:
                    rows = conn.execute('SELECT * FROM messages WHERE channel_id = ? AND message_id > ? ORDER BY timestamp ASC', (cid, since_id)).fetchall()
                else:
                    rows = []
            elif limit_q and str(limit_q).lower() == 'all':
                rows = conn.execute('SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp ASC', (cid,)).fetchall()
            else:
                try:
                    lim = int(limit_q) if limit_q else 1000
                except ValueError:
                    lim = 1000
                # Get the most recent messages, then order them chronologically for display
                rows = conn.execute('SELECT * FROM (SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC', (cid, lim)).fetchall()
        else:
            # no channel filter: return recent messages across channels
            try:
                lim = int(limit_q) if limit_q else 50
            except ValueError:
                lim = 50
            rows = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?', (lim,)).fetchall()

        conn.close()
        results = []
        for row in rows:
            d = dict(row)
            # Stringify all snowflake IDs — JS Number loses 64-bit precision
            d['channel_id'] = str(d['channel_id']) if d.get('channel_id') else None
            d['message_id'] = str(d['message_id']) if d.get('message_id') else None
            d['author_id']  = str(d['author_id'])  if d.get('author_id')  else None
            results.append(d)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/deadline', methods=['POST'])
def deadline_endpoint():
    """Direct API call to trigger !deadline — bypasses on_message entirely."""
    data = request.json or {}
    channel_id = data.get('channel_id')
    username   = data.get('username', '').strip()

    if not channel_id or not username:
        return jsonify({'error': 'channel_id and username required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _run():
        try:
            channel = await client.fetch_channel(int(channel_id))
            # Create a fake message-like object so handle_deadline can work
            # Actually we just call the core logic directly
            guild = getattr(channel, 'guild', None)
            if not guild:
                return {'success': False, 'error': 'No guild'}

            import re, datetime, asyncio

            target_user = None
            username_clean = username.lstrip('@').strip()

            m_id = re.match(r'<@!?(\d+)>', username)
            if m_id:
                uid = int(m_id.group(1))
                try:
                    target_user = guild.get_member(uid) or await guild.fetch_member(uid)
                except Exception:
                    pass
            else:
                if not guild.chunked:
                    try:
                        await guild.chunk()
                    except Exception:
                        pass
                q = username_clean.lower()
                for mem in guild.members:
                    if mem.display_name.lower() == q or mem.name.lower() == q:
                        target_user = mem
                        break
                if not target_user:
                    for mem in guild.members:
                        if q in mem.display_name.lower() or q in mem.name.lower():
                            target_user = mem
                            break
                if not target_user:
                    try:
                        results = await guild.query_members(query=username_clean, limit=3)
                        if results:
                            target_user = results[0]
                    except Exception:
                        pass

            mention_str = target_user.mention if target_user else f'@{username_clean}'
            target_id   = target_user.id if target_user else None

            deadline_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            unix_ts     = int(deadline_dt.timestamp())

            embed = discord.Embed(
                title='Deadline — Confirmation Required',
                description=(
                    mention_str + ' you must confirm your availability within the next **24 hours**.'
                    + '\n\nReact with ✅ to confirm.'
                    + '\nDeadline: <t:' + str(unix_ts) + ':R>'
                ),
                color=0xF5A623
            )
            embed.set_footer(text='If you do not confirm within 24h, the ticket will be marked as ready to close.')

            sent = await channel.send(embed=embed)
            await sent.add_reaction('✅')
            print(f'>>> [DEADLINE API] Sent for {mention_str} in #{channel.name}')

            # Watch reaction in background
            async def watch():
                try:
                    def check(reaction, user):
                        return (
                            str(reaction.emoji) == '✅'
                            and reaction.message.id == sent.id
                            and (target_id is None or user.id == target_id)
                            and not user.bot
                        )
                    await client.wait_for('reaction_add', check=check, timeout=86400)
                    confirmed_embed = discord.Embed(
                        title='Confirmed',
                        description=mention_str + ' has confirmed their availability.',
                        color=0x26C9B8
                    )
                    try:
                        await sent.edit(embed=confirmed_embed)
                        await sent.clear_reactions()
                    except Exception:
                        pass
                except asyncio.TimeoutError:
                    close_embed = discord.Embed(
                        title='Ticket Ready to Close',
                        description=mention_str + ' did not confirm within 24h. The ticket is ready to be closed.',
                        color=0xFF6B6B
                    )
                    try:
                        await channel.send(embed=close_embed)
                        expired = discord.Embed(title='Deadline Expired', description=mention_str + ' did not respond.', color=0x888888)
                        await sent.edit(embed=expired)
                        await sent.clear_reactions()
                    except Exception as e:
                        print(f'!!! [DEADLINE TIMEOUT]: {e}')
                except Exception as e:
                    print(f'!!! [DEADLINE WATCH]: {e}')

            asyncio.create_task(watch())
            return {'success': True, 'message_id': str(sent.id)}

        except Exception as e:
            print(f'!!! [DEADLINE API ERROR]: {e}')
            return {'success': False, 'error': str(e)}

    try:
        result = asyncio.run_coroutine_threadsafe(_run(), bot_loop).result(timeout=15)
        if result.get('success'):
            return jsonify({'status': 'ok', 'message_id': result.get('message_id')})
        return jsonify({'error': result.get('error', 'Unknown error')}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/members')
def get_members():
    """Return guild members and roles for @mention autocomplete."""
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'members': [], 'roles': []}), 503

    async def _fetch():
        try:
            category = client.get_channel(TARGET_CATEGORY_ID)
            if not category:
                category = await client.fetch_channel(TARGET_CATEGORY_ID)
            guild = getattr(category, 'guild', None)
            if not guild:
                return {'members': [], 'roles': []}

            members = []
            async for m in guild.fetch_members(limit=1000):
                members.append({
                    'id': str(m.id),
                    'display': m.display_name,
                    'username': m.name,
                    'avatar': str(m.avatar.url) if m.avatar else None
                })

            roles = []
            for r in sorted(guild.roles, key=lambda x: -x.position):
                if r.name == '@everyone':
                    continue
                roles.append({
                    'id': str(r.id),
                    'name': r.name,
                    'color': '#{:06x}'.format(r.color.value) if r.color.value else '#888888'
                })

            return {'members': members, 'roles': roles}
        except Exception as e:
            print(f'!!! [MEMBERS ERROR]: {e}')
            return {'members': [], 'roles': []}

    try:
        future = asyncio.run_coroutine_threadsafe(_fetch(), bot_loop)
        result = future.result(timeout=8)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/sync')
def trigger_sync():
    """Trigger a history sync. Query params: ?limit=200 or ?limit=all
    Returns immediate acknowledgement; sync runs in bot loop.
    """
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "Bot disconnected"}), 503

    limit_q = request.args.get('limit', None)
    if limit_q and str(limit_q).lower() == 'all':
        limit = None
    else:
        try:
            limit = int(limit_q) if limit_q else int(os.getenv('HISTORY_LIMIT', '1000'))
        except ValueError:
            limit = int(os.getenv('HISTORY_LIMIT', '1000'))

    try:
        future = asyncio.run_coroutine_threadsafe(sync_category_history(limit), bot_loop)
        # don't block long; return accepted
        return jsonify({"status": "accepted", "limit": ("all" if limit is None else limit)}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    print(f"\n>>> [WEB SEND] ID: {channel_id} | Msg: {content}")
    
    if not channel_id or not content: return jsonify({"error": "Faltan datos"}), 400
    if not bot_loop or not bot_ready_event.is_set(): return jsonify({"error": "Bot desconectado"}), 503

    async def send_async():
        try:
            c_id = int(channel_id)
            channel = await client.fetch_channel(c_id)
            sent = await channel.send(content)
            # return sent message info so the web client can update optimistic UI immediately
            return {"success": True, "message_id": str(sent.id), "author_id": str(getattr(sent.author, 'id', '') or ''), "author_name": str(sent.author.name), "author_avatar": (str(sent.author.avatar.url) if getattr(sent.author, 'avatar', None) else None), "channel_name": getattr(channel, 'name', None), "timestamp": sent.created_at.isoformat()}
        except discord.NotFound:
            print(f"!!! [ERROR] Canal {channel_id} NO EXISTE.")
            return {"success": False, "error": "Canal no encontrado en Discord."}
        except discord.Forbidden:
            print(f"!!! [ERROR] SIN PERMISOS en {channel_id}.")
            return {"success": False, "error": "Sin permisos."}
        except Exception as e:
            print(f"!!! [ERROR]: {e}")
            return {"success": False, "error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(send_async(), bot_loop)
        result = future.result(timeout=10)
        
        if result["success"]:
            # Persist the sent message into local DB immediately so the web UI sees it on reload
            try:
                conn = get_db_connection()
                conn.execute('''
                    INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    int(channel_id),
                    result.get('channel_name') or '',
                    result.get('author_name') or '',
                    result.get('author_avatar') or "https://cdn.discordapp.com/embed/avatars/0.png",
                    content,
                    result.get('author_id'),
                    result.get('message_id'),
                    result.get('timestamp')
                ))
                conn.commit()
                conn.close()
                print(f">>> [SEND DB SAVED]: msg_id={result.get('message_id')} channel={result.get('channel_name')} author={result.get('author_name')}")
            except Exception as e:
                print(f"!!! [SEND DB SAVE ERROR]: {e}")
            return jsonify({"status": "OK", **({k: v for k, v in result.items() if k != 'success'})})
        else:
            return jsonify({"error": result["error"]}), 400
            
    except Exception as e:
        return jsonify({"error": f"Internal: {e}"}), 500

# --- ARRANQUE ---
@app.route('/api/messages/<int:message_id>/delete', methods=['POST'])
def delete_message(message_id):
    data = request.json or {}
    channel_id = data.get('channel_id')
    if not channel_id:
        return jsonify({'error': 'channel_id required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _delete():
        try:
            channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(message_id)
            await msg.delete()
            return {'success': True}
        except discord.NotFound:
            return {'success': False, 'error': 'Mensaje no encontrado'}
        except discord.Forbidden:
            return {'success': False, 'error': 'Sin permisos para borrar'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        result = asyncio.run_coroutine_threadsafe(_delete(), bot_loop).result(timeout=10)
        if result['success']:
            # Remove from local DB too
            try:
                conn = get_db_connection()
                conn.execute('DELETE FROM messages WHERE message_id = ?', (message_id,))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f'!!! [DELETE DB ERROR]: {e}')
            return jsonify({'status': 'deleted'})
        return jsonify({'error': result['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/messages/<int:message_id>/edit', methods=['POST'])
def edit_message(message_id):
    data = request.json or {}
    channel_id = data.get('channel_id')
    new_content = data.get('content', '').strip()
    if not channel_id or not new_content:
        return jsonify({'error': 'channel_id and content required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _edit():
        try:
            channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(message_id)
            await msg.edit(content=new_content)
            return {'success': True}
        except discord.NotFound:
            return {'success': False, 'error': 'Mensaje no encontrado'}
        except discord.Forbidden:
            return {'success': False, 'error': 'Solo puedes editar tus propios mensajes'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        result = asyncio.run_coroutine_threadsafe(_edit(), bot_loop).result(timeout=10)
        if result['success']:
            try:
                conn = get_db_connection()
                conn.execute('UPDATE messages SET content = ? WHERE message_id = ?',
                             (new_content, message_id))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f'!!! [EDIT DB ERROR]: {e}')
            return jsonify({'status': 'edited'})
        return jsonify({'error': result['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/messages/<int:message_id>/react', methods=['POST'])
def react_message(message_id):
    data = request.json or {}
    channel_id = data.get('channel_id')
    emoji = data.get('emoji', '')
    if not channel_id or not emoji:
        return jsonify({'error': 'channel_id and emoji required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _react():
        try:
            channel = await client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
            return {'success': True}
        except discord.NotFound:
            return {'success': False, 'error': 'Mensaje o emoji no encontrado'}
        except discord.Forbidden:
            return {'success': False, 'error': 'Sin permisos para reaccionar'}
        except discord.HTTPException as e:
            return {'success': False, 'error': f'Emoji inválido: {e}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        result = asyncio.run_coroutine_threadsafe(_react(), bot_loop).result(timeout=10)
        if result['success']:
            return jsonify({'status': 'reacted'})
        return jsonify({'error': result['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def run_discord_bot():
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    if TOKEN:
        try: bot_loop.run_until_complete(client.start(TOKEN))
        except Exception as e: print(f"!!! [BOT CRASH]: {e}")


def start_periodic_sync_thread():
    """Start a background thread that periodically triggers history sync via bot_loop.
    This runs in a normal Python thread and schedules the async task on the bot event loop.
    """
    def _runner():
        # wait until bot is ready
        bot_ready_event.wait()
        try:
            interval = int(os.getenv('HISTORY_INTERVAL_MINUTES', '10'))
        except ValueError:
            interval = 10

        history_limit = None
        try:
            history_limit = int(os.getenv('HISTORY_LIMIT', '1000'))
        except ValueError:
            history_limit = 1000

        while True:
            try:
                if bot_loop:
                    print(f">>> [PERIODIC SYNC] scheduling sync (limit={history_limit})")
                    asyncio.run_coroutine_threadsafe(sync_category_history(history_limit), bot_loop)
            except Exception as e:
                print(f"!!! [PERIODIC SYNC ERROR]: {e}")
            # sleep minutes
            try:
                for _ in range(interval * 6):
                    # short sleeps to be responsive to shutdown (not strictly necessary)
                    threading.Event().wait(10)
            except Exception:
                threading.Event().wait(interval * 60)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

# Run DB init at module level so Render/gunicorn workers get the tables too
try:
    init_db()
    ensure_author_id_column()
except Exception as _e:
    print(f'!!! [STARTUP DB INIT]: {_e}')


@app.route('/api/messages/<int:message_id>/interact', methods=['POST'])
def interact_message(message_id):
    """
    Los bots de Discord no pueden hacer click en botones de otros mensajes —
    esa acción es exclusiva de usuarios (self-bot), que Discord prohíbe.
    Los botones se muestran en el UI de forma visual pero deben pulsarse
    directamente en el cliente de Discord.
    """
    return jsonify({
        'error': 'Los botones de Discord deben pulsarse directamente en Discord. '
                 'Los bots no pueden interactuar con botones de otros mensajes.'
    }), 501


# ═══════════════════════════════════════════
# MOD PANEL API ENDPOINTS
# ═══════════════════════════════════════════

@app.route('/api/mod/users')
def mod_users():
    """All users with at least 1 warning, with warn count and latest action."""
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT user_id, MAX(user_name) as user_name, guild_id,
               COUNT(*) as warn_count, MAX(timestamp) as last_warn
        FROM warnings
        GROUP BY user_id
        ORDER BY warn_count DESC, last_warn DESC
    ''').fetchall()

    users = []
    for row in rows:
        uid  = row['user_id']
        gid  = row['guild_id'] or ''
        warns = conn.execute(
            'SELECT reason, timestamp, moderator_name FROM warnings WHERE user_id=? ORDER BY timestamp DESC',
            (uid,)
        ).fetchall()
        action = conn.execute(
            'SELECT action, timestamp, reason FROM mod_actions WHERE user_id=? ORDER BY timestamp DESC LIMIT 1',
            (uid,)
        ).fetchone()
        users.append({
            'user_id':    uid,
            'user_name':  row['user_name'],
            'guild_id':   gid,
            'warn_count': row['warn_count'],
            'last_warn':  row['last_warn'],
            'warnings':   [dict(w) for w in warns],
            'last_action': dict(action) if action else None,
        })
    conn.close()
    return jsonify(users)


@app.route('/api/mod/warn', methods=['POST'])
def mod_warn():
    """Manually add a warning to a user."""
    data   = request.json or {}
    uid    = data.get('user_id')
    uname  = data.get('user_name', 'Unknown')
    gid    = data.get('guild_id')
    reason = data.get('reason', 'Manual warn')
    if not uid or not gid:
        return jsonify({'error': 'user_id and guild_id required'}), 400
    count = add_warning(uid, uname, gid, reason, mod_id='WEB', mod_name='WebMod')
    return jsonify({'status': 'warned', 'warn_count': count})


@app.route('/api/mod/timeout', methods=['POST'])
def mod_timeout():
    """Apply a timeout to a user."""
    data     = request.json or {}
    uid      = data.get('user_id')
    gid      = data.get('guild_id')
    duration = int(data.get('duration_seconds', 3600))
    reason   = data.get('reason', 'Manual timeout')
    if not uid or not gid:
        return jsonify({'error': 'user_id and guild_id required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _do():
        import datetime
        try:
            guild  = await client.fetch_guild(int(gid))
            member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            until  = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration)
            await member.timeout(until, reason=reason)
            log_mod_action(uid, member.name, gid, 'timeout', reason, duration, 'WEB', 'WebMod')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        r = asyncio.run_coroutine_threadsafe(_do(), bot_loop).result(timeout=10)
        return jsonify(r) if r['success'] else jsonify({'error': r['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mod/kick', methods=['POST'])
def mod_kick():
    """Kick a user from the guild."""
    data   = request.json or {}
    uid    = data.get('user_id')
    gid    = data.get('guild_id')
    reason = data.get('reason', 'Manual kick')
    if not uid or not gid:
        return jsonify({'error': 'user_id and guild_id required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _do():
        try:
            guild  = await client.fetch_guild(int(gid))
            member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            await member.kick(reason=reason)
            log_mod_action(uid, member.name, gid, 'kick', reason, None, 'WEB', 'WebMod')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        r = asyncio.run_coroutine_threadsafe(_do(), bot_loop).result(timeout=10)
        return jsonify(r) if r['success'] else jsonify({'error': r['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mod/ban', methods=['POST'])
def mod_ban():
    """Ban a user."""
    data   = request.json or {}
    uid    = data.get('user_id')
    gid    = data.get('guild_id')
    reason = data.get('reason', 'Manual ban')
    days   = int(data.get('delete_days', 0))
    if not uid or not gid:
        return jsonify({'error': 'user_id and guild_id required'}), 400
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({'error': 'Bot desconectado'}), 503

    async def _do():
        try:
            guild  = await client.fetch_guild(int(gid))
            member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            await member.ban(reason=reason, delete_message_days=days)
            log_mod_action(uid, member.name, gid, 'ban', reason, None, 'WEB', 'WebMod')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    try:
        r = asyncio.run_coroutine_threadsafe(_do(), bot_loop).result(timeout=10)
        return jsonify(r) if r['success'] else jsonify({'error': r['error']}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mod/clear_warns', methods=['POST'])
def mod_clear_warns():
    """Clear all warnings for a user."""
    data = request.json or {}
    uid  = data.get('user_id')
    gid  = data.get('guild_id')
    if not uid or not gid:
        return jsonify({'error': 'user_id and guild_id required'}), 400
    conn = get_db_connection()
    conn.execute('DELETE FROM warnings WHERE user_id=? AND guild_id=?', (uid, gid))
    conn.commit()
    conn.close()
    return jsonify({'status': 'cleared'})

@app.route('/api/mod/action_log')
def mod_action_log():
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT * FROM mod_actions ORDER BY timestamp DESC LIMIT 200'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

if __name__ == '__main__':
    init_db()
    # Ensure older databases get the new `author_id` column without destructive reset
    try:
        ensure_author_id_column()
    except Exception as e:
        print(f"!!! [MIGRATION STARTUP ERROR]: {e}")
    t = threading.Thread(target=run_discord_bot, daemon=True)
    t.start()
    # Start periodic background sync thread so missed or old messages are backfilled
    start_periodic_sync_thread()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)