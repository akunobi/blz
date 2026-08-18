# bot.py - BLZ-T Bot: Matchmaking System
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

# --- LOGGING SETUP ---
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

# --- MATCHMAKING CONFIG ---
GUILD_ID = 1538589344368164905          # Server
QUEUE_CHANNEL_ID = 1539158063116984361  # Channel where the permanent matchmaking embed lives
DUEL_CATEGORY_ID = 1539157638925918238  # Category where private duel channels are created

# --- MINIMAL FLASK (just to keep the Render service alive) ---
app = Flask(__name__)


@app.route("/")
def health():
    return jsonify({"status": "ok", "bot": "BLZ-T Matchmaking"}), 200


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to resolve the opponent by ID when matching

client = commands.Bot(command_prefix="!", intents=intents)
bot_ready_event = threading.Event()

# --- MATCHMAKING QUEUE STATE (in-memory) ---
matchmaking_queue: list[int] = []
queue_lock = asyncio.Lock()

DUEL_TOPIC_RE = re.compile(r'^duel-participants:(\d+):(\d+)$')


def _safe_channel_part(name: str) -> str:
    s = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')
    return s or 'player'


class CloseDuelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Duel",
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
                "This isn't a valid duel channel.", ephemeral=True
            )
            return

        p1, p2 = int(match.group(1)), int(match.group(2))
        if user.id not in (p1, p2):
            await interaction.response.send_message(
                "❌ You don't have permission to close this duel.", ephemeral=True
            )
            return

        button.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        try:
            await channel.send(
                f"🔒 Duel closed by {user.mention}. This channel will be deleted in 5 seconds..."
            )
        except Exception:
            pass

        logger.info(f">>> [DUEL] Closed by {user.name} ({user.id}) in #{channel.name}")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Duel closed by {user.name}")
        except Exception as e:
            logger.error(f"!!! [DUEL DELETE ERROR]: {e}")


class MatchmakingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Find Duel",
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
            logger.error(f"!!! [DUEL] Category {DUEL_CATEGORY_ID} not found: {e}")
            return None

    base_name = f"duel-{_safe_channel_part(user1.name)}-{_safe_channel_part(user2.name)}"[:90]
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
            reason=f"Matchmaking duel between {user1.name} and {user2.name}"
        )
    except discord.Forbidden:
        logger.error("!!! [DUEL] Missing permissions to create the duel channel")
        return None
    except Exception as e:
        logger.error(f"!!! [DUEL CREATE ERROR]: {e}")
        return None

    embed = discord.Embed(
        title="⚔️ Duel",
        description=(
            f"{user1.mention} vs {user2.mention}\n\n"
            f"Good luck to both of you! When you're done, press **Close Duel**."
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

    logger.info(f">>> [DUEL] Channel created: #{channel.name} ({user1.name} vs {user2.name})")
    return channel


async def handle_queue_join(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("This only works inside the server.", ephemeral=True)
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
            "You're already in the matchmaking queue. We'll notify you when we find an opponent.",
            ephemeral=True
        )
        return

    if opponent_id is None:
        await interaction.response.send_message(
            "✅ You joined the matchmaking queue. We'll notify you when we find an opponent.",
            ephemeral=True
        )
        return

    opponent = guild.get_member(opponent_id)
    if opponent is None or opponent.id == user.id:
        # The opponent is no longer available: the user goes into the queue instead.
        async with queue_lock:
            matchmaking_queue.append(user.id)
        await interaction.response.send_message(
            "✅ You joined the matchmaking queue. We'll notify you when we find an opponent.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    channel = await create_duel_channel(guild, opponent, user)

    if channel is None:
        async with queue_lock:
            matchmaking_queue.append(opponent_id)
        await interaction.followup.send(
            "Couldn't create the duel channel. Contact an administrator.",
            ephemeral=True
        )
        return

    await interaction.followup.send(f"⚔️ Opponent found! Your duel: {channel.mention}", ephemeral=True)
    try:
        await opponent.send(f"⚔️ Opponent found! Your duel: {channel.mention}")
    except Exception:
        pass  # the user may have DMs disabled


async def ensure_matchmaking_panel():
    """Publish the permanent matchmaking embed if it's not already in the channel."""
    channel = client.get_channel(QUEUE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(QUEUE_CHANNEL_ID)
        except Exception as e:
            logger.error(f"!!! [MATCHMAKING PANEL] Channel {QUEUE_CHANNEL_ID} not found: {e}")
            return

    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == client.user.id and msg.components:
                logger.info(f">>> [MATCHMAKING PANEL] Already published in #{channel.name}")
                return

        embed = discord.Embed(
            title="⚔️ Matchmaking",
            description=(
                "Press the button to join the queue.\n"
                "Once 2 players are in the queue, a private channel for the "
                "duel will be created automatically."
            ),
            color=0xE63946
        )
        embed.set_footer(text="BLZ-T · Matchmaking")
        await channel.send(embed=embed, view=MatchmakingView())
        logger.info(f">>> [MATCHMAKING PANEL] Published in #{channel.name}")
    except discord.Forbidden:
        logger.error(f"!!! [MATCHMAKING PANEL] Missing permissions in #{channel.name}")
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL] Error: {e}")


@client.event
async def on_ready():
    logger.info(f">>> [DISCORD]: Logged in as {client.user}")
    bot_ready_event.set()

    # Register persistent views (buttons survive restarts)
    try:
        client.add_view(MatchmakingView())
        client.add_view(CloseDuelView())
        logger.info(">>> [MATCHMAKING] Persistent views registered")
    except Exception as e:
        logger.error(f"!!! [VIEW REGISTER]: {e}")

    # Sync slash commands (empty): this removes any old command
    # (/rename, /thping, /deadline, etc.) still registered with Discord.
    try:
        synced = await client.tree.sync()
        logger.info(f">>> [SLASH] Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"!!! [SLASH SYNC ERROR]: {e}")

    # Publish the matchmaking panel if it isn't already there
    try:
        await ensure_matchmaking_panel()
    except Exception as e:
        logger.error(f"!!! [MATCHMAKING PANEL ON READY]: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    client.run(TOKEN)
