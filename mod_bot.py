# mod_bot.py — BARC Moderation Bot: /bban, /bkick, /bmute, /bwarn
#
# A SEPARATE Discord bot (its own application/token) that runs alongside bot.py.
# It reuses bot.py's Mongo connection and the exact ban/warn DM text (build_ban_dm /
# build_warn_dm) so the notices look identical to the ones /bandm and /warndm send
# manually — the difference is this bot actually performs the ban/kick/mute and
# fires the DM on its own.
import os
import time
import asyncio
import logging
from datetime import timedelta, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING
from dotenv import load_dotenv

import bot as main_bot  # importing this reuses bot.py's already-open Mongo connection
                         # instead of opening a second one, and gives us the same GUILD_ID
                         # as the main bot. All moderation logic (roles, DM text, commands)
                         # lives here now — bot.py no longer has any of it.

load_dotenv()

logger = logging.getLogger("mod-bot")

TOKEN = os.getenv("MOD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("MOD_BOT_TOKEN environment variable is not set.")

GUILD_ID = main_bot.GUILD_ID
db = main_bot.db

MOD_ROLE_IDS = {
    1538589345991360527,
    1539303279195062313,
}  # Members with either role can use /bban, /bkick, /bmute, /bwarn

SUPPORT_SERVER_URL = "https://discord.gg/FZmjTSBpSZ"  # Used in ban/warn DMs


def build_ban_dm(reason: str) -> str:
    return (
        "🟥 **RED CARD!** 🟥\n\n"
        "You've been locked off the field of Blazing Lock. A true egoist knows the rules of the game.\n\n"
        f"`Reason:` {reason}\n\n"
        "For further assistance, head to the support locker room.\n"
        f"`Support Server:` {SUPPORT_SERVER_URL}"
    )


def build_warn_dm(punishment: str, reason: str) -> str:
    return (
        "🟨 **YELLOW CARD!** 🟨\n\n"
        "You've been cautioned on the field of Blazing Lock. A true egoist knows the rules of the game.\n\n"
        f"`Punishment:` {punishment}\n"
        f"`Reason:` {reason}\n\n"
        "For further assistance, head to the support locker room.\n"
        f"`Support Server:` {SUPPORT_SERVER_URL}"
    )

# --- WARNINGS STORAGE (one doc per /bmute, shown back by /bwarn) ---
warnings_col = db["warnings"]  # {user_id, moderator_id, punishment, reason, created_at}
warnings_col.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])


def _log_warning_sync(user_id: int, moderator_id: int, punishment: str, reason: str):
    warnings_col.insert_one({
        "user_id": user_id,
        "moderator_id": moderator_id,
        "punishment": punishment,
        "reason": reason,
        "created_at": datetime.now(timezone.utc),
    })


async def _log_warning(user_id: int, moderator_id: int, punishment: str, reason: str):
    await asyncio.to_thread(_log_warning_sync, user_id, moderator_id, punishment, reason)


def _get_warnings_sync(user_id: int):
    return list(warnings_col.find({"user_id": user_id}).sort("created_at", DESCENDING).limit(25))


async def _get_warnings(user_id: int):
    return await asyncio.to_thread(_get_warnings_sync, user_id)


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to ban/kick/timeout by member and DM them

client = commands.Bot(command_prefix="!mod!", intents=intents)  # prefix unused, slash-only bot


def _is_mod(interaction: discord.Interaction) -> bool:
    roles = getattr(interaction.user, "roles", [])
    return any(r.id in MOD_ROLE_IDS for r in roles)


async def _try_dm(member: discord.Member, content: str) -> bool:
    try:
        await member.send(content)
        return True
    except Exception:
        return False


# =====================================================================================
# /bban
# =====================================================================================

