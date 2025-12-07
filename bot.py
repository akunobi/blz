import discord
from discord.ext import tasks
import os
import asyncio

# --- IMPORTACIÓN HÍBRIDA ---
from app import app, db, Ticket, Message

# --- CONFIGURACIÓN ---
TOKEN = os.environ.get('DISCORD_TOKEN')

# ID de la Categoría
try:
    CATEGORY_ID = int(os.environ.get('CATEGORY_ID', '1355062396322058287'))
except ValueError:
    CATEGORY_ID = 1355062396322058287

# --- CONFIGURACIÓN DE ROLES DE REGIÓN ---
# Estos son los IDs que el bot buscará para asignar la región
ROLE_EU_ID = 1355062394547736673
ROLE_ASIA_ID = 1355062394547736674
ROLE_NA_ID = 1355062394547736675

print(f"Bot config loaded. Category: {CATEGORY_ID}")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)

# Función auxiliar para determinar región según menciones de rol
def detect_region_from_mentions(message_mentions, role_mentions, default="NA"):
    # Juntamos todas las menciones de roles (directas o via mensaje)
    all_role_ids = [r.id for r in role_mentions]
    
    if ROLE_EU_ID in all_role_ids:
        return "EU"
    elif ROLE_ASIA_ID in all_role_ids:
        return "ASIA"
    elif ROLE_NA_ID in all_role_ids:
        return "NA"
    return default

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    print("Starting Ticket Sync & Role Scan...")

    with app.app_context():
        found_count = 0
        for guild in client.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel) and channel.category_id == CATEGORY_ID:
                    
                    # 1. Determinar Región: Escaneamos los últimos 10 mensajes para buscar el ping del rol
                    region = "NA" # Valor por defecto si no hay ping
                    try:
                        # Leemos historial breve para encontrar el ping de bienvenida
                        async for msg in channel.history(limit=10, oldest_first=True):
                            found_region = detect_region_from_mentions(msg.mentions, msg.role_mentions, default=None)
                            if found_region:
                                region = found_region
                                break # Encontramos la región, dejamos de buscar
                    except Exception as e:
                        print(f"Could not read history for {channel.name}: {e}")

                    # 2. Sincronizar con DB
                    existing_ticket = Ticket.query.get(str(channel.id))
                    
                    if not existing_ticket:
                        # Si no existe, lo creamos con la región detectada
                        new_ticket = Ticket(id=str(channel.id), name=channel.name, region=region, status='open')
                        db.session.add(new_ticket)
                        found_count += 1
                    else:
                        # Si ya existe, ACTUALIZAMOS la región por si cambió el ping
                        if existing_ticket.region != region:
                            existing_ticket.region = region
                            db.session.add(existing_ticket) # Marcar para update
        
        db.session.commit()
        print(f"Sync complete. Updated/Created {found_count} tickets.")
    
    if not check_web_messages.is_running():
        check_web_messages.start()

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if hasattr(message.channel, 'category_id') and message.channel.category_id == CATEGORY_ID:
        
        with app.app_context():
            # 1. Buscar o Crear Ticket
            ticket = Ticket.query.get(str(message.channel.id))
            
            # Detectar si este mensaje contiene un ping de región para actualizar el ticket
            new_region = detect_region_from_mentions(message.mentions, message.role_mentions, default=None)

            if not ticket:
                # Si es un ticket nuevo, usamos la región del ping (o NA por defecto)
                final_region = new_region if new_region else "NA"
                ticket = Ticket(id=str(message.channel.id), name=message.channel.name, region=final_region, status='open')
                db.session.add(ticket)
            elif new_region:
                # Si el ticket ya existe Y este mensaje tiene un ping de región, ACTUALIZAMOS la región
                # Esto es útil si se pinguea al rol después de crear el canal
                if ticket.region != new_region:
                    ticket.region = new_region
                    db.session.add(ticket) # Guardar cambio de región

            # 2. Guardar Mensaje
            new_msg = Message(
                ticket_id=str(message.channel.id),
                sender=message.author.name,
                content=message.content,
                read_by_web=False,
                synced_to_discord=True
            )
            db.session.add(new_msg)
            db.session.commit()

@tasks.loop(seconds=3)
async def check_web_messages():
    with app.app_context():
        try:
            msgs_to_send = Message.query.filter_by(sender='WebAgent', synced_to_discord=False).all()
            
            for msg in msgs_to_send:
                try:
                    channel = client.get_channel(int(msg.ticket_id))
                    if channel:
                        embed_text = f"**[STAFF]:** {msg.content}"
                        await channel.send(embed_text)
                        msg.synced_to_discord = True
                        db.session.commit()
                except Exception as e:
                    print(f"Error sending to Discord {msg.ticket_id}: {e}")
        except Exception as e:
            print(f"DB polling error: {e}")

if __name__ == '__main__':
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not set in Environment.")
    else:
        client.run(TOKEN)