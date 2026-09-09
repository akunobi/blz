# mod_bot.py — BARC Moderation Bot: /bban, /bkick, /bmute, /bunmute, /bunban, /bwarn,
# /cases, /case, /modlogs, /bstats, /modstats
# + context menus: Quick Mute (on a member), Warn Message (on a message)
# + every one of the commands above ALSO works as a text command with the "-" prefix
#   (e.g. "-bban @user spamming" works exactly like "/bban"), plus the original
#   quick text commands -a (avatar) and -s (stats leaderboard, alias for -bstats).
#   Text versions skip the ban's optional delete_days — use /bban for that.
#
# Every logged action (ban/kick/mute/unmute/unban/warn) gets a permanent, sequential
# "case number" now (see _next_case_number / actions_col below). /cases lists cases
# (optionally filtered to one member), /case looks up a single case in full detail,
# and /modlogs shows one member's entire moderation history in full detail — all
# three are paginated with buttons when there's more than one page of results.
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
from pymongo import ASCENDING, DESCENDING, ReturnDocument
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


def build_ban_dm(reason: str, case_number: int = None) -> str:
    case_line = f"`Case:` #{case_number}\n" if case_number else ""
    return (
        "🟥 **RED CARD!** 🟥\n\n"
        "You've been locked off the field of Blazing Lock. A true egoist knows the rules of the game.\n\n"
        f"`Reason:` {reason}\n"
        f"{case_line}\n"
        "For further assistance, head to the support locker room.\n"
        f"`Support Server:` {SUPPORT_SERVER_URL}"
    )