@client.tree.command(name="bban", description="Ban a member (sends the ban DM automatically)")
@app_commands.describe(member="The member to ban", reason="The reason for the ban")
async def bban_command(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    # DM before banning — once they're banned there's a good chance the bot can no
    # longer reach their DMs (no shared server left), so this order matters.
    dm_sent = await _try_dm(member, build_ban_dm(reason))

    try:
        await member.ban(reason=f"{reason} (by {interaction.user})")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to ban that member.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BBAN ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong banning that member.", ephemeral=True)
        return

    note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
    await interaction.followup.send(f"✅ {member.mention} has been banned.{note}", ephemeral=True)


# =====================================================================================
# /bkick
# =====================================================================================

@client.tree.command(name="bkick", description="Kick a member")
@app_commands.describe(member="The member to kick", reason="The reason for the kick")
async def bkick_command(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    try:
        await member.kick(reason=f"{reason} (by {interaction.user})")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to kick that member.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BKICK ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong kicking that member.", ephemeral=True)
        return

    await interaction.followup.send(f"✅ {member.mention} has been kicked.", ephemeral=True)


# =====================================================================================
# /bmute  (Discord timeout under the hood — no separate "Muted" role needed)
# =====================================================================================

@client.tree.command(name="bmute", description="Timeout a member (sends the warn DM automatically)")
@app_commands.describe(
    member="The member to mute",
    minutes="Mute duration in minutes (max 40320 = 28 days, Discord's timeout limit)",
    reason="The reason for the mute",
)
async def bmute_command(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str,
):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    try:
        await member.timeout(timedelta(minutes=minutes), reason=f"{reason} (by {interaction.user})")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to mute that member.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BMUTE ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong muting that member.", ephemeral=True)
        return

    punishment = f"{minutes} minute mute"
    dm_sent = await _try_dm(member, build_warn_dm(punishment, reason))
    await _log_warning(member.id, interaction.user.id, punishment, reason)

    note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
    await interaction.followup.send(f"✅ {member.mention} has been muted for {minutes} minute(s).{note}", ephemeral=True)


# =====================================================================================
# /bwarn
# =====================================================================================

@client.tree.command(name="bwarn", description="View a member's warning history")
@app_commands.describe(member="The member to check")
async def bwarn_command(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    history = await _get_warnings(member.id)
    if not history:
        await interaction.followup.send(f"{member.mention} has no warnings on record.", ephemeral=True)
        return

    lines = []
    for i, w in enumerate(history, start=1):
        ts = w["created_at"].strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"**{i}.** `{ts}` — {w['punishment']} — {w['reason']} (by <@{w['moderator_id']}>)")

    embed = discord.Embed(
        title=f"Warnings — {member.display_name}",
        description="\n".join(lines)[:4000],
        color=discord.Color.yellow(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# =====================================================================================
# BOT LIFECYCLE (mirrors bot.py's pattern)
# =====================================================================================

@client.event
async def on_ready():
    logger.info(f">>> [MOD BOT] Logged in as {client.user}")
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        client.tree.copy_global_to(guild=guild_obj)
        synced = await client.tree.sync(guild=guild_obj)
        logger.info(f">>> [MOD BOT] Synced {len(synced)} command(s) to guild {GUILD_ID}")
    except Exception as e:
        logger.error(f"!!! [MOD BOT SLASH SYNC ERROR]: {e}")


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    cmd_name = interaction.command.name if interaction.command else "unknown"
    logger.error(f"!!! [MOD BOT APP COMMAND ERROR] /{cmd_name} failed: {error!r}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong running that command.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong running that command.", ephemeral=True)
    except discord.errors.HTTPException:
        pass


def _run_with_backoff():
    """Same reasoning as bot.py's _run_with_backoff(): run once, and if Discord's login
    endpoint 429s us, wait out the cooldown then exit non-zero so Render restarts with a
    fresh process rather than retrying client.run() on the same (possibly broken) client."""
    try:
        client.run(TOKEN)
    except discord.errors.LoginFailure:
        logger.error("!!! [MOD BOT LOGIN] Invalid token — check MOD_BOT_TOKEN and redeploy.")
        raise
    except discord.errors.HTTPException as e:
        if e.status == 429:
            requested = None
            try:
                requested = int(float(e.response.headers.get("Retry-After", 0)))
            except Exception:
                requested = None
            backoff = max(requested or 60, 60)
            backoff = min(backoff, 3600)
            logger.error(
                f"!!! [MOD BOT LOGIN] Rate limited (429). Discord asked for a {requested}s "
                f"cooldown — waiting {backoff}s, then exiting so Render restarts with a clean process..."
            )
            time.sleep(backoff)
            raise SystemExit(1)
        raise
