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
    conn = sqlite3.connect('database.db', check_same_thread=False)
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
                message_id INTEGER UNIQUE,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR]: {e}")

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
                    INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, message_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    message.channel.id,
                    message.channel.name,
                    message.author.name,
                    str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                    message.content,
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
                                INSERT OR IGNORE INTO messages (channel_id, channel_name, author_name, author_avatar, content, message_id, timestamp)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                c.id,
                                c.name,
                                msg.author.name,
                                str(msg.author.avatar.url) if msg.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                                msg.content,
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

@app.route('/api/admin/reset')
def reset_database():
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM messages')
        conn.execute('VACUUM')
        conn.commit()
        conn.close()
        return jsonify({"status": "SUCCESS", "message": "Base de datos purgada."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

@app.route('/api/messages')
def get_messages():
    try:
        conn = get_db_connection()
        msgs = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 50').fetchall()
        conn.close()
        results = []
        for row in msgs:
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
            await channel.send(content)
            return {"success": True}
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
        
        if result["success"]: return jsonify({"status": "OK"})
        else: return jsonify({"error": result["error"]}), 400
            
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
    t = threading.Thread(target=run_discord_bot, daemon=True)
    t.start()
    # Start periodic background sync thread so missed or old messages are backfilled
    start_periodic_sync_thread()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)