def build_warn_dm(punishment: str, reason: str, case_number: int = None) -> str:
    case_line = f"`Case:` #{case_number}\n" if case_number else ""
    return (
        "🟨 **YELLOW CARD!** 🟨\n\n"
        "You've been cautioned on the field of Blazing Lock. A true egoist knows the rules of the game.\n\n"
        f"`Punishment:` {punishment}\n"
        f"`Reason:` {reason}\n"
        f"{case_line}\n"
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


# --- MOD ACTIONS LOG (one doc per ban/kick/mute/unmute/unban/warn, powers /cases,
# /case, /modlogs, the admin dashboard's Moderation Panel, and /bstats) ---
actions_col = db["mod_actions"]  # {case_number, action, moderator_id, target_id, reason, detail, created_at}
actions_col.create_index([("moderator_id", ASCENDING)])
actions_col.create_index([("target_id", ASCENDING), ("created_at", DESCENDING)])
actions_col.create_index([("case_number", ASCENDING)], unique=True, sparse=True)  # sparse: actions
                          # logged before cases existed have no case_number and are skipped by it

# --- CASE NUMBERS — one permanent, ever-increasing counter shared by every mod action.
# Stored in its own tiny collection (a single doc) so the increment is atomic even if
# two moderators act at the exact same moment; find_one_and_update with $inc can't
# double-hand out the same number the way "read max(case_number), add 1" could.
counters_col = db["mod_counters"]


def _next_case_number_sync() -> int:
    doc = counters_col.find_one_and_update(
        {"_id": "case_number"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def _log_action_sync(action: str, moderator_id: int, target_id: int, reason: str = "", detail: str = "") -> int:
    """Logs one mod action and returns its new case number."""
    case_number = _next_case_number_sync()
    actions_col.insert_one({
        "case_number": case_number,
        "action": action,
        "moderator_id": moderator_id,
        "target_id": target_id,
        "reason": reason,
        "detail": detail,  # extra context: mute duration, ban delete_days, warned message preview, etc.
        "created_at": datetime.now(timezone.utc),
    })
    return case_number


async def _log_action(action: str, moderator_id: int, target_id: int, reason: str = "", detail: str = "") -> int:
    return await asyncio.to_thread(_log_action_sync, action, moderator_id, target_id, reason, detail)


def _get_case_sync(case_number: int):
    return actions_col.find_one({"case_number": case_number})


async def _get_case(case_number: int):
    return await asyncio.to_thread(_get_case_sync, case_number)


def _get_cases_sync(target_id: int = None, limit: int = 200):
    query = {"case_number": {"$exists": True}}
    if target_id is not None:
        query["target_id"] = target_id
    return list(actions_col.find(query).sort("created_at", DESCENDING).limit(limit))


async def _get_cases(target_id: int = None, limit: int = 200):
    return await asyncio.to_thread(_get_cases_sync, target_id, limit)


def _get_all_actions_sync(target_id: int, limit: int = 200):
    """Every logged action for a member (used by /modlogs), including any logged
    before case numbers existed — unlike /cases and /case, this isn't limited to
    numbered cases."""
    return list(actions_col.find({"target_id": target_id}).sort("created_at", DESCENDING).limit(limit))


async def _get_all_actions(target_id: int, limit: int = 200):
    return await asyncio.to_thread(_get_all_actions_sync, target_id, limit)


def _get_leaderboard_sync(limit: int = 10):
    pipeline = [
        {"$group": {"_id": "$moderator_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    return list(actions_col.aggregate(pipeline))


async def _get_leaderboard(limit: int = 10):
    return await asyncio.to_thread(_get_leaderboard_sync, limit)


def _get_modstats_sync(moderator_id: int):
    pipeline = [
        {"$match": {"moderator_id": moderator_id}},
        {"$group": {"_id": "$action", "count": {"$sum": 1}}},
    ]
    counts = {row["_id"]: row["count"] for row in actions_col.aggregate(pipeline)}
    return counts, sum(counts.values())


async def _get_modstats(moderator_id: int):
    return await asyncio.to_thread(_get_modstats_sync, moderator_id)


# Single source of truth for how each action type is displayed — emoji, label, embed
# color — reused everywhere: modlog posts, confirmation embeds, /cases, /case, /modlogs.
ACTION_META = {
    "ban":    {"emoji": "🟥", "label": "Ban",    "color": discord.Color.red()},
    "kick":   {"emoji": "🟧", "label": "Kick",   "color": discord.Color.orange()},
    "mute":   {"emoji": "🟨", "label": "Mute",   "color": discord.Color.gold()},
    "unmute": {"emoji": "🟩", "label": "Unmute", "color": discord.Color.green()},
    "unban":  {"emoji": "🟩", "label": "Unban",  "color": discord.Color.green()},
    "warn":   {"emoji": "🟨", "label": "Warn",   "color": discord.Color.gold()},
}


def _action_meta(action: str) -> dict:
    return ACTION_META.get(action, {"emoji": "⬜", "label": action.title(), "color": discord.Color.greyple()})


def _build_modstats_embed(member: discord.abc.User, counts: dict, total: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"📊 Mod Stats — {member.display_name}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if total == 0:
        embed.description = "No moderation actions logged yet."
        return embed
    for key, meta in ACTION_META.items():
        if counts.get(key):
            embed.add_field(name=f"{meta['emoji']} {meta['label']}s", value=f"**{counts[key]}**", inline=True)
    embed.set_footer(text=f"Total actions: {total}")
    return embed


def _build_leaderboard_embed(rows) -> discord.Embed:
    embed = discord.Embed(title="📊 Moderation Leaderboard", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
    if not rows:
        embed.description = "No moderation actions logged yet."
        return embed
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        prefix = medals[i] if i < 3 else f"`{i + 1}.`"
        lines.append(f"{prefix} <@{row['_id']}> — **{row['count']}** action(s)")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Top {len(rows)} moderator(s)")
    return embed


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # needed to ban/kick/timeout by member and DM them
intents.message_content = True  # needed to read "-a" / "-s" quick text commands

client = commands.Bot(command_prefix="-", intents=intents)  # "-" prefix powers the quick text commands


NO_PERM = "❌ You don't have permission to use this command."


def _has_mod_role(user: discord.abc.User) -> bool:
    roles = getattr(user, "roles", [])
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
    case_number: int = None,
    target: discord.abc.User = None,
):
    """Posts an embed to the modlog channel. Called after every ban/kick/mute/unmute/unban/warn."""
    channel = client.get_channel(MODLOG_CHANNEL_ID)
    if channel is None:
        logger.error(f"!!! [MODLOG] Channel {MODLOG_CHANNEL_ID} not found/cached.")
        return
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    if target is not None:
        embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Member", value=f"<@{target_id}> (`{target_id}`)", inline=True)
    embed.add_field(name="Moderator", value=moderator.mention, inline=True)
    if case_number:
        embed.add_field(name="Case", value=f"#{case_number}", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if extra:
        embed.add_field(name="Details", value=extra, inline=False)
    embed.set_footer(text=f"Case #{case_number}" if case_number else "BARC Moderation")
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"!!! [MODLOG SEND ERROR]: {e}")


def _target_check_error(guild_me: discord.Member, moderator: discord.abc.User, target: discord.Member) -> str | None:
    """Same target safety checks as CircleUtilityBot's target_check: returns an error
    message if `target` isn't a valid moderation target, or None if it's fine to proceed.
    guild_me/moderator are passed in (rather than an interaction) so this works from both
    slash commands and the "-" text commands."""
    if target.id == client.user.id:
        return "❌ You can't target the bot itself."
    if target.id == moderator.id:
        return "❌ You can't target yourself."
    if target.guild_permissions.administrator or any(r.id in MOD_ROLE_IDS for r in target.roles):
        return "❌ You can't target another moderator/admin."
    if guild_me.top_role.position <= target.top_role.position:
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

        case_number = await _log_action("warn", interaction.user.id, member.id, reason, detail=f"Message: \"{preview}\"")
        dm_sent = await _try_dm(member, build_warn_dm("Verbal warning", reason, case_number))
        await _log_warning(member.id, interaction.user.id, "Verbal warning", reason)
        await _send_modlog("Warn (Warn Message)", discord.Color.gold(), interaction.user, member.id, reason,
                            f"Message: \"{preview}\"", case_number=case_number, target=member)

        embed = _build_result_embed("warn", member, interaction.user, reason, case_number, dm_sent,
                                     extra_fields=[("Warned Message", preview)])
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
        case_number = await _log_action("mute", interaction.user.id, member.id, reason, detail=punishment)
        dm_sent = await _try_dm(member, build_warn_dm(punishment, reason, case_number))
        await _log_warning(member.id, interaction.user.id, punishment, reason)
        await _send_modlog("Mute (Quick Mute)", discord.Color.gold(), interaction.user, member.id, reason,
                            punishment, case_number=case_number, target=member)

        expires = discord.utils.format_dt(datetime.now(timezone.utc) + timedelta(minutes=minutes), style="R")
        embed = _build_result_embed("mute", member, interaction.user, reason, case_number, dm_sent,
                                     extra_fields=[("Duration", punishment), ("Expires", expires)])
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_result_embed(
    action: str,
    member: discord.abc.User,
    moderator: discord.abc.User,
    reason: str,
    case_number: int,
    dm_sent: bool = None,
    extra_fields: list = None,
) -> discord.Embed:
    """The moderator-facing confirmation embed sent after ban/kick/mute/unmute/unban/warn
    — shown to the acting moderator (ephemeral for slash commands, a normal reply for
    text commands). Every field a moderator would want at a glance: who, who-by, why,
    which case number to reference later, and whether the DM notice actually landed."""
    meta = _action_meta(action)
    embed = discord.Embed(
        title=f"{meta['emoji']} {meta['label']} — Case #{case_number}",
        color=meta["color"],
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="Moderator", value=f"{moderator.mention}", inline=True)
    embed.add_field(name="Case", value=f"#{case_number}", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    for name, value in (extra_fields or []):
        embed.add_field(name=name, value=value, inline=False)
    if dm_sent is not None:
        embed.add_field(
            name="DM Notice",
            value="✅ Delivered" if dm_sent else "⚠️ Couldn't deliver — DMs may be disabled",
            inline=False,
        )
    embed.set_footer(text="BARC Moderation")
    return embed


async def _reply_result(interaction: discord.Interaction, result):
    """result is a discord.Embed on success or a plain error string on failure."""
    if isinstance(result, discord.Embed):
        await interaction.followup.send(embed=result, ephemeral=True)
    else:
        await interaction.followup.send(result, ephemeral=True)


async def _reply_result_text(ctx: commands.Context, result):
    if isinstance(result, discord.Embed):
        await ctx.reply(embed=result, mention_author=False)
    else:
        await ctx.reply(result, mention_author=False)


# =====================================================================================
# Core moderation actions — shared by the /slash commands AND the "-" text commands
# below, so the logic (checks, DMs, logging, modlog post) only lives in one place.
# Each returns (ok: bool, result), where result is a discord.Embed to show the
# moderator on success, or a plain error string on failure.
# =====================================================================================

async def _core_ban(guild: discord.Guild, moderator: discord.abc.User, member: discord.Member, reason: str, delete_days: int = 0):
    err = _target_check_error(guild.me, moderator, member)
    if err:
        return False, err

    # Grab the case number BEFORE banning/DMing, so it's already known in time to
    # include it in the ban DM itself for the member's own reference.
    try:
        case_number = await _log_action("ban", moderator.id, member.id, reason,
                                         detail=f"Deleted messages from the last {delete_days} day(s)." if delete_days else "")
    except Exception as e:
        logger.error(f"!!! [BBAN LOG ERROR]: {e}")
        return False, "⚠️ Couldn't log that case (DB issue) — no ban was performed."

    # DM before banning — once they're banned there's a good chance the bot can no
    # longer reach their DMs (no shared server left), so this order matters.
    dm_sent = await _try_dm(member, build_ban_dm(reason, case_number))

    try:
        await member.ban(reason=f"{reason} (by {moderator}) [Case #{case_number}]", delete_message_seconds=delete_days * 86400)
    except discord.Forbidden:
        return False, "❌ I don't have permission to ban that member."
    except Exception as e:
        logger.error(f"!!! [BBAN ERROR]: {e}")
        return False, "⚠️ Something went wrong banning that member."

    extra_fields = [("Messages Deleted", f"Last {delete_days} day(s)")] if delete_days else None
    await _send_modlog("Ban", discord.Color.red(), moderator, member.id, reason,
                        extra_fields[0][1] if extra_fields else "", case_number=case_number, target=member)

    embed = _build_result_embed("ban", member, moderator, reason, case_number, dm_sent, extra_fields)
    return True, embed


async def _core_kick(guild: discord.Guild, moderator: discord.abc.User, member: discord.Member, reason: str):
    err = _target_check_error(guild.me, moderator, member)
    if err:
        return False, err
    try:
        await member.kick(reason=f"{reason} (by {moderator})")
    except discord.Forbidden:
        return False, "❌ I don't have permission to kick that member."
    except Exception as e:
        logger.error(f"!!! [BKICK ERROR]: {e}")
        return False, "⚠️ Something went wrong kicking that member."

    case_number = await _log_action("kick", moderator.id, member.id, reason)
    await _send_modlog("Kick", discord.Color.orange(), moderator, member.id, reason, case_number=case_number, target=member)

    embed = _build_result_embed("kick", member, moderator, reason, case_number)
    return True, embed


async def _core_mute(guild: discord.Guild, moderator: discord.abc.User, member: discord.Member, minutes: int, reason: str):
    err = _target_check_error(guild.me, moderator, member)
    if err:
        return False, err
    try:
        await member.timeout(timedelta(minutes=minutes), reason=f"{reason} (by {moderator})")
    except discord.Forbidden:
        return False, "❌ I don't have permission to mute that member."
    except Exception as e:
        logger.error(f"!!! [BMUTE ERROR]: {e}")
        return False, "⚠️ Something went wrong muting that member."

    punishment = f"{minutes} minute mute"
    case_number = await _log_action("mute", moderator.id, member.id, reason, detail=punishment)
    dm_sent = await _try_dm(member, build_warn_dm(punishment, reason, case_number))
    await _log_warning(member.id, moderator.id, punishment, reason)
    await _send_modlog("Mute", discord.Color.gold(), moderator, member.id, reason, punishment, case_number=case_number, target=member)

    expires = discord.utils.format_dt(datetime.now(timezone.utc) + timedelta(minutes=minutes), style="R")
    extra_fields = [("Duration", punishment), ("Expires", expires)]
    embed = _build_result_embed("mute", member, moderator, reason, case_number, dm_sent, extra_fields)
    return True, embed


async def _core_unmute(moderator: discord.abc.User, member: discord.Member):
    if member.current_timeout is None:
        return False, f"{member.mention} is not currently muted."
    try:
        await member.timeout(None, reason=f"Unmuted (by {moderator})")
    except discord.Forbidden:
        return False, "❌ I don't have permission to unmute that member."
    except Exception as e:
        logger.error(f"!!! [BUNMUTE ERROR]: {e}")
        return False, "⚠️ Something went wrong unmuting that member."

    case_number = await _log_action("unmute", moderator.id, member.id)
    await _send_modlog("Unmute", discord.Color.green(), moderator, member.id, case_number=case_number, target=member)

    embed = _build_result_embed("unmute", member, moderator, "", case_number)
    return True, embed


async def _core_unban(guild: discord.Guild, moderator: discord.abc.User, user_id_str: str, reason: str):
    try:
        uid = int(user_id_str)
    except ValueError:
        return False, "❌ That doesn't look like a valid user ID."

    try:
        await guild.fetch_ban(discord.Object(id=uid))
    except discord.NotFound:
        return False, "That user isn't banned from this server."
    except Exception as e:
        logger.error(f"!!! [BUNBAN LOOKUP ERROR]: {e}")
        return False, "⚠️ Something went wrong checking the ban list."

    try:
        await guild.unban(discord.Object(id=uid), reason=f"{reason} (by {moderator})")
    except discord.Forbidden:
        return False, "❌ I don't have permission to unban that user."
    except Exception as e:
        logger.error(f"!!! [BUNBAN ERROR]: {e}")
        return False, "⚠️ Something went wrong unbanning that user."

    case_number = await _log_action("unban", moderator.id, uid, reason)

    # Only a raw ID is guaranteed here (the user may not share a server with the
    # bot anymore) — try to resolve a real discord.User for a nicer embed, but
    # fall back to a bare mention if that lookup fails.
    try:
        target_user = await client.fetch_user(uid)
    except Exception:
        target_user = None

    await _send_modlog("Unban", discord.Color.green(), moderator, uid, reason, case_number=case_number, target=target_user)

    if target_user is not None:
        embed = _build_result_embed("unban", target_user, moderator, reason, case_number)
    else:
        meta = _action_meta("unban")
        embed = discord.Embed(title=f"{meta['emoji']} {meta['label']} — Case #{case_number}", color=meta["color"],
                               timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Member", value=f"<@{uid}>\n`{uid}`", inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Case", value=f"#{case_number}", inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="BARC Moderation")
    return True, embed


def _build_warn_embed(member: discord.abc.User, history):
    """Returns a discord.Embed if there's history, or a plain string if there isn't."""
    if not history:
        return f"{member.mention} has no warnings on record."
    lines = []
    for i, w in enumerate(history, start=1):
        ts = w["created_at"].strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"**{i}.** `{ts}` — {w['punishment']} — {w['reason']} (by <@{w['moderator_id']}>)")
    embed = discord.Embed(
        title=f"🟨 Warnings — {member.display_name}",
        description="\n".join(lines)[:4000],
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{len(history)} warning(s) shown — use /modlogs for full moderation history")
    return embed


# =====================================================================================
# Pagination — a small Prev/Next view shared by /cases and /modlogs whenever there's
# more than one page of results. Only the person who ran the command can page through
# it (it's an ephemeral reply anyway, so nobody else can even see it); the buttons
# disable themselves once the view times out so an old page doesn't look clickable.
# =====================================================================================

class CasePaginator(discord.ui.View):
    def __init__(self, pages: list, invoker_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.invoker_id = invoker_id
        self.index = 0
        self.interaction: discord.Interaction = None  # set by the caller for slash-command replies
        self.message: discord.Message = None          # set by the caller for text-command replies
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.pages) - 1
        self.page_label.label = f"Page {self.index + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "❌ Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # display-only, always disabled — not actually clickable

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


async def _send_paginated(interaction: discord.Interaction, pages: list):
    """For slash commands — replies to the deferred ephemeral interaction."""
    if len(pages) == 1:
        await interaction.followup.send(embed=pages[0], ephemeral=True)
        return
    view = CasePaginator(pages, interaction.user.id)
    await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
    view.interaction = interaction


async def _send_paginated_text(ctx: commands.Context, pages: list):
    """For "-" text commands — replies in-channel (not ephemeral; text commands can't be)."""
    if len(pages) == 1:
        await ctx.reply(embed=pages[0], mention_author=False)
        return
    view = CasePaginator(pages, ctx.author.id)
    view.message = await ctx.reply(embed=pages[0], view=view, mention_author=False)


def _build_cases_pages(cases: list, title: str, show_target: bool = True, per_page: int = 8) -> list:
    """Compact case list — /cases. Each line is one case; several cases per page."""
    if not cases:
        return [discord.Embed(title=title, description="No cases logged yet.", color=discord.Color.blurple())]

    chunks = [cases[i:i + per_page] for i in range(0, len(cases), per_page)]
    pages = []
    for page_num, chunk in enumerate(chunks, start=1):
        lines = []
        for c in chunk:
            meta = _action_meta(c["action"])
            ts = discord.utils.format_dt(c["created_at"], style="R")
            reason = c.get("reason") or "No reason provided."
            if len(reason) > 80:
                reason = reason[:80] + "…"
            target_part = f"<@{c['target_id']}> — " if show_target else ""
            lines.append(f"**#{c['case_number']}** {meta['emoji']} **{meta['label']}** — {target_part}{reason}\n"
                         f"By <@{c['moderator_id']}> • {ts}")
        embed = discord.Embed(title=title, description="\n\n".join(lines), color=discord.Color.blurple())
        embed.set_footer(text=f"Page {page_num}/{len(chunks)} • {len(cases)} case(s) shown (max 200)")
        pages.append(embed)
    return pages


def _build_case_detail_embed(case: dict) -> discord.Embed:
    """Full detail for one case — /case."""
    meta = _action_meta(case["action"])
    embed = discord.Embed(
        title=f"{meta['emoji']} Case #{case['case_number']} — {meta['label']}",
        color=meta["color"],
        timestamp=case["created_at"],
    )
    embed.add_field(name="Member", value=f"<@{case['target_id']}>\n`{case['target_id']}`", inline=True)
    embed.add_field(name="Moderator", value=f"<@{case['moderator_id']}>\n`{case['moderator_id']}`", inline=True)
    embed.add_field(name="Action", value=f"{meta['emoji']} {meta['label']}", inline=True)
    embed.add_field(name="Reason", value=case.get("reason") or "No reason provided.", inline=False)
    if case.get("detail"):
        embed.add_field(name="Details", value=case["detail"], inline=False)
    embed.set_footer(text="BARC Moderation")
    return embed


def _build_modlogs_pages(member: discord.abc.User, actions: list, counts: dict, total: int, per_page: int = 4) -> list:
    """Full-detail moderation history for one member — /modlogs. Every entry gets its
    own field with the full reason, moderator, timestamp and any extra detail (mute
    duration, ban delete_days, warned message preview, etc.), a few entries per page."""
    summary = " • ".join(
        f"{ACTION_META[k]['emoji']} {v} {ACTION_META[k]['label']}{'s' if v != 1 else ''}"
        for k, v in counts.items() if v and k in ACTION_META
    ) or "No actions logged."

    if not actions:
        embed = discord.Embed(
            title=f"📁 Modlogs — {member.display_name}",
            description=f"No moderation actions on record.\n\n**Total:** {total}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="BARC Moderation")
        return [embed]

    chunks = [actions[i:i + per_page] for i in range(0, len(actions), per_page)]
    pages = []
    for page_num, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=f"📁 Modlogs — {member.display_name}",
            description=f"**Summary:** {summary}\n**Total:** {total}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for a in chunk:
            meta = _action_meta(a["action"])
            case_label = f"Case #{a['case_number']}" if a.get("case_number") else "Unnumbered case (logged before /cases existed)"
            ts = discord.utils.format_dt(a["created_at"], style="f")
            value_lines = [
                f"**When:** {ts}",
                f"**Moderator:** <@{a['moderator_id']}>",
                f"**Reason:** {a.get('reason') or 'No reason provided.'}",
            ]
            if a.get("detail"):
                value_lines.append(f"**Details:** {a['detail']}")
            embed.add_field(
                name=f"{meta['emoji']} {meta['label']} — {case_label}",
                value="\n".join(value_lines)[:1024],
                inline=False,
            )
        embed.set_footer(text=f"Page {page_num}/{len(chunks)} • {len(actions)} action(s) shown (max 200)")
        pages.append(embed)
    return pages


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
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok, result = await _core_ban(interaction.guild, interaction.user, member, reason, delete_days)
    await _reply_result(interaction, result)


# =====================================================================================
# /bkick
# =====================================================================================

@client.tree.command(name="bkick", description="Kick a member")
@app_commands.describe(member="The member to kick", reason="The reason for the kick")
async def bkick_command(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok, result = await _core_kick(interaction.guild, interaction.user, member, reason)
    await _reply_result(interaction, result)


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
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok, result = await _core_mute(interaction.guild, interaction.user, member, minutes, reason)
    await _reply_result(interaction, result)


# =====================================================================================
# /bunmute
# =====================================================================================

@client.tree.command(name="bunmute", description="Remove an active timeout from a member")
@app_commands.describe(member="The member to unmute")
async def bunmute_command(interaction: discord.Interaction, member: discord.Member):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok, result = await _core_unmute(interaction.user, member)
    await _reply_result(interaction, result)


# =====================================================================================
# /bunban
# =====================================================================================

@client.tree.command(name="bunban", description="Unban a user by their ID")
@app_commands.describe(user_id="The ID of the user to unban", reason="The reason for the unban")
async def bunban_command(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided."):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok, result = await _core_unban(interaction.guild, interaction.user, user_id, reason)
    await _reply_result(interaction, result)


# =====================================================================================
# Quick Mute (context menu) — right-click a member → Apps → Quick Mute
# =====================================================================================

@client.tree.context_menu(name="Quick Mute")
async def quick_mute_command(interaction: discord.Interaction, member: discord.Member):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return

    err = _target_check_error(interaction.guild.me, interaction.user, member)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    await interaction.response.send_modal(QuickMuteModal(member))


# =====================================================================================
# Warn Message (context menu) — right-click a message → Apps → Warn Message
# =====================================================================================

@client.tree.context_menu(name="Warn Message")
async def warn_message_command(interaction: discord.Interaction, message: discord.Message):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return

    err = _target_check_error(interaction.guild.me, interaction.user, message.author)
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
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    history = await _get_warnings(member.id)
    result = _build_warn_embed(member, history)
    if isinstance(result, discord.Embed):
        await interaction.followup.send(embed=result, ephemeral=True)
    else:
        await interaction.followup.send(result, ephemeral=True)


# =====================================================================================
# /cases — list moderation cases. With a member: only that member's cases. Without
# one: the most recent cases server-wide. Paginated (8/page) when there's more than
# one page's worth.
# =====================================================================================

@client.tree.command(name="cases", description="List moderation cases — a member's, or the most recent server-wide")
@app_commands.describe(member="Only show this member's cases (optional — leave empty for the full recent case list)")
async def cases_command(interaction: discord.Interaction, member: discord.Member = None):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    cases = await _get_cases(target_id=member.id if member else None, limit=200)
    title = f"📁 Cases — {member.display_name}" if member else "📁 Recent Cases — All Members"
    pages = _build_cases_pages(cases, title, show_target=member is None)
    await _send_paginated(interaction, pages)


# =====================================================================================
# /case — full detail for a single case number (companion to /cases).
# =====================================================================================

@client.tree.command(name="case", description="View full detail for a single case number")
@app_commands.describe(case_number="The case number to look up")
async def case_command(interaction: discord.Interaction, case_number: app_commands.Range[int, 1]):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    case = await _get_case(case_number)
    if case is None:
        await interaction.followup.send(f"❌ No case #{case_number} found.", ephemeral=True)
        return
    await interaction.followup.send(embed=_build_case_detail_embed(case), ephemeral=True)


# =====================================================================================
# /modlogs — a member's ENTIRE moderation history in full detail (every ban, kick,
# mute, unmute, unban and warn — reason, moderator, timestamp, and any extra detail
# like mute duration or deleted-message range). Paginated (4 entries/page).
# =====================================================================================

@client.tree.command(name="modlogs", description="View a member's full moderation history in detail")
@app_commands.describe(member="The member to check")
async def modlogs_command(interaction: discord.Interaction, member: discord.Member):
    if not _has_mod_role(interaction.user):
        await interaction.response.send_message(NO_PERM, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    actions = await _get_all_actions(member.id, limit=200)
    counts, total = await _get_modstats(member.id)
    pages = _build_modlogs_pages(member, actions, counts, total)
    await _send_paginated(interaction, pages)


# =====================================================================================
# /bstats — moderation leaderboard (also available as the quick "-s" text command)
# =====================================================================================

@client.tree.command(name="bstats", description="View the moderation leaderboard")
async def bstats_command(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await _get_leaderboard()
    await interaction.followup.send(embed=_build_leaderboard_embed(rows))


# =====================================================================================
# /modstats — one moderator's action breakdown (ban/kick/mute/etc. counts + total)
# =====================================================================================

@client.tree.command(name="modstats", description="View a moderator's action breakdown")
@app_commands.describe(member="The moderator to check (defaults to yourself)")
async def modstats_command(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    target = member or interaction.user
    counts, total = await _get_modstats(target.id)
    await interaction.followup.send(embed=_build_modstats_embed(target, counts, total))


# =====================================================================================
# Text commands (prefix "-") — every slash command above also works this way, e.g.
# "-bban @user spamming" does the same thing as "/bban". Plus the original quick
# commands -a (avatar) and -s (stats leaderboard, same as -bstats).
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
    """-s — shows the moderation leaderboard (same as -bstats)."""
    rows = await _get_leaderboard()
    await ctx.reply(embed=_build_leaderboard_embed(rows), mention_author=False)


@client.command(name="bstats")
async def bstats_text(ctx: commands.Context):
    """-bstats — shows the moderation leaderboard (same as -s)."""
    rows = await _get_leaderboard()
    await ctx.reply(embed=_build_leaderboard_embed(rows), mention_author=False)


@client.command(name="modstats")
async def modstats_text(ctx: commands.Context, member: discord.Member = None):
    """-modstats [@moderator] — view a moderator's action breakdown (defaults to yourself)."""
    target = member or ctx.author
    counts, total = await _get_modstats(target.id)
    await ctx.reply(embed=_build_modstats_embed(target, counts, total), mention_author=False)


@client.command(name="bban")
async def bban_text(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    """-bban @member [reason] — bans a member. (Delete-days option isn't available here — use /bban.)"""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    ok, result = await _core_ban(ctx.guild, ctx.author, member, reason)
    await _reply_result_text(ctx, result)


@client.command(name="bkick")
async def bkick_text(ctx: commands.Context, member: discord.Member, *, reason: str):
    """-bkick @member reason — kicks a member."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    ok, result = await _core_kick(ctx.guild, ctx.author, member, reason)
    await _reply_result_text(ctx, result)


@client.command(name="bmute")
async def bmute_text(ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str):
    """-bmute @member minutes reason — times out a member."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    minutes = max(1, min(minutes, 40320))
    ok, result = await _core_mute(ctx.guild, ctx.author, member, minutes, reason)
    await _reply_result_text(ctx, result)


@client.command(name="bunmute")
async def bunmute_text(ctx: commands.Context, member: discord.Member):
    """-bunmute @member — removes an active timeout."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    ok, result = await _core_unmute(ctx.author, member)
    await _reply_result_text(ctx, result)


@client.command(name="bunban")
async def bunban_text(ctx: commands.Context, user_id: str, *, reason: str = "No reason provided."):
    """-bunban user_id [reason] — unbans a user by their ID."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    ok, result = await _core_unban(ctx.guild, ctx.author, user_id, reason)
    await _reply_result_text(ctx, result)


@client.command(name="bwarn")
async def bwarn_text(ctx: commands.Context, member: discord.Member):
    """-bwarn @member — view a member's warning history."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    history = await _get_warnings(member.id)
    result = _build_warn_embed(member, history)
    if isinstance(result, discord.Embed):
        await ctx.reply(embed=result, mention_author=False)
    else:
        await ctx.reply(result, mention_author=False)


@client.command(name="cases")
async def cases_text(ctx: commands.Context, member: discord.Member = None):
    """-cases [@member] — list moderation cases (a member's, or the most recent server-wide)."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    cases = await _get_cases(target_id=member.id if member else None, limit=200)
    title = f"📁 Cases — {member.display_name}" if member else "📁 Recent Cases — All Members"
    pages = _build_cases_pages(cases, title, show_target=member is None)
    await _send_paginated_text(ctx, pages)


@client.command(name="case")
async def case_text(ctx: commands.Context, case_number: int):
    """-case <number> — view full detail for a single case number."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    case = await _get_case(case_number)
    if case is None:
        await ctx.reply(f"❌ No case #{case_number} found.", mention_author=False)
        return
    await ctx.reply(embed=_build_case_detail_embed(case), mention_author=False)


@client.command(name="modlogs")
async def modlogs_text(ctx: commands.Context, member: discord.Member):
    """-modlogs @member — view a member's full moderation history in detail."""
    if not _has_mod_role(ctx.author):
        await ctx.reply(NO_PERM, mention_author=False)
        return
    actions = await _get_all_actions(member.id, limit=200)
    counts, total = await _get_modstats(member.id)
    pages = _build_modlogs_pages(member, actions, counts, total)
    await _send_paginated_text(ctx, pages)


@client.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Keeps a bad '-something' text command from throwing a raw traceback — every
    message in the server passes through here since the bot reads message content."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing argument: `{error.param.name}`.", mention_author=False)
        return
    if isinstance(error, (commands.MemberNotFound, commands.BadArgument)):
        await ctx.reply("❌ Couldn't find that member or ID — check it and try again.", mention_author=False)
        return
    logger.error(f"!!! [MOD BOT TEXT COMMAND ERROR] -{ctx.command}: {error!r}")
    await ctx.reply("⚠️ Something went wrong running that command.", mention_author=False)


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
