import discord
from discord.ext import tasks
import os
import asyncio

# --- IMPORTACIÓN HÍBRIDA ---
# Importamos la configuración de base de datos desde la aplicación web
from app import app, db, Ticket, Message

# --- CONFIGURACIÓN SEGURA PARA RENDER ---
# 1. El Token se lee de las Variables de Entorno de Render.
TOKEN = os.environ.get('DISCORD_TOKEN')

# 2. La Categoría también se lee del entorno, pero dejamos tu ID como respaldo (fallback)
#    por si se te olvida ponerla en Render.
try:
    CATEGORY_ID = int(os.environ.get('CATEGORY_ID', '1355062396322058287'))
except ValueError:
    CATEGORY_ID = 1355062396322058287

print(f"Bot configuration loaded. Category ID: {CATEGORY_ID}")

# Configuración de Intents (Permisos)
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    print("Starting Ticket Sync...")

    # Usamos el contexto de la aplicación Flask para acceder a la base de datos
    with app.app_context():
        found_count = 0
        for guild in client.guilds:
            for channel in guild.channels:
                # Verificar si es un canal de texto y está en la categoría correcta
                if isinstance(channel, discord.TextChannel) and channel.category_id == CATEGORY_ID:
                    
                    # Lógica para detectar región (EU/NA/ASIA)
                    region = "NA" # Valor por defecto
                    name = channel.name.lower()
                    if "eu" in name: region = "EU"
                    elif "asia" in name: region = "ASIA"
                    
                    # Sincronizar con la base de datos
                    existing_ticket = Ticket.query.get(str(channel.id))
                    if not existing_ticket:
                        new_ticket = Ticket(
                            id=str(channel.id), 
                            name=channel.name, 
                            region=region, 
                            status='open'
                        )
                        db.session.add(new_ticket)
                        found_count += 1
        
        db.session.commit()
        print(f"Sync complete. Registered {found_count} new tickets from Discord.")
    
    # Iniciar la tarea repetitiva (bucle) si no está corriendo ya
    if not check_web_messages.is_running():
        check_web_messages.start()

@client.event
async def on_message(message):
    # Ignorar mensajes propios
    if message.author == client.user:
        return

    # Verificar si el mensaje está en la categoría monitoreada
    if hasattr(message.channel, 'category_id') and message.channel.category_id == CATEGORY_ID:
        
        with app.app_context():
            # 1. Asegurar que el ticket existe en DB
            ticket = Ticket.query.get(str(message.channel.id))
            if not ticket:
                region = "NA"
                if "eu" in message.channel.name.lower(): region = "EU"
                elif "asia" in message.channel.name.lower(): region = "ASIA"
                
                ticket = Ticket(
                    id=str(message.channel.id), 
                    name=message.channel.name, 
                    region=region, 
                    status='open'
                )
                db.session.add(ticket)

            # 2. Guardar el mensaje del usuario
            new_msg = Message(
                ticket_id=str(message.channel.id),
                sender=message.author.name,
                content=message.content,
                read_by_web=False,     # La web debe marcarlo como leído después
                synced_to_discord=True # Ya viene de Discord
            )
            db.session.add(new_msg)
            db.session.commit()

# --- TAREA CÍCLICA: Enviar mensajes de la Web a Discord ---
@tasks.loop(seconds=3)
async def check_web_messages():
    with app.app_context():
        try:
            # Buscar mensajes creados por la Web ('WebAgent') que aún no se han enviado a Discord
            msgs_to_send = Message.query.filter_by(sender='WebAgent', synced_to_discord=False).all()
            
            for msg in msgs_to_send:
                try:
                    channel = client.get_channel(int(msg.ticket_id))
                    if channel:
                        # Formato visual del mensaje en Discord
                        embed_text = f"**[STAFF]:** {msg.content}"
                        await channel.send(embed_text)
                        
                        # Marcar como sincronizado para no enviarlo doble
                        msg.synced_to_discord = True
                        db.session.commit()
                except Exception as e:
                    print(f"Error sending message to Discord channel {msg.ticket_id}: {e}")
                    
        except Exception as e:
            print(f"Database polling error: {e}")

# --- ARRANQUE DEL BOT ---
if __name__ == '__main__':
    if not TOKEN:
        # Esto aparecerá en los logs de Render si olvidaste poner la variable
        print("ERROR CRÍTICO: No se encontró la variable de entorno DISCORD_TOKEN.")
        print("Por favor, ve a la pestaña 'Environment' en Render y añade tu token.")
    else:
        client.run(TOKEN)