import discord
from discord.ext import tasks
import os
import asyncio

# --- IMPORTACIÓN HÍBRIDA ---
from app import app, db, Ticket, Message

# --- CONFIGURACIÓN SEGURA ---
# Intenta leer del entorno (Render).
TOKEN = os.environ.get('DISCORD_TOKEN')

# Intenta leer la categoría del entorno, o usa tu ID por defecto
try:
    CATEGORY_ID = int(os.environ.get('CATEGORY_ID', '1355062396322058287'))
except ValueError:
    CATEGORY_ID = 1355062396322058287

print(f"Bot configuration loaded. Category ID: {CATEGORY_ID}")

# Configuración de Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    print("Starting Ticket Sync...")

    with app.app_context():
        found_count = 0
        for guild in client.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel) and channel.category_id == CATEGORY_ID:
                    
                    region = "NA"
                    name = channel.name.lower()
                    if "eu" in name: region = "EU"
                    elif "asia" in name: region = "ASIA"
                    
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
    
    if not check_web_messages.is_running():
        check_web_messages.start()

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if hasattr(message.channel, 'category_id') and message.channel.category_id == CATEGORY_ID:
        
        with app.app_context():
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
                    print(f"Error sending message to Discord channel {msg.ticket_id}: {e}")
                    
        except Exception as e:
            print(f"Database polling error: {e}")

if __name__ == '__main__':
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN environment variable not set.")
    else:
        client.run(TOKEN)