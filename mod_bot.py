# mod_bot.py — BARC Moderation Bot: /bban, /bkick, /bmute, /bunmute, /bunban, /bwarn, /bstats
# + context menus: Quick Mute (on a member), Warn Message (on a message)
# + quick text commands (prefix "-"): -a (avatar), -s (stats leaderboard)
#
# A SEPARATE Discord bot (its own application/token) that runs alongside bot.py.
# It reuses bot.py's Mongo connection and the exact ban/warn DM text (build_ban_dm /
# build_warn_dm) so the notices look identical to the ones /bandm and /warndm send
# manually — the difference is this bot actually performs the ban/kick/mute and
# fires the DM on its own.
import os
import time
import typing
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
}  # Members with either role can use all /b... commands and both context menus

SUPPORT_SERVER_URL = "https://discord.gg/FZmjTSBpSZ"  # Used in ban/warn DMs
MODLOG_CHANNEL_ID = 1546607743174049922  # Every mod action gets posted here


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


# --- MOD ACTIONS LOG (one doc per ban/kick/mute/unmute/unban/warn, powers modlogs + /bstats) ---
actions_col = db["mod_actions"]  # {action, moderator_id, target_id, reason, created_at}
actions_col.create_index([("moderator_id", ASCENDING)])


def _log_action_sync(action: str, moderator_id: int, target_id: int, reason: str = ""):
    actions_col.insert_one({
        "action": action,
        "moderator_id": moderator_id,
        "target_id": target_id,
        "reason": reason,
        "created_at": datetime.now(timezone.utc),
    })


async def _log_action(action: str, moderator_id: int, target_id: int, reason: str = ""):
    await asyncio.to_thread(_log_action_sync, action, moderator_id, target_id, reason)


