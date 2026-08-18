# bot.py - BLZ-T Bot: Sistema de Matchmaking
import discord
from discord.ext import commands
import os
import re
import threading
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN DE LOGGING ---
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('blz-bot')

TOKEN = os.getenv("DISCORD_TOKEN")

# --- CONFIGURACIÓN DEL MATCHMAKING ---
GUILD_ID = 1538589344368164905          # Servidor
QUEUE_CHANNEL_ID = 1539158063116984361  # Canal donde vive el embed permanente de matchmaking
DUEL_CATEGORY_ID = 1539157638925918238  # Categoría donde se crean los canales privados de duelo

# --- FLASK MÍNIMO (solo para que Render mantenga el servicio vivo) ---
app = Flask(__name__)


@app.route("/")
def health():
    return jsonify({"status": "ok", "bot": "BLZ-T Matchmaking"}), 200


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# --- SETUP DISCORD BOT ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # necesario para resolver al rival por ID al emparejar

client = commands.Bot(command_prefix="!", intents=intents)
bot_ready_event = threading.Event()

# --- ESTADO DE LA COLA DE MATCHMAKING (en memoria) ---
matchmaking_queue: list[int] = []
queue_lock = asyncio.Lock()

DUEL_TOPIC_RE = re.compile(r'^duel-participants:(\d+):(\d+)$')


def _safe_channel_part(name: str) -> str:
    s = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')
    return s or 'jugador'


class CloseDuelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Cerrar Duelo",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="blz_duel_close"
    )
    async def close_duel(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        user = interaction.user

        topic = getattr(channel, 'topic', '') or ''
        match = DUEL_TOPIC_RE.match(topic)
        if not match:
            await interaction.response.send_message(
                "Este no es un canal de duelo válido.", ephemeral=True
            )
            return

        p1, p2 = int(match.group(1)), int(match.group(2))
        if user.id not in (p1, p2):
            await interaction.response.send_message(
                "❌ No tienes permiso para cerrar este duelo.", ephemeral=True
            )
            return

        button.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        try:
            await channel.send(
                f"🔒 Duelo cerrado por {user.mention}. Este canal se eliminará en 5 segundos..."
            )
        except Exception:
            pass

        logger.info(f">>> [DUEL] Cerrado por {user.name} ({user.id}) en #{channel.name}")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Duelo cerrado por {user.name}")
        except Exception as e:
            logger.error(f"!!! [DUEL DELETE ERROR]: {e}")


class MatchmakingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Buscar Duelo",
        style=discord.ButtonStyle.primary,
        emoji="⚔️",
        custom_id="blz_matchmaking_join"
    )
    async def join_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_queue_join(interaction)


async def create_duel_channel(guild: discord.Guild, user1: discord.Member, user2: discord.Member):
    category = guild.get_channel(DUEL_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(DUEL_CATEGORY_ID)
        except Exception as e:
            logger.error(f"!!! [DUEL] Categoría {DUEL_CATEGORY_ID} no encontrada: {e}")
            return None

    base_name = f"duelo-{_safe_channel_part(user1.name)}-{_safe_channel_part(user2.name)}"[:90]
    channel_name = base_name
    existing_names = {c.name for c in category.channels}
    suffix = 1
    while channel_name in existing_names:
        suffix += 1
        channel_name = f"{base_name}-{suffix}"[:90]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
            read_message_history=True,
        ),
    }
    for u in (user1, user2):
        overwrites[u] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
        )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"duel-participants:{user1.id}:{user2.id}",
            reason=f"Duelo de matchmaking entre {user1.name} y {user2.name}"
        )
    except discord.Forbidden:
        logger.error("!!! [DUEL] Sin permisos para crear el canal de duelo")
        return None
    except Exception as e:
        logger.error(f"!!! [DUEL CREATE ERROR]: {e}")
        return None

    embed = discord.Embed(
        title="⚔️ Duelo",
        description=(
            f"{user1.mention} vs {user2.mention}\n\n"
            f"¡Buena suerte a ambos! Cuando terminen, pulsen **Cerrar Duelo**."
        ),
        color=0xE63946
    )
    embed.set_footer(text="BLZ-T · Matchmaking")

    try:
        await channel.send(
            content=f"{user1.mention} {user2.mention}",
            embed=embed,
            view=CloseDuelView(),
            allowed_mentions=discord.AllowedMentions(users=True)
        )
    except Exception as e:
        logger.error(f"!!! [DUEL SEND ERROR]: {e}")

    logger.info(f">>> [DUEL] Canal creado: #{channel.name} ({user1.name} vs {user2.name})")
    return channel


