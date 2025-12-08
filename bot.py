import discord
import sqlite3
import threading
import os
import asyncio
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Cargar variables locales si estás probando en PC 
# (En Render, estas variables se configuran en el Dashboard)
load_dotenv()

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

# TOKEN: Asegúrate de tenerlo en tu .env o variables de Render
TOKEN = os.getenv("DISCORD_TOKEN") 

# ID de la categoría donde el bot puede leer/escribir
# CAMBIA ESTO por la ID de tu categoría real en Discord
try:
    TARGET_CATEGORY_ID = int(os.getenv("TARGET_CATEGORY_ID", 1355062396322058287))
except ValueError:
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
    """Inicializa la tabla de mensajes si no existe."""
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
        print(">>> [DB]: Base de datos inicializada correctamente.")
    except Exception as e:
        print(f"!!! [DB ERROR]: {e}")

# --- EVENTOS DEL BOT ---
@client.event
async def on_ready():
    print(f'>>> [DISCORD]: Conectado como {client.user}')
    print(f'>>> [DISCORD]: Escuchando en Categoría ID: {TARGET_CATEGORY_ID}')
    bot_ready_event.set() # Señalizamos que el bot está listo para recibir comandos

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Filtrar por categoría para no llenar la DB de mensajes de otros lados
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
                print(f">>> [MSG SAVED]: {message.author.name} en #{message.channel.name}")
            except Exception as e:
                print(f"!!! [SAVE ERROR]: {e}")

# --- RUTAS FLASK ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/channels')
def get_channels():
    """Devuelve la lista de canales disponibles en la categoría."""
    if not bot_ready_event.is_set():
        return jsonify({"error": "El bot se está iniciando..."}), 503

    # Función asíncrona interna para interactuar con Discord
    async def get_channels_async():
        try:
            # Intentamos obtener la categoría del caché
            category = client.get_channel(TARGET_CATEGORY_ID)
            
            # Si no está en caché, intentamos fetch (más lento pero seguro)
            if not category:
                try:
                    category = await client.fetch_channel(TARGET_CATEGORY_ID)
                except:
                    return []

            if category:
                # Filtramos solo canales de texto y ordenamos por posición
                text_channels = [c for c in category.channels if isinstance(c, discord.TextChannel)]
                text_channels.sort(key=lambda x: x.position)
                
                return [{
                    "id": c.id, 
                    "name": c.name
                } for c in text_channels]
            return []
        except Exception as e:
            print(f"Error fetching channels: {e}")
            return []

    # Ejecutamos la función async desde Flask de forma segura
    try:
        future = asyncio.run_coroutine_threadsafe(get_channels_async(), bot_loop)
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
    """
    Recibe mensaje de la web y lo manda a Discord.
    MEJORADO: Manejo de errores detallado.
    """
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    
    # 1. Validación básica
    if not channel_id or not content:
        return jsonify({"error": "Faltan datos (channel_id o content)"}), 400

    # 2. Verificar estado del bot
    if not bot_loop or not bot_ready_event.is_set():
        return jsonify({"error": "El bot no está conectado aún. Espera unos segundos."}), 503

    # 3. Definimos la tarea asíncrona con manejo de excepciones específico
    async def send_async():
        try:
            # Usamos fetch_channel para asegurar que existe y validar permisos
            channel = await client.fetch_channel(int(channel_id))
            await channel.send(content)
            return {"success": True}
        
        except discord.NotFound:
            return {"success": False, "error": "Canal no encontrado (ID inválida o borrado)."}
        except discord.Forbidden:
            return {"success": False, "error": "El bot no tiene permisos para escribir en este canal."}
        except Exception as e:
            print(f"!!! [SEND ERROR]: {e}")
            return {"success": False, "error": str(e)}

    # 4. Cruzamos el puente hacia el hilo del bot
    try:
        future = asyncio.run_coroutine_threadsafe(send_async(), bot_loop)
        result = future.result(timeout=10) # Timeout de 10s por si Discord va lento
        
        if result["success"]:
            return jsonify({"status": "Mensaje enviado"})
        else:
            # Devolvemos el error específico capturado en el bloque async
            return jsonify({"error": result["error"]}), 500
            
    except asyncio.TimeoutError:
        return jsonify({"error": "Discord tardó demasiado en responder."}), 504
    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- ARRANQUE ---
def run_discord_bot():
    global bot_loop
    # Crear nuevo loop para este hilo
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    if not TOKEN:
        print("!!! [CRITICAL]: NO SE ENCONTRÓ EL TOKEN. Revisa tus variables de entorno.")
        return

    try:
        bot_loop.run_until_complete(client.start(TOKEN))
    except Exception as e:
        print(f"!!! [BOT CRASH]: {e}")

if __name__ == '__main__':
    # 1. Inicializar DB
    init_db()
    
    # 2. Arrancar Bot en hilo separado
    t = threading.Thread(target=run_discord_bot, daemon=True)
    t.start()
    
    # 3. Servidor Web
    # Render asigna un puerto en la variable de entorno PORT automáticamente
    port = int(os.environ.get("PORT", 5000))
    print(f">>> [WEB]: Iniciando en puerto {port}...")
    
    # host='0.0.0.0' es OBLIGATORIO para Render
    app.run(host='0.0.0.0', port=port, debug=False)