def _get_leaderboard_sync(limit: int = 10):
    pipeline = [
        {"$group": {"_id": "$moderator_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    return list(actions_col.aggregate(pipeline))


async def _get_leaderboard(limit: int = 10):
    return await asyncio.to_thread(_get_leaderboard_sync, limit)


def _build_leaderboard_embed(rows) -> discord.Embed:
    if not rows:
        return discord.Embed(
            title="📊 Moderation Leaderboard",
            description="No moderation actions logged yet.",
            color=discord.Color.blurple(),
        )
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        prefix = medals[i] if i < 3 else f"`{i + 1}.`"
        lines.append(f"{prefix} <@{row['_id']}> — **{row['count']}** action(s)")
    return discord.Embed(
        title="📊 Moderation Leaderboard",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to ban/kick/timeout by member and DM them
intents.message_content = True  # needed to read "-a" / "-s" quick text commands

client = commands.Bot(command_prefix="-", intents=intents)  # "-" prefix powers the quick text commands


def _is_mod(interaction: discord.Interaction) -> bool:
    roles = getattr(interaction.user, "roles", [])
    return any(r.id in MOD_ROLE_IDS for r in roles)


async def _try_dm(member: discord.Member, content: str) -> bool:
    try:
        await member.send(content)
        return True
    except Exception:
        return False


async def _send_modlog(
    title: str,
    color: discord.Color,
    moderator: discord.abc.User,
    target_id: int,
    reason: str = "",
    extra: str = "",
):
    """Posts an embed to the modlog channel. Called after every ban/kick/mute/unmute/unban/warn."""
    channel = client.get_channel(MODLOG_CHANNEL_ID)
    if channel is None:
        logger.error(f"!!! [MODLOG] Channel {MODLOG_CHANNEL_ID} not found/cached.")
        return
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Member", value=f"<@{target_id}> (`{target_id}`)", inline=False)
    embed.add_field(name="Moderator", value=moderator.mention, inline=False)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if extra:
        embed.add_field(name="Details", value=extra, inline=False)
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"!!! [MODLOG SEND ERROR]: {e}")


def _target_check_error(interaction: discord.Interaction, target: discord.Member) -> str | None:
    """Same target safety checks as CircleUtilityBot's target_check: returns an error
    message if `target` isn't a valid moderation target, or None if it's fine to proceed."""
    if target.id == client.user.id:
        return "❌ You can't target the bot itself."
    if target.id == interaction.user.id:
        return "❌ You can't target yourself."
    if target.guild_permissions.administrator or any(r.id in MOD_ROLE_IDS for r in target.roles):
        return "❌ You can't target another moderator/admin."
    if interaction.guild.me.top_role.position <= target.top_role.position:
        return "❌ I need a higher role than that member to do this."
    return None


class WarnReasonModal(discord.ui.Modal, title="Warn this message"):
    """Popup asking for a reason — used by the 'Warn Message' context menu command."""
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why is this message being warned?",
        max_length=300,
    )

    def __init__(self, message: discord.Message):
        super().__init__()
        self.target_message = message

    async def on_submit(self, interaction: discord.Interaction):
        member = self.target_message.author
        reason = str(self.reason)

        preview = self.target_message.content or "*(no text — attachment/embed only)*"
        if len(preview) > 300:
            preview = preview[:300] + "…"

        dm_sent = await _try_dm(member, build_warn_dm("Verbal warning", reason))
        await _log_warning(member.id, interaction.user.id, "Verbal warning", reason)
        await _log_action("warn", interaction.user.id, member.id, reason)
        await _send_modlog("🟨 Warn", discord.Color.yellow(), interaction.user, member.id, reason, f"Message: \"{preview}\"")

        note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
        await interaction.response.send_message(
            f"✅ Warned {member.mention} for: \"{preview}\"{note}",
            ephemeral=True,
        )


class QuickMuteModal(discord.ui.Modal, title="Quick mute"):
    """Popup asking for duration + reason — used by the 'Quick Mute' context menu command."""
    duration = discord.ui.TextInput(
        label="Duration (minutes)",
        placeholder="Default is 60",
        required=False,
        max_length=6,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Default is 'No reason provided.'",
        required=False,
        max_length=300,
    )

    def __init__(self, member: discord.Member):
        super().__init__()
        self.target_member = member

    async def on_submit(self, interaction: discord.Interaction):
        raw_duration = str(self.duration).strip()
        reason = str(self.reason).strip() or "No reason provided."

        minutes = 60
        if raw_duration:
            try:
                minutes = int(raw_duration)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Duration must be a whole number of minutes.", ephemeral=True
                )
                return
        minutes = max(1, min(minutes, 40320))

        member = self.target_member
        await interaction.response.defer(ephemeral=True)

        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"{reason} (by {interaction.user})")
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to mute that member.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"!!! [QUICK MUTE ERROR]: {e}")
            await interaction.followup.send("⚠️ Something went wrong muting that member.", ephemeral=True)
            return

        punishment = f"{minutes} minute mute"
        dm_sent = await _try_dm(member, build_warn_dm(punishment, reason))
        await _log_warning(member.id, interaction.user.id, punishment, reason)
        await _log_action("mute", interaction.user.id, member.id, reason)
        await _send_modlog("🟨 Mute (Quick Mute)", discord.Color.yellow(), interaction.user, member.id, reason, punishment)

        note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
        await interaction.followup.send(
            f"✅ {member.mention} has been muted for {minutes} minute(s).{note}", ephemeral=True
        )


# =====================================================================================
# /bban
# =====================================================================================

@client.tree.command(name="bban", description="Ban a member (sends the ban DM automatically)")
@app_commands.describe(
    member="The member to ban",
    reason="The reason for the ban",
    delete_days="Also delete this member's recent messages (default: don't delete)",
)
async def bban_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    delete_days: typing.Literal[0, 1, 3, 7] = 0,
):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    err = _target_check_error(interaction, member)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    # DM before banning — once they're banned there's a good chance the bot can no
    # longer reach their DMs (no shared server left), so this order matters.
    dm_sent = await _try_dm(member, build_ban_dm(reason))

    try:
        await member.ban(reason=f"{reason} (by {interaction.user})", delete_message_seconds=delete_days * 86400)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to ban that member.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BBAN ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong banning that member.", ephemeral=True)
        return

    await _log_action("ban", interaction.user.id, member.id, reason)
    extra = f"Deleted messages from the last {delete_days} day(s)." if delete_days else ""
    await _send_modlog("🟥 Ban", discord.Color.red(), interaction.user, member.id, reason, extra)

    note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
    await interaction.followup.send(f"✅ {member.mention} has been banned.{note}", ephemeral=True)
    if delete_days:
        await interaction.followup.send(f"🗑️ Also deleted their messages from the last {delete_days} day(s).", ephemeral=True)


# =====================================================================================
# /bkick
# =====================================================================================