async def handle_queue_join(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("Esto solo funciona dentro del servidor.", ephemeral=True)
        return

    already_in_queue = False
    opponent_id = None

    async with queue_lock:
        if user.id in matchmaking_queue:
            already_in_queue = True
        elif matchmaking_queue:
            opponent_id = matchmaking_queue.pop(0)
        else:
            matchmaking_queue.append(user.id)

    if already_in_queue:
        await interaction.response.send_message(
            "Ya estás en la cola de matchmaking. Te avisaremos cuando encontremos rival.",
            ephemeral=True
        )
        return

    if opponent_id is None:
        await interaction.response.send_message(
            "✅ Te uniste a la cola de matchmaking. Te avisaremos cuando encontremos rival.",
            ephemeral=True
        )
        return

    opponent = guild.get_member(opponent_id)
    if opponent is None or opponent.id == user.id:
        # El rival ya no está disponible: el usuario pasa a la cola.
        async with queue_lock:
            matchmaking_queue.append(user.id)
        await interaction.response.send_message(
            "✅ Te uniste a la cola de matchmaking. Te avisaremos cuando encontremos rival.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    channel = await create_duel_channel(guild, opponent, user)

    if channel is None:
        async with queue_lock:
            matchmaking_queue.append(opponent_id)
        await interaction.followup.send(
            "No se pudo crear el canal de duelo. Contacta a un administrador.",
            ephemeral=True
        )
        return

    await interaction.followup.send(f"⚔️ ¡Rival encontrado! Tu duelo: {channel.mention}", ephemeral=True)
    try:
        await opponent.send(f"⚔️ ¡Rival encontrado! Tu duelo: {channel.mention}")
    except Exception:
        pass  # el usuario puede tener los DMs cerrados


async def ensure_matchmaking_panel():
    """Publica el embed permanente de matchmaking si todavía no está en el canal."""
    channel = client.get_channel(QUEUE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(QUEUE_CHANNEL_ID)
        except Exception as e:
            logger.error(f"!!! [MATCHMAKING PANEL] Canal {QUEUE_CHANNEL_ID} no encontrado: {e}")
            return

    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == client.user.id and msg.components:
                logger.info(f">>> [MATCHMAKING PANEL] Ya publicado en #{channel.name}")
                return

        embed = discord.Embed(
            title="⚔️ Matchmaking",
            description=(
                "Pulsa el botón para entrar a la cola.\n"
                "Cuando haya 2 jugadores en cola, se creará automáticamente "
                "un canal privado para el duelo."
            ),
            color=0xE63946
        )
        embed.set_footer(text="BLZ-T · Matchmaking")
        await channel.send(embed=embed, view=MatchmakingView())
        logger.info(f">>> [MATCHMAKING PANEL] Publicado en #{channel.name}")
    except discord.Forbidden:
        logger.error(f"!!! [MATCHMAKING PANEL] Sin permisos en #{channel.name}")
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL] Error: {e}")


@client.event
async def on_ready():
    logger.info(f">>> [DISCORD]: Conectado como {client.user}")
    bot_ready_event.set()

    # Registrar las vistas persistentes (los botones sobreviven a reinicios)
    try:
        client.add_view(MatchmakingView())
        client.add_view(CloseDuelView())
        logger.info(">>> [MATCHMAKING] Vistas persistentes registradas")
    except Exception as e:
        logger.error(f"!!! [VIEW REGISTER]: {e}")

    # Sincroniza los slash commands (vacío): esto elimina de Discord cualquier
    # comando antiguo (/rename, /thping, /deadline, etc.) que hubiera quedado registrado.
    try:
        synced = await client.tree.sync()
        logger.info(f">>> [SLASH] Sincronizados {len(synced)} comandos")
    except Exception as e:
        logger.error(f"!!! [SLASH SYNC ERROR]: {e}")

    # Publicar el panel de matchmaking si no está ya presente
    try:
        await ensure_matchmaking_panel()
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL ON READY]: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    client.run(TOKEN)
