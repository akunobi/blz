import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request

# --- CONFIGURACIÓN ---
app = Flask(__name__)
TOKEN = os.getenv("DISCORD_TOKEN")  # Recuerda configurar esto en Render
TARGET_CATEGORY_ID = 1355062396322058287

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

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
    if message.channel.category_id == TARGET_CATEGORY_ID:
        try:
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
            print(f">>> [DATA DEVOURED]: Mensaje guardado de {message.author.name}")
        except Exception as e:
            print(f"!!! [ERROR]: {e}")

# --- RUTAS DE FLASK (WEB) ---
@app.route('/')
def index():
    # Renderiza el HTML base
    return render_template('index.html')

@app.route('/api/messages')
def get_messages():
    # API para obtener mensajes en tiempo real (polling)
    conn = get_db_connection()
    messages = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT 50').fetchall()
    conn.close()
    
    # Convertir a lista de dicts
    msgs_list = [dict(row) for row in messages]
    return jsonify(msgs_list)

@app.route('/api/channels')
def get_channels():
    # Obtener lista de canales activos en la DB
    conn = get_db_connection()
    channels = conn.execute('SELECT DISTINCT channel_name, channel_id FROM messages').fetchall()
    conn.close()
    return jsonify([dict(row) for row in channels])

@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    if not channel_id or not content:
        return jsonify({"error": "Missing data"}), 400

    # Usamos run_coroutine_threadsafe para hablar con el bot desde Flask
    try:
        channel = client.get_channel(int(channel_id))
        if channel:
            asyncio.run_coroutine_threadsafe(channel.send(content), client.loop)
            return jsonify({"status": "sent"})
        else:
            return jsonify({"error": "Channel not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ARRANQUE HÍBRIDO ---
def run_discord_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(client.start(TOKEN))

if __name__ == '__main__':
    # Iniciar el bot en un hilo separado
    bot_thread = threading.Thread(target=run_discord_bot)
    bot_thread.start()
    
    # Iniciar servidor Web (Render usa puerto asignado por env o 5000 por defecto)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)