@client.tree.command(name="bkick", description="Kick a member")
@app_commands.describe(member="The member to kick", reason="The reason for the kick")
async def bkick_command(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    err = _target_check_error(interaction, member)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
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

    await _log_action("kick", interaction.user.id, member.id, reason)
    await _send_modlog("🟧 Kick", discord.Color.orange(), interaction.user, member.id, reason)

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
    err = _target_check_error(interaction, member)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
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
    await _log_action("mute", interaction.user.id, member.id, reason)
    await _send_modlog("🟨 Mute", discord.Color.yellow(), interaction.user, member.id, reason, punishment)

    note = "" if dm_sent else " (couldn't DM them — DMs may be disabled)"
    await interaction.followup.send(f"✅ {member.mention} has been muted for {minutes} minute(s).{note}", ephemeral=True)


# =====================================================================================
# /bunmute
# =====================================================================================

@client.tree.command(name="bunmute", description="Remove an active timeout from a member")
@app_commands.describe(member="The member to unmute")
async def bunmute_command(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    if member.current_timeout is None:
        await interaction.followup.send(f"{member.mention} is not currently muted.", ephemeral=True)
        return

    try:
        await member.timeout(None, reason=f"Unmuted (by {interaction.user})")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to unmute that member.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BUNMUTE ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong unmuting that member.", ephemeral=True)
        return

    await _log_action("unmute", interaction.user.id, member.id)
    await _send_modlog("🟩 Unmute", discord.Color.green(), interaction.user, member.id)

    await interaction.followup.send(f"✅ {member.mention} has been unmuted.", ephemeral=True)


# =====================================================================================
# /bunban
# =====================================================================================

@client.tree.command(name="bunban", description="Unban a user by their ID")
@app_commands.describe(user_id="The ID of the user to unban", reason="The reason for the unban")
async def bunban_command(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided."):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    try:
        uid = int(user_id)
    except ValueError:
        await interaction.followup.send("❌ That doesn't look like a valid user ID.", ephemeral=True)
        return

    try:
        await interaction.guild.fetch_ban(discord.Object(id=uid))
    except discord.NotFound:
        await interaction.followup.send("That user isn't banned from this server.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BUNBAN LOOKUP ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong checking the ban list.", ephemeral=True)
        return

    try:
        await interaction.guild.unban(discord.Object(id=uid), reason=f"{reason} (by {interaction.user})")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to unban that user.", ephemeral=True)
        return
    except Exception as e:
        logger.error(f"!!! [BUNBAN ERROR]: {e}")
        await interaction.followup.send("⚠️ Something went wrong unbanning that user.", ephemeral=True)
        return

    await _log_action("unban", interaction.user.id, uid, reason)
    await _send_modlog("🟩 Unban", discord.Color.green(), interaction.user, uid, reason)

    await interaction.followup.send(f"✅ <@{uid}> has been unbanned.", ephemeral=True)


# =====================================================================================
# Quick Mute (context menu) — right-click a member → Apps → Quick Mute
# =====================================================================================

@client.tree.context_menu(name="Quick Mute")
async def quick_mute_command(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    err = _target_check_error(interaction, member)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    await interaction.response.send_modal(QuickMuteModal(member))


# =====================================================================================
# Warn Message (context menu) — right-click a message → Apps → Warn Message
# =====================================================================================

@client.tree.context_menu(name="Warn Message")
async def warn_message_command(interaction: discord.Interaction, message: discord.Message):
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    err = _target_check_error(interaction, message.author)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    await interaction.response.send_modal(WarnReasonModal(message))


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
# /bstats — moderation leaderboard (also available as the quick "-s" text command)
# =====================================================================================

@client.tree.command(name="bstats", description="View the moderation leaderboard")
async def bstats_command(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await _get_leaderboard()
    await interaction.followup.send(embed=_build_leaderboard_embed(rows))


# =====================================================================================
# Quick text commands (prefix "-") — plain messages, not slash commands
# =====================================================================================

@client.command(name="a")
async def quick_avatar(ctx: commands.Context, member: discord.Member = None):
    """-a [@member] — shows a member's avatar (defaults to yourself)."""
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}'s avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await ctx.reply(embed=embed, mention_author=False)


@client.command(name="s")
async def quick_stats(ctx: commands.Context):
    """-s — shows the moderation leaderboard."""
    rows = await _get_leaderboard()
    await ctx.reply(embed=_build_leaderboard_embed(rows), mention_author=False)


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
