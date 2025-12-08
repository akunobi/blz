import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request

# --- CONFIGURACIÓN ---
app = Flask(__name__)
# El token debe estar en las Environment Variables de Render como DISCORD_TOKEN
TOKEN = os.getenv("DISCORD_TOKEN") 
TARGET_CATEGORY_ID = 1355062396322058287

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# --- GESTIÓN DE BASE DE DATOS ---
def get_db_connection():
    # check_same_thread=False permite que Flask y Discord accedan (aunque es mejor abrir/cerrar)
    conn = sqlite3.connect('database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_automatically():
    """
    Esta función se ejecuta SIEMPRE al iniciar para asegurar que las tablas existen.
    Soluciona el error 'no such table: messages' en Render.
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Tabla para mensajes (Sistema de Tickets/Chat)
        c.execute('''
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

        # Tabla simple para Stats (Opcional)
        c.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                data TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        print(">>> [SYSTEM]: DATABASE INTEGRITY VERIFIED. EGOIST MEMORY READY.")
    except Exception as e:
        print(f"!!! [CRITICAL ERROR]: DATABASE INIT FAILED: {e}")

# --- EVENTOS DE DISCORD ---
@client.event
async def on_ready():
    print(f'>>> [LOCKED IN]: Bot conectado como {client.user}')

@client.event
async def on_message(message):
    # Ignorar mensajes del propio bot
    if message.author == client.user:
        return

    # Verificar si el canal pertenece a la categoría especificada
    # Nota: A veces category_id puede ser None si es DM o canal suelto, protegemos con try
    try:
        if message.channel.category and message.channel.category.id == TARGET_CATEGORY_ID:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('''
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
            print(f">>> [DATA DEVOURED]: Mensaje guardado de {message.author.name} en #{message.channel.name}")
    except Exception as e:
        print(f"!!! [ERROR SAVING MSG]: {e}")

# --- RUTAS DE FLASK (WEB) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/messages')
def get_messages():
    try:
        conn = get_db_connection()
        # Traemos los últimos 100 mensajes para no saturar
        messages = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 100').fetchall()
        conn.close()
        return jsonify([dict(row) for row in messages])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/channels')
def get_channels():
    try:
        conn = get_db_connection()
        # Seleccionamos canales únicos que tengan mensajes guardados
        channels = conn.execute('SELECT DISTINCT channel_name, channel_id FROM messages').fetchall()
        conn.close()
        return jsonify([dict(row) for row in channels])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/send', methods=['POST'])
def send_message():
    """
    Permite enviar mensajes desde la Web hacia Discord.
    """
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    if not channel_id or not content:
        return jsonify({"error": "Missing data"}), 400

    try:
        # Convertimos ID a entero
        c_id_int = int(channel_id)
        
        # Obtenemos el canal desde el cache del bot
        channel = client.get_channel(c_id_int)
        
        if channel:
            # Ejecutar el envío asíncrono desde el hilo síncrono de Flask
            future = asyncio.run_coroutine_threadsafe(channel.send(content), client.loop)
            future.result() # Esperar a que se envíe (opcional, para confirmar éxito)
            return jsonify({"status": "sent"})
        else:
            return jsonify({"error": "Channel not found or Bot not ready"}), 404
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ARRANQUE HÍBRIDO ---
def run_discord_bot():
    if not TOKEN:
        print("!!! [ERROR]: NO DISCORD TOKEN FOUND. CHECK .ENV OR RENDER SETTINGS.")
        return
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.start(TOKEN))
    except Exception as e:
        print(f"!!! [BOT CRASH]: {e}")

if __name__ == '__main__':
    # 1. INICIALIZAR LA BASE DE DATOS ANTES DE NADA
    # Esto arregla el error "no such table: messages"
    init_db_automatically()

    # 2. Iniciar el bot en un hilo separado (background)
    bot_thread = threading.Thread(target=run_discord_bot)
    bot_thread.start()
    
    # 3. Iniciar servidor Web (Render asigna el puerto en la variable PORT)
    port = int(os.environ.get("PORT", 5000))
    # debug=False es importante en producción con hilos
    app.run(host='0.0.0.0', port=port, debug=False)