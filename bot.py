import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

TOKEN = os.getenv("DISCORD_TOKEN") 

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
client = discord.Client(intents=intents)

bot_loop = None
bot_ready_event = threading.Event()

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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR]: {e}")


def ensure_author_id_column():
    """Ensure `author_id` column exists on messages table; add it if missing.
    This performs a safe ALTER TABLE for older DBs created before this column existed.
    """
    try:
        conn = get_db_connection()
        cur = conn.execute("PRAGMA table_info(messages)").fetchall()
        cols = [r['name'] for r in cur]
        if 'author_id' not in cols:
            try:
                conn.execute('ALTER TABLE messages ADD COLUMN author_id INTEGER')
                conn.commit()
                print('>>> [DB MIGRATION] Added column author_id')
            except Exception as e:
                print(f'!!! [DB MIGRATION ERROR]: {e}')
        conn.close()
    except Exception as e:
        print(f'!!! [DB CHECK ERROR]: {e}')

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    print(f'>>> [DISCORD]: ID Categoría: {TARGET_CATEGORY_ID}')
    bot_ready_event.set()
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

@client.event
async def on_message(message):
    # NOTA: Eliminamos la restricción de 'client.user' para ver mensajes de la web
    
    if hasattr(message.channel, 'category') and message.channel.category:
        if message.channel.category.id == TARGET_CATEGORY_ID:
            try:
                conn = get_db_connection()
                conn.execute('''
                    INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    message.channel.id,
                    message.channel.name,
                    message.author.name,
                    str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                    message.content,
                    getattr(message.author, 'id', None),
                    message.id
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
                            conn.execute('''
                                INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, author_id, message_id, timestamp)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                c.id,
                                c.name,
                                msg.author.name,
                                str(msg.author.avatar.url) if msg.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                                msg.content,
                                getattr(msg.author, 'id', None),
                                msg.id,
                                msg.created_at.isoformat()
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
                rows = conn.execute('SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp ASC LIMIT ?', (cid, lim)).fetchall()
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
            d['channel_id'] = str(d['channel_id'])
            results.append(d)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
            return {"success": True, "message_id": sent.id, "author_id": getattr(sent.author, 'id', None), "author_name": str(sent.author.name), "author_avatar": (str(sent.author.avatar.url) if getattr(sent.author, 'avatar', None) else None), "channel_name": getattr(channel, 'name', None), "timestamp": sent.created_at.isoformat()}
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