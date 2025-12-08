import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Cargar variables
load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

TOKEN = os.getenv("DISCORD_TOKEN") 

# Configuración de Categoría (Render vs Local)
try:
    raw_id = os.getenv("CATEGORY_ID")
    if not raw_id:
        print("!!! [ALERTA] Variable CATEGORY_ID no encontrada. Usando 0.")
        TARGET_CATEGORY_ID = 0
    else:
        TARGET_CATEGORY_ID = int(raw_id)
except ValueError:
    TARGET_CATEGORY_ID = 0

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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"!!! [DB ERROR]: {e}")

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    print(f'>>> [DISCORD]: ID Categoría: {TARGET_CATEGORY_ID}')
    bot_ready_event.set()

@client.event
async def on_message(message):
    if message.author == client.user: return

    # Guardar solo mensajes de la categoría correcta
    if hasattr(message.channel, 'category') and message.channel.category:
        if message.channel.category.id == TARGET_CATEGORY_ID:
            try:
                conn = get_db_connection()
                conn.execute('''
                    INSERT INTO messages (channel_id, channel_name, author_name, author_avatar, content)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    message.channel.id,
                    message.channel.name,
                    message.author.name,
                    str(message.author.avatar.url) if message.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png",
                    message.content
                ))
                conn.commit()
                conn.close()
                print(f">>> [MSG SAVED]: {message.author.name} -> #{message.channel.name}")
            except Exception as e:
                print(f"!!! [SAVE ERROR]: {e}")

# --- RUTAS FLASK ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/admin/reset')
def reset_database():
    """Limpia la base de datos sin borrar el archivo físico (más seguro)."""
    try:
        conn = get_db_connection()
        # Usamos DELETE en lugar de DROP para evitar bloqueos de archivo
        cursor = conn.execute('DELETE FROM messages')
        deleted_count = cursor.rowcount
        
        # VACUUM reduce el tamaño del archivo tras borrar
        conn.execute('VACUUM')
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "SUCCESS", 
            "message": f"Base de datos limpiada. {deleted_count} mensajes eliminados."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/channels')
def get_channels():
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot iniciando..."}), 503

    async def get_channels_async():
        try:
            # 1. Intentar obtener canales EN VIVO desde Discord
            category = client.get_channel(TARGET_CATEGORY_ID)
            if not category:
                try:
                    category = await client.fetch_channel(TARGET_CATEGORY_ID)
                except:
                    pass
            
            if category:
                text_channels = [c for c in category.channels if isinstance(c, discord.TextChannel)]
                text_channels.sort(key=lambda x: x.position)
                return [{"id": c.id, "name": c.name} for c in text_channels]
            
            return []
        except Exception as e:
            print(f"Error channels: {e}")
            return []

    try:
        future = asyncio.run_coroutine_threadsafe(get_channels_async(), bot_loop)
        channels = future.result(timeout=5)
        
        if channels:
            return jsonify(channels)
        
        # 2. Fallback: Si Discord falla, leer de la DB
        conn = get_db_connection()
        db_chans = conn.execute('SELECT DISTINCT channel_name as name, channel_id as id FROM messages').fetchall()
        conn.close()
        return jsonify([dict(row) for row in db_chans])

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages')
def get_messages():
    try:
        conn = get_db_connection()
        msgs = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 50').fetchall()
        conn.close()
        return jsonify([dict(row) for row in msgs])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    print(f"\n>>> [WEB SEND] ID: {channel_id} | Msg: {content}")
    
    if not channel_id or not content:
        return jsonify({"error": "Datos incompletos"}), 400

    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "Bot desconectado"}), 503

    async def send_async():
        try:
            c_id = int(channel_id)
            channel = await client.fetch_channel(c_id)
            await channel.send(content)
            return {"success": True}
        except discord.NotFound:
            print(f"!!! [ERROR] Canal {c_id} NO EXISTE.")
            return {"success": False, "error": "Canal no encontrado (Borrado o ID vieja)."}
        except discord.Forbidden:
            print(f"!!! [ERROR] SIN PERMISOS en {c_id}.")
            return {"success": False, "error": "Sin permisos."}
        except Exception as e:
            print(f"!!! [ERROR]: {e}")
            return {"success": False, "error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(send_async(), bot_loop)
        result = future.result(timeout=10)
        
        if result["success"]:
            return jsonify({"status": "OK"})
        else:
            return jsonify({"error": result["error"]}), 500
            
    except Exception as e:
        return jsonify({"error": f"Internal: {e}"}), 500

# --- ARRANQUE ---
def run_discord_bot():
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    if not TOKEN:
        print("!!! [ERROR]: Falta DISCORD_TOKEN.")
        return

    try:
        bot_loop.run_until_complete(client.start(TOKEN))
    except Exception as e:
        print(f"!!! [BOT CRASH]: {e}")

if __name__ == '__main__':
    init_db()
    t = threading.Thread(target=run_discord_bot, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)