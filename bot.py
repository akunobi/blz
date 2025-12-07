import os
import threading
import datetime
import asyncio
import discord
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

# ==========================================
# 1. CONFIGURACIÓN E INICIO
# ==========================================

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
# Fix para Render: cambia postgres:// a postgresql:// automáticamente
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///tickets.db').replace("postgres://", "postgresql://")

# Configuración de Flask
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
CORS(app) # Permite peticiones cruzadas si fuera necesario
db = SQLAlchemy(app)

# Configuración del Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ==========================================
# 2. MODELOS DE BASE DE DATOS
# ==========================================

class Ticket(db.Model):
    id = db.Column(db.String, primary_key=True) # ID del Canal de Discord (único)
    user_id = db.Column(db.String, nullable=False) # ID del Usuario de Discord
    user_name = db.Column(db.String, nullable=False) # Nombre legible para mostrar en la web
    status = db.Column(db.String, default="open")
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String, db.ForeignKey('ticket.id'), nullable=False)
    sender = db.Column(db.String, nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    synced_to_discord = db.Column(db.Boolean, default=True)

# Crear tablas si no existen al iniciar
with app.app_context():
    db.create_all()

# ==========================================
# 3. LÓGICA DEL BOT DE DISCORD
# ==========================================

class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Crear Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        # --- CONFIGURACIÓN OPCIONAL ---
        # Si quieres que se creen en una categoría específica, pon su ID aquí (número entero).
        # Si lo dejas en None, se crearán arriba del todo.
        CATEGORY_ID = None 
        # Ejemplo: CATEGORY_ID = 123456789012345678
        
        category = None
        if CATEGORY_ID:
            category = interaction.guild.get_channel(CATEGORY_ID)

        # Usamos el ID del usuario para el nombre del canal (evita duplicados y caracteres raros)
        channel_name = f"ticket-{interaction.user.id}"
        
        # Verificar si ya existe un canal con ese nombre
        existing_channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if existing_channel:
            await interaction.response.send_message(f"⚠️ Ya tienes un ticket abierto: {existing_channel.mention}", ephemeral=True)
            return

        # Permisos: Solo el usuario y el bot pueden ver el canal
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        try:
            channel = await interaction.guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category
            )
        except Exception as e:
            await interaction.response.send_message(f"Error creando el canal: {e}", ephemeral=True)
            return

        # Guardar el Ticket en la Base de Datos
        # NOTA: Guardamos channel.id como la clave primaria
        with app.app_context():
            new_ticket = Ticket(
                id=str(channel.id),
                user_id=str(interaction.user.id),
                user_name=interaction.user.name,
                status="open"
            )
            db.session.add(new_ticket)
            db.session.commit()

        # Mensaje de bienvenida en el ticket
        embed = discord.Embed(title=f"Ticket de {interaction.user.name}", description="Describe tu problema aquí. El staff te atenderá pronto.", color=0x00ff00)
        await channel.send(content=f"{interaction.user.mention}", embed=embed)
        
        await interaction.response.send_message(f"✅ Ticket creado: {channel.mention}", ephemeral=True)

@bot.event
async def on_ready():
    print(f'✅ Bot conectado como: {bot.user}')
    # Esto hace que el botón sea persistente incluso si reinicias el bot
    bot.add_view(TicketLauncher())

@bot.command()
async def setup(ctx):
    """Comando para poner el panel de tickets"""
    await ctx.send("📩 **Soporte Técnico**\nHaz clic abajo para abrir un ticket privado.", view=TicketLauncher())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Sincronización Discord -> Web
    # Si se escribe en un canal que está registrado en la base de datos como ticket
    with app.app_context():
        ticket_db = db.session.get(Ticket, str(message.channel.id))
        
        if ticket_db:
            try:
                new_msg = Message(
                    ticket_id=str(message.channel.id),
                    sender=message.author.name,
                    content=message.content,
                    synced_to_discord=True # Ya está en Discord, no hay que reenviarlo
                )
                db.session.add(new_msg)
                db.session.commit()
            except Exception as e:
                print(f"Error guardando mensaje en DB: {e}")

    await bot.process_commands(message)

# ==========================================
# 4. API WEB (FLASK)
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_tickets')
def get_tickets():
    tickets = Ticket.query.all()
    # Devolvemos el ID del canal (para lógica) y el Nombre del usuario (para mostrar)
    return jsonify([{
        'id': t.id,
        'user_name': t.user_name,
        'status': t.status,
        'created_at': t.created_at.isoformat()
    } for t in tickets])

@app.route('/api/get_messages/<ticket_id>')
def get_messages(ticket_id):
    # Obtener mensajes ordenados por fecha
    messages = Message.query.filter_by(ticket_id=ticket_id).order_by(Message.timestamp).all()
    return jsonify([{
        'sender': m.sender,
        'content': m.content,
        'timestamp': m.timestamp.isoformat()
    } for m in messages])

@app.route('/api/send_message', methods=['POST'])
def send_message_web():
    """
    Recibe un mensaje desde la página web y lo envía al canal de Discord correspondiente.
    """
    data = request.json
    ticket_id = data.get('ticket_id') # ESTE DEBE SER EL ID DEL CANAL
    content = data.get('content')
    sender = "Soporte Web" # O el nombre del admin logueado

    if not ticket_id or not content:
        return jsonify({'error': 'Faltan datos'}), 400

    # 1. Guardar mensaje en Base de Datos
    new_msg = Message(ticket_id=ticket_id, sender=sender, content=content, synced_to_discord=True)
    db.session.add(new_msg)
    db.session.commit()

    # 2. Enviar a Discord (Magia de Threading)
    try:
        channel = bot.get_channel(int(ticket_id))
        if channel:
            # Usamos run_coroutine_threadsafe para hablar con el Bot desde Flask
            asyncio.run_coroutine_threadsafe(channel.send(f"**{sender}:** {content}"), bot.loop)
        else:
            return jsonify({'error': 'Canal de Discord no encontrado (quizás fue borrado)'}), 404
    except Exception as e:
        print(f"Error enviando a Discord: {e}")
        return jsonify({'error': str(e)}), 500

    return jsonify({'status': 'success'})

# ==========================================
# 5. EJECUCIÓN (THREADING)
# ==========================================

def run_flask():
    # Render asigna el puerto en la variable de entorno PORT
    port = int(os.environ.get("PORT", 5000))
    # '0.0.0.0' es obligatorio para que Render exponga la web
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    # Ejecutamos Flask en un hilo paralelo para no bloquear al Bot
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Ejecutamos el Bot en el hilo principal
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("❌ ERROR: No se encontró el DISCORD_TOKEN en las variables de entorno.")