import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request

# --- CONFIGURACIÓN ---
app = Flask(__name__)
TOKEN = os.getenv("DISCORD_TOKEN") 
# Asegúrate de que este ID sea correcto y el bot tenga acceso a ver/escribir en esos canales
TARGET_CATEGORY_ID = 1355062396322058287 

# --- SETUP DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Variable global para guardar el bucle de eventos del bot
bot_loop = None

# --- BASE DE DATOS ---
def get_db_connection():
    # check_same_thread=False es vital para que Flask y el Bot compartan la DB
    conn = sqlite3.connect('database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_automatically():
    """Inicializa la DB al arrancar para evitar errores en Render"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
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
        conn.commit()
        conn.close()
        print(">>> [SYSTEM]: DATABASE INTEGRITY VERIFIED.")
    except Exception as e:
        print(f"!!! [DB ERROR]: {e}")

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [BOT]: CONNECTED AS {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Verificar si el mensaje viene de la categoría correcta
    # Usamos hasattr para evitar errores si es un DM
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
                print(f">>> [MSG SAVED]: {message.author.name} in #{message.channel.name}")
            except Exception as e:
                print(f"!!! [SAVE ERROR]: {e}")

# --- RUTAS FLASK ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/messages')
def get_messages():
    try:
        conn = get_db_connection()
        # Traer los últimos 100 mensajes
        msgs = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 100').fetchall()
        conn.close()
        return jsonify([dict(row) for row in msgs])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/channels')
def get_channels():
    # Intento 1: Obtener canales en TIEMPO REAL desde Discord
    if client.is_ready():
        try:
            # Usamos fetch_channel para asegurar que tenemos datos frescos
            # Nota: fetch_channel es asíncrono, pero aquí estamos en contexto síncrono.
            # Usaremos get_channel que lee de caché, si falla, el usuario verá la lista vacía hasta que el bot cargue.
            category = client.get_channel(TARGET_CATEGORY_ID)
            
            if category:
                # Ordenar por posición
                channels = sorted(category.channels, key=lambda x: x.position)
                return jsonify([{
                    "channel_name": c.name, 
                    "channel_id": c.id
                } for c in channels])
            else:
                print("!!! [ERROR]: Category ID not found in cache. Bot might need a restart or ID is wrong.")
        except Exception as e:
            print(f"Error live channels: {e}")

    # Intento 2: Fallback a Base de Datos (si el bot no está listo)
    conn = get_db_connection()
    channels = conn.execute('SELECT DISTINCT channel_name, channel_id FROM messages').fetchall()
    conn.close()
    return jsonify([dict(row) for row in channels])

@app.route('/api/send', methods=['POST'])
def send_message():
    """
    Ruta crítica para enviar mensajes.
    Usa fetch_channel y run_coroutine_threadsafe.
    """
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    if not channel_id or not content:
        return jsonify({"error": "Missing data"}), 400

    if not bot_loop:
        return jsonify({"error": "Bot loop not ready"}), 500

    # Función interna asíncrona para hacer el envío
    async def send_async_task():
        try:
            # fetch_channel hace una llamada API (más lento pero seguro)
            # get_channel usa caché (rápido pero a veces falla si no está en caché)
            channel = await client.fetch_channel(int(channel_id))
            await channel.send(content)
            return True
        except Exception as e:
            print(f"!!! [SEND ERROR]: {e}")
            return False

    # Ejecutar la tarea en el hilo del bot
    try:
        future = asyncio.run_coroutine_threadsafe(send_async_task(), bot_loop)
        # Esperamos el resultado (timeout de 5s para no bloquear la web si Discord va lento)
        success = future.result(timeout=5)
        
        if success:
            return jsonify({"status": "sent"})
        else:
            return jsonify({"error": "Discord Error"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ARRANQUE ---
def run_discord_bot():
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    if not TOKEN:
        print("!!! [CRITICAL]: NO TOKEN FOUND. CHECK ENV VARIABLES.")
        return

    try:
        bot_loop.run_until_complete(client.start(TOKEN))
    except Exception as e:
        print(f"!!! [BOT CRASH]: {e}")

if __name__ == '__main__':
    # 1. Inicializar DB
    init_db_automatically()
    
    # 2. Hilo del Bot
    t = threading.Thread(target=run_discord_bot)
    t.start()
    
    # 3. Servidor Web
    port = int(os.environ.get("PORT", 5000))
    # debug=False es importante al usar hilos
    app.run(host='0.0.0.0', port=port, debug=False)