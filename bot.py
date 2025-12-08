import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

# TOKEN: Asegúrate de tenerlo en tu .env o variables de Render
TOKEN = os.getenv("DISCORD_TOKEN") 

# ID de la categoría (Intenta convertirlo a int, si falla usa el default)
try:
    TARGET_CATEGORY_ID = int(os.getenv("TARGET_CATEGORY_ID", 1355062396322058287))
except ValueError:
    TARGET_CATEGORY_ID = 1355062396322058287 

# --- SETUP DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Variables globales para el hilo del bot
bot_loop = None
bot_ready_event = threading.Event()

# --- BASE DE DATOS ---
def get_db_connection():
    # check_same_thread=False es vital para SQLite con hilos
    conn = sqlite3.connect('database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Crea la tabla de mensajes si no existe."""
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
        print(">>> [DB]: Base de datos lista.")
    except Exception as e:
        print(f"!!! [DB ERROR]: {e}")

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    print(f'>>> [DISCORD]: ID Categoría Objetivo: {TARGET_CATEGORY_ID}')
    bot_ready_event.set()

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Guardar mensaje solo si pertenece a la categoría correcta
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

@app.route('/api/channels')
def get_channels():
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot iniciando..."}), 503

    async def get_channels_async():
        try:
            category = client.get_channel(TARGET_CATEGORY_ID)
            # Si no está en caché, intentar fetch
            if not category:
                try:
                    category = await client.fetch_channel(TARGET_CATEGORY_ID)
                except:
                    return []
            
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
        return jsonify(channels)
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
    """
    Ruta con depuración detallada para diagnosticar errores de envío.
    """
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    # --- LOGS DE DEPURACIÓN ---
    print(f"\n>>> [WEB REQUEST] Enviar mensaje:")
    print(f"    - Canal ID Raw: {channel_id} ({type(channel_id)})")
    print(f"    - Contenido: {content}")
    
    if not channel_id or not content:
        return jsonify({"error": "Faltan datos"}), 400

    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "Bot desconectado"}), 503

    async def send_async():
        try:
            c_id = int(channel_id)
            # Usamos fetch_channel para forzar validación con la API de Discord
            channel = await client.fetch_channel(c_id)
            print(f"    - Canal encontrado: #{channel.name} (ID: {channel.id})")
            
            await channel.send(content)
            print(f"    - ¡Mensaje enviado con éxito!")
            return {"success": True}
        
        except ValueError:
            print("!!! [ERROR] ID no es un entero válido.")
            return {"success": False, "error": "ID de canal inválida."}
        except discord.NotFound:
            print(f"!!! [ERROR] Discord: Canal {channel_id} NO ENCONTRADO.")
            return {"success": False, "error": "Canal no encontrado o borrado."}
        except discord.Forbidden:
            print(f"!!! [ERROR] Discord: SIN PERMISOS en canal {channel_id}.")
            return {"success": False, "error": "Bot sin permisos para escribir."}
        except Exception as e:
            print(f"!!! [ERROR CRÍTICO]: {e}")
            return {"success": False, "error": str(e)}

    try:
        future = asyncio.run_coroutine_threadsafe(send_async(), bot_loop)
        result = future.result(timeout=10)
        
        if result["success"]:
            return jsonify({"status": "OK"})
        else:
            return jsonify({"error": result["error"]}), 500
            
    except Exception as e:
        return jsonify({"error": f"Timeout/Internal: {e}"}), 500

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
    # 1. DB
    init_db()
    
    # 2. Hilo Bot
    t = threading.Thread(target=run_discord_bot, daemon=True)
    t.start()
    
    # 3. Web
    port = int(os.environ.get("PORT", 5000))
    print(f">>> [WEB]: Iniciando en puerto {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)