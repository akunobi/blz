import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Cargar variables locales si estás probando en PC (en Render se configuran en el dashboard)
load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

# TOKEN: En Render, añádelo en las "Environment Variables"
TOKEN = os.getenv("DISCORD_TOKEN") 

# ID de la categoría donde el bot puede leer/escribir
# Asegúrate de que este número sea INT (sin comillas)
TARGET_CATEGORY_ID = 1355062396322058287 

# --- SETUP DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Variables globales para controlar el hilo del bot
bot_loop = None
bot_ready_event = threading.Event()

# --- BASE DE DATOS ---
def get_db_connection():
    # check_same_thread=False permite que Flask acceda a la DB creada por el Bot
    conn = sqlite3.connect('database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa la tabla si no existe."""
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
        print(f"[DB ERROR]: {e}")

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    bot_ready_event.set() # Señalizamos que el bot está listo

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Filtrar por categoría para no llenar la DB de basura
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
                    str(message.author.avatar.url) if message.author.avatar else "",
                    message.content
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[SAVE ERROR]: {e}")

# --- RUTAS FLASK ---

@app.route('/')
def index():
    # Flask buscará index.html dentro de la carpeta 'templates' que ya tienes
    return render_template('index.html')

@app.route('/api/channels')
def get_channels():
    """Devuelve la lista de canales disponibles en la categoría."""
    if not bot_ready_event.is_set():
        return jsonify({"error": "Bot is starting..."}), 503

    # Usamos una función async interna para pedirle datos al bot
    async def get_channels_async():
        try:
            category = client.get_channel(TARGET_CATEGORY_ID)
            if category:
                # Filtramos solo canales de texto
                return [{
                    "id": c.id, 
                    "name": c.name
                } for c in category.channels if isinstance(c, discord.TextChannel)]
            return []
        except Exception as e:
            print(f"Error fetching channels: {e}")
            return []

    # Ejecutamos la función async desde Flask
    future = asyncio.run_coroutine_threadsafe(get_channels_async(), bot_loop)
    try:
        channels = future.result(timeout=5)
        return jsonify(channels)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages')
def get_messages():
    """Lee mensajes de la DB local."""
    try:
        conn = get_db_connection()
        # Traemos los últimos 50 mensajes
        msgs = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 50').fetchall()
        conn.close()
        return jsonify([dict(row) for row in msgs])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/send', methods=['POST'])
def send_message():
    """Recibe mensaje de la web y lo manda a Discord."""
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    if not channel_id or not content:
        return jsonify({"error": "Datos incompletos"}), 400

    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "El bot no está conectado aún"}), 503

    # Definimos la tarea asíncrona de envío
    async def send_async():
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                # Si no está en caché, intentar fetch (más lento pero seguro)
                channel = await client.fetch_channel(int(channel_id))
            
            await channel.send(content)
            return True
        except Exception as e:
            print(f"[SEND ERROR]: {e}")
            return False

    # Cruzamos el puente hacia el hilo del bot
    future = asyncio.run_coroutine_threadsafe(send_async(), bot_loop)
    
    try:
        success = future.result(timeout=10)
        if success:
            return jsonify({"status": "Mensaje enviado"})
        else:
            return jsonify({"error": "No se pudo enviar al canal"}), 500
    except Exception as e:
        return jsonify({"error": "Timeout comunicando con Discord"}), 500

# --- INICIO ---
def run_bot_in_thread():
    global bot_loop
    # Creamos un nuevo loop para este hilo
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    if not TOKEN:
        print("!!! ERROR: No se encontró DISCORD_TOKEN")
        return

    bot_loop.run_until_complete(client.start(TOKEN))

if __name__ == '__main__':
    # 1. Inicializar DB
    init_db()
    
    # 2. Arrancar Bot en hilo separado
    t = threading.Thread(target=run_bot_in_thread, daemon=True)
    t.start()
    
    # 3. Arrancar Web (Configuración para Render)
    # Render asigna un puerto en la variable de entorno PORT
    port = int(os.environ.get("PORT", 5000))
    
    print(f">>> Iniciando Servidor Web en puerto {port}...")
    # host='0.0.0.0' es CRÍTICO para que Render exponga la web
    app.run(host='0.0.0.0', port=port, debug=False)