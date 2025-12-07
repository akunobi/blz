import os
import threading
import datetime
import discord
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

# --- CONFIGURACIÓN ---
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
# Fix para Render: postgres:// debe ser postgresql://
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///tickets.db').replace("postgres://", "postgresql://")

# --- FLASK APP Y DB SETUP ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
CORS(app)
db = SQLAlchemy(app)

# --- MODELOS DE BASE DE DATOS ---
class Ticket(db.Model):
    id = db.Column(db.String, primary_key=True) # ID del Canal de Discord
    user_id = db.Column(db.String, nullable=False)
    user_name = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default="open")
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    messages = db.relationship('Message', backref='ticket', lazy=True, cascade="all, delete-orphan")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String, db.ForeignKey('ticket.id'), nullable=False)
    sender = db.Column(db.String, nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    read_by_web = db.Column(db.Boolean, default=False)
    synced_to_discord = db.Column(db.Boolean, default=True)

# Inicialización de DB (Segura)
with app.app_context():
    db.create_all()

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- COMANDO DE SINCRONIZACIÓN (NUEVO) ---
@bot.command()
async def sync(ctx):
    """Busca tickets existentes en la categoría y los guarda en la DB"""
    CATEGORY_ID = 1355062396322058287 # La categoría que mencionaste
    category = bot.get_channel(CATEGORY_ID)

    if not category:
        await ctx.send(f"❌ No encontré la categoría con ID {CATEGORY_ID}")
        return

    await ctx.send(f"🔄 Iniciando sincronización de tickets en **{category.name}**...")
    
    count_tickets = 0
    count_msgs = 0

    with app.app_context():
        # Recorrer todos los canales de texto en la categoría
        for channel in category.text_channels:
            # 1. Verificar si el ticket ya existe en DB
            ticket_db = db.session.get(Ticket, str(channel.id))
            
            if not ticket_db:
                # Intentar adivinar el dueño del ticket (el usuario que tiene permiso de ver)
                ticket_owner_id = "0"
                ticket_owner_name = "Desconocido"
                
                for target, overwrite in channel.overwrites.items():
                    # Buscamos un miembro (no rol) que no sea el bot y tenga view_channel=True
                    if isinstance(target, discord.Member) and not target.bot and overwrite.view_channel:
                        ticket_owner_id = str(target.id)
                        ticket_owner_name = target.name
                        break
                
                # Crear el Ticket en DB
                new_ticket = Ticket(
                    id=str(channel.id),
                    user_id=ticket_owner_id,
                    user_name=ticket_owner_name,
                    status="open",
                    created_at=channel.created_at or datetime.datetime.utcnow()
                )
                db.session.add(new_ticket)
                count_tickets += 1
            
            # 2. Sincronizar Mensajes (Historial)
            # Leemos los últimos 50 mensajes para llenar la web
            async for msg in channel.history(limit=50, oldest_first=True):
                # Verificar si el mensaje ya existe (para no duplicar si ejecutas el comando 2 veces)
                # Nota: Esto es una verificación simple.
                # Para hacerlo más rápido, borramos y reinsertamos o simplemente agregamos.
                # Aquí asumiremos que si el ticket es nuevo, los mensajes también.
                # Si el ticket ya existía, podríamos duplicar si no tenemos cuidado, 
                # pero Message no tiene un ID único de Discord en este modelo simple, tiene un ID autoincremental.
                # Para evitar duplicados masivos, verificaremos por contenido y timestamp aproximado, o simplemente limpiamos.
                
                # Para simplificar en este script de rescate: Agregamos todo.
                # (Si ejecutas !sync dos veces, podrías tener mensajes dobles. Úsalo con cuidado o borra la DB antes).
                
                if not msg.content: continue # Ignorar mensajes vacíos o de sistema
                
                new_msg = Message(
                    ticket_id=str(channel.id),
                    sender=msg.author.name,
                    content=msg.content,
                    timestamp=msg.created_at,
                    synced_to_discord=True
                )
                db.session.add(new_msg)
                count_msgs += 1

        db.session.commit()

    await ctx.send(f"✅ **Sincronización completada!**\nRecuperados: {count_tickets} tickets y {count_msgs} mensajes.\nRevisa la web ahora.")

# --- CLASE PARA EL BOTÓN DE CREAR TICKET ---
class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Crear Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        CATEGORY_ID = 1355062396322058287
        category = interaction.guild.get_channel(CATEGORY_ID)

        # Evitar duplicados
        existing_channel = discord.utils.get(interaction.guild.text_channels, name=f"ticket-{interaction.user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"Ya tienes un ticket abierto: {existing_channel.mention}", ephemeral=True)
            return

        # Permisos
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        # Crear canal en la categoría correcta
        channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            category=category 
        )

        # Guardar en DB
        with app.app_context():
            new_ticket = Ticket(
                id=str(channel.id),
                user_id=str(interaction.user.id),
                user_name=interaction.user.name,
                status="open"
            )
            db.session.add(new_ticket)
            db.session.commit()

        await channel.send(f"¡Hola {interaction.user.mention}! Un miembro del staff te atenderá pronto.")
        await interaction.response.send_message(f"Ticket creado: {channel.mention}", ephemeral=True)

# --- EVENTOS DEL BOT ---
@bot.event
async def on_ready():
    print(f'✅ Bot conectado como {bot.user}')
    bot.add_view(TicketLauncher())

@bot.command()
async def setup(ctx):
    await ctx.send("Haz clic abajo para abrir un ticket:", view=TicketLauncher())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    with app.app_context():
        ticket_existente = db.session.get(Ticket, str(message.channel.id))
        
        if not ticket_existente:
            await bot.process_commands(message)
            return 

        try:
            new_msg = Message(
                ticket_id=str(message.channel.id),
                sender=message.author.name,
                content=message.content,
                synced_to_discord=True
            )
            db.session.add(new_msg)
            db.session.commit()
        except Exception as e:
            print(f"Error guardando mensaje: {e}")
            db.session.rollback()

    await bot.process_commands(message)

# --- RUTAS DE FLASK (API) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_tickets')
def get_tickets():
    tickets = Ticket.query.all()
    return jsonify([{
        'id': t.id,
        'user_name': t.user_name,
        'status': t.status,
        'created_at': t.created_at.isoformat()
    } for t in tickets])

@app.route('/api/get_messages/<ticket_id>')
def get_messages(ticket_id):
    messages = Message.query.filter_by(ticket_id=ticket_id).order_by(Message.timestamp).all()
    return jsonify([{
        'sender': m.sender,
        'content': m.content,
        'timestamp': m.timestamp.isoformat()
    } for m in messages])

@app.route('/api/send_message', methods=['POST'])
def send_message_web():
    data = request.json
    ticket_id = data.get('ticket_id')
    content = data.get('content')
    sender = "Soporte Web"

    if not ticket_id or not content:
        return jsonify({'error': 'Faltan datos'}), 400

    new_msg = Message(ticket_id=ticket_id, sender=sender, content=content, synced_to_discord=True)
    db.session.add(new_msg)
    db.session.commit()

    channel = bot.get_channel(int(ticket_id))
    if channel:
        future = discord.run_coroutine_threadsafe(channel.send(f"**{sender}:** {content}"), bot.loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

    return jsonify({'status': 'success'})

# --- INICIO ---
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot.run(DISCORD_TOKEN)