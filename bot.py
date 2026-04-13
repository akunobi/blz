import discord
from discord import app_commands
import sqlite3
import threading
import os
import asyncio
import json as _json_mod
import time as _time_gs
import urllib.request as _urllib_req
import urllib.parse as _urllib_parse
import re as _re_mod
import unicodedata as _ud
import collections
import hashlib
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN FLASK ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CATEGORY_ID = int(os.getenv("CATEGORY_ID", 0))

# ── CONFIGURACIÓN GOOGLE SHEETS ────────
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "19YQvEMF2NoDDLAdvqok8Nko3Hw14o5G9AmheOcMdUdE")
SHEETS_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")
WARN_DM_ADMIN_IDS = [898579360720764999, 1075463469865906216]

_REGION_ROLES = [
    (1355062394547736673, "EU",   3,  4,  5),
    (1355062394547736675, "NA",   7,  8,  9),
    (1355062394547736674, "ASIA", 11, 12, 13),
]
_SHEET_DATA_START_ROW = 15
_SHEET_TAB            = "Tracker"

# --- DISCORD CLIENT CON SLASH COMMANDS ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_messages = True
intents.reactions = True

class MyBot(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Sincroniza los comandos '/' con Discord al iniciar
        await self.tree.sync()

client = MyBot(intents=intents)
bot_loop = None
bot_ready_event = threading.Event()

# --- BASE DE DATOS ---
def get_db_connection():
    DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, channel_name TEXT,
        author_name TEXT, author_avatar TEXT, content TEXT, author_id INTEGER,
        message_id INTEGER UNIQUE, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, components TEXT DEFAULT NULL)''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, user_name TEXT,
        guild_id TEXT, reason TEXT, message_content TEXT DEFAULT NULL, message_link TEXT DEFAULT NULL,
        moderator_id TEXT, moderator_name TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS mod_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, user_name TEXT, guild_id TEXT,
        action TEXT NOT NULL, reason TEXT, duration_seconds INTEGER, moderator_id TEXT,
        moderator_name TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def log_mod_action(user_id, user_name, guild_id, action, reason, duration=None, mod_id='BOT', mod_name='AutoMod'):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO mod_actions (user_id, user_name, guild_id, action, reason, duration_seconds, moderator_id, moderator_name) VALUES (?,?,?,?,?,?,?,?)',
        (str(user_id), user_name, str(guild_id), action, reason, duration, str(mod_id), mod_name)
    )
    conn.commit()
    conn.close()

# --- AUTOMODERACIÓN ---
_LEET_DIGITS = str.maketrans({'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '6': 'g', '7': 't', '8': 'b', '9': 'g'})
_BAD_SINGLE = ['nigger', 'nigga', 'faggot', 'retard', 'bitch', 'asshole', 'kys', 'porn', 'cunt', 'cock'] # Agrega la lista completa
_BAD_PHRASES = ['kill yourself', 'hang yourself']

def _norm(text: str) -> str:
    t = _ud.normalize('NFKD', text)
    t = ''.join(c for c in t if not _ud.combining(c)).lower().translate(_LEET_DIGITS)
    return _re_mod.sub(r'[^a-z0-9]', ' ', t).strip()

def contains_bad_word(text: str):
    ns = _norm(text)
    toks = set(ns.split())
    
    for phrase in _BAD_PHRASES:
        if _norm(phrase) in ns:
            return phrase

    for word in _BAD_SINGLE:
        wn = _norm(word)
        for tok in toks:
            if tok == wn or tok.startswith(wn):
                return word
    return None

# --- EVENTOS DISCORD ---
@client.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    print(f'>>> [DISCORD]: Conectado como {client.user} y Comandos / Sincronizados')
    bot_ready_event.set()

@client.event
async def on_message(message):
    if message.author.bot:
        return

    # 1. FIX: AUTOMODERACIÓN (Prioridad absoluta con early return)
    bad_word = contains_bad_word(message.content)
    if bad_word:
        try:
            await message.delete()
            print(f">>> [AUTOMOD] Mensaje borrado de {message.author.name} por palabra bloqueada: {bad_word}")
            log_mod_action(message.author.id, message.author.name, message.guild.id, 'auto_delete', f"Contenía: {bad_word}")
        except Exception as e:
            print(f"!!! [AUTOMOD ERROR]: {e}")
        return  # FIX: El mensaje tóxico no llega a la base de datos ni afecta otros sistemas

    # Guardar en Base de Datos si pertenece a la categoría monitoreada
    if hasattr(message.channel, 'category') and message.channel.category and message.channel.category.id == TARGET_CATEGORY_ID:
        try:
            conn = get_db_connection()
            # FIX MENCIONES: message.clean_content procesa tags <@> a nombres legibles @Usuario
            content_to_save = message.clean_content 
            
            conn.execute('''
                INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                message.channel.id, message.channel.name, message.author.name,
                str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                content_to_save, message.author.id, message.id
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"!!! [DB ERROR]: {e}")

# --- SLASH COMMANDS (Migración de ! a /) ---
@client.tree.command(name="deadline", description="Establece un deadline de 24h para un usuario")
@app_commands.describe(usuario="El miembro objetivo del deadline")
async def deadline(interaction: discord.Interaction, usuario: discord.Member):
    ALLOWED_ROLE_IDS = {1355062394547736675, 1355062394547736673, 1483349943962964068}
    author_role_ids = {r.id for r in getattr(interaction.user, 'roles', [])}
    
    if not (author_role_ids & ALLOWED_ROLE_IDS):
        await interaction.response.send_message('No tienes permisos para usar este comando.', ephemeral=True)
        return

    # Responder al instante para evitar "La interacción ha fallado"
    await interaction.response.send_message(f"⌛ Iniciando tracker de deadline para {usuario.mention}...", ephemeral=False)
    
    # Lógica de deadline asíncrona adaptada
    import datetime
    deadline_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    unix_ts = int(deadline_dt.timestamp())

    embed = discord.Embed(
        title='Deadline — Confirmation Required',
        description=(f"{usuario.mention} you must confirm your availability within the next **24 hours**.\n\n"
                     f"React with ✅ to confirm.\nDeadline: <t:{unix_ts}:R>"),
        color=0xF5A623
    )
    embed.set_footer(text='If you do not confirm within 24h, the ticket will be marked as ready to close.')

    sent = await interaction.channel.send(embed=embed)
    await sent.add_reaction('✅')

    async def watch_reaction():
        try:
            def check(reaction, user):
                return str(reaction.emoji) == '✅' and reaction.message.id == sent.id and user.id == usuario.id
            
            await client.wait_for('reaction_add', check=check, timeout=86400)
            
            confirmed_embed = discord.Embed(
                title='Confirmed', description=f"{usuario.mention} has confirmed their availability.", color=0x26C9B8
            )
            await sent.edit(embed=confirmed_embed)
            await sent.clear_reactions()
        except asyncio.TimeoutError:
            close_embed = discord.Embed(
                title='Ticket Ready to Close', description=f"{usuario.mention} did not confirm within 24 hours.", color=0xFF6B6B
            )
            await interaction.channel.send(embed=close_embed)

    asyncio.create_task(watch_reaction())

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/messages', methods=['GET'])
def get_messages():
    try:
        conn = get_db_connection()
        limit = request.args.get('limit', 50)
        messages = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?', (limit,)).fetchall()
        conn.close()
        return jsonify([dict(m) for m in messages])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- INICIO DEL SISTEMA ---
def run_discord():
    client.run(TOKEN)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=run_discord, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
