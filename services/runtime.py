import csv
import asyncio
import io
import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from services.duel import (
    last_duel_for_user,
    load_duels,
    phase_windows,
    record_duel,
    week_dates,
)
from services.fun_facts import choose_fact


load_dotenv()


def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"Expected an integer ID, got {value!r}") from error


TOKEN = os.getenv("DISCORD_TOKEN")
REPORT_PATH = Path(os.getenv("REPORT_FILE", "vc_report.txt"))
MEME_DIR = Path(__file__).with_name("meme")
TRACKED_GUILD_ID = _optional_int(os.getenv("TRACKED_GUILD_ID"))
TRACKED_VOICE_CHANNEL_ID = _optional_int(os.getenv("TRACKED_VOICE_CHANNEL_ID"))
MAIN_ID = _optional_int(os.getenv("MAIN_ID"))
IST = timezone(timedelta(hours=5, minutes=30))
ACHIEVEMENT_CHANNEL_ID = 1401597433057771613
GENERAL_CHANNEL_ID = (
    _optional_int(os.getenv("GENERAL_CHANNEL_ID")) or ACHIEVEMENT_CHANNEL_ID
)
SESSION_MERGE_GAP = timedelta(minutes=10)
MIN_SESSION_DURATION = timedelta(minutes=5)
ACHIEVEMENT_RANKS = (
    (0, 5, "Chodu"),
    (5, 10, "Tip Of Dihh"),
    (10, 15, "C ka Bacha Hai Tu"),
    (15, 20, '"C"hootad Licker'),
    (20, 25, "Pointer To Pussy"),
    (25, 30, "Munnu Ke Niche Ho Abhi Bhi"),
    (30, 35, "Rukja Bhai"),
    (35, None, "Bas Kar BhosadiKe Kitna Padhega"),
)
MILESTONE_BADGES = ("🌱", "🔥", "⚡", "🏆", "💎", "👑", "🚀", "🌟", "☄️", "🏅")
MILESTONE_HOURS = tuple(range(50, 501, 50))

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
active_sessions: dict[tuple[int, int], dict[str, Any]] = {}
active_duel_users: set[int] = set()
active_roulette_effects: dict[tuple[int, int], dict[str, Any]] = {}
boss_monitor_tasks: dict[int, asyncio.Task[None]] = {}
commands_synced = False
weekly_reset_task: asyncio.Task[None] | None = None
duel_store = load_duels()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def message_embed(
    description: str,
    *,
    title: str = "Voice Tracker",
    color: discord.Color = discord.Color.blurple(),
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=utc_now(),
    )
    embed.set_footer(text="Requested By the command user")
    return embed


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def member_label(
    guild: discord.Guild, member_id: int, fallback: str | None = None
) -> str:
    member = guild.get_member(member_id)
    name = member.display_name if member else fallback or f"User {member_id}"
    guild_report = report.get("guilds", {}).get(str(guild.id), {})
    record = guild_report.get("users", {}).get(str(member_id), {})
    crown_expires = record.get("boss_crown_expires_at")
    has_crown = False
    if crown_expires:
        has_crown = parse_timestamp(crown_expires) > utc_now()
    elif record.get("boss_crowns", 0):
        has_crown = True
    if not has_crown and crown_expires:
        record.pop("boss_crown_expires_at", None)
        record["boss_crowns"] = 0
        save_report()
    return f"👑 {name}" if has_crown else name


ROULETTE_OUTCOMES = tuple(
    {
        "name": f"{theme} {number}",
        "role": role,
        "stat": stat,
        "description": description,
    }
    for theme, role, stat, description in (
        (
            "VC Wizard",
            "Temporary VC Wizard",
            "+25% VC aura",
            "Your microphone now has suspiciously powerful energy.",
        ),
        (
            "Midnight",
            "Midnight Creature",
            "+2 night luck",
            "The later it gets, the stronger your legend becomes.",
        ),
        (
            "Yap",
            "Certified Yapologist",
            "+30% yap power",
            "Every sentence now arrives with a director's commentary.",
        ),
        (
            "Lurker",
            "Elite Lurker",
            "+20% stealth",
            "You are present, mysterious, and probably muted.",
        ),
        (
            "Grind",
            "Clocked-In Grinder",
            "+1 grind point",
            "The imaginary productivity department approves.",
        ),
        (
            "Chaos",
            "Agent of Chaos",
            "+40% chaos",
            "Your next VC entrance requires dramatic sound effects.",
        ),
        (
            "Chill",
            "Zen VC Monk",
            "+35% calm",
            "Nothing can disturb your perfectly unnecessary serenity.",
        ),
        (
            "Duel",
            "Arena Champion",
            "+15% duel luck",
            "The scoreboard is legally required to fear you.",
        ),
        (
            "Meme",
            "Meme Archivist",
            "+3 meme points",
            "Your next joke has been pre-approved by the council.",
        ),
        (
            "Speed",
            "Speedy Joiner",
            "+10% join speed",
            "You appear in VC before the notification finishes.",
        ),
        (
            "Social",
            "Social Battery Hoarder",
            "+2 social battery",
            "You have somehow discovered a spare battery.",
        ),
        (
            "Boss",
            "Temporary Final Boss",
            "+50% intimidation",
            "The lobby music changes when you connect.",
        ),
        (
            "NPC",
            "Main-Character NPC",
            "+12% plot armor",
            "A side quest has spawned around your presence.",
        ),
        (
            "Scholar",
            "VC Scholar",
            "+4 knowledge",
            "You understand exactly 4% more of the conversation.",
        ),
        (
            "Goblin",
            "Certified VC Goblin",
            "+33% goblin energy",
            "The call has gained one unpredictable creature.",
        ),
    )
    for number in range(1, 11)
)

POWERUPS = {
    "poison_splash": {
        "name": "Poison Splash Potion",
        "description": "Poisons the boss for 10 minutes; it loses 0.03% HP every 10 seconds and may weaken current players.",
    },
    "strength_splash": {
        "name": "Strength Splash Potion",
        "description": "Boosts the user by 10% for 5 minutes, with a chance to strengthen the boss.",
    },
    "luck_splash": {
        "name": "Luck Splash Potion",
        "description": "Improves the user's minute damage, with a chance to regenerate the boss for 10 minutes.",
    },
    "invisibility_splash": {
        "name": "Invisibility Splash Potion",
        "description": "Gives current VC players 5% more damage for 10 minutes; the boss may miss hits.",
    },
    "awkward_splash": {
        "name": "Awkward Splash Potion",
        "description": "Does nothing for 5 minutes, but boasts about either the user or the boss.",
    },
}


def quest_pool(guild: discord.Guild) -> tuple[dict[str, Any], ...]:
    channels = [
        channel.name for channel in guild.voice_channels if is_tracked_channel(channel)
    ] or ["the tracked VC"]
    templates = (
        ("Spend 25 minutes in {channel}.", "voice_minutes", 25),
        ("Spend 45 minutes in {channel}.", "voice_minutes", 45),
        ("Spend 45 minutes in {channel}.", "voice_minutes", 45),
        ("Join {channel} before sunset.", "join_channel", 0),
        ("Return to {channel} twice today.", "join_channel_twice", 2),
        ("Spend 60 total minutes in voice today.", "voice_minutes_any", 60),
        ("Join a tracked VC with another player.", "join_with_player", 0),
        ("Complete one full session of at least 30 minutes.", "long_session", 30),
        (
            "Visit {channel} and stay until the conversation gets chaotic.",
            "voice_minutes",
            20,
        ),
        ("Enter {channel} during a new hour.", "join_channel", 0),
    )
    quests = []
    for index in range(50):
        text, kind, target = templates[index % len(templates)]
        channel = channels[index % len(channels)]
        quests.append(
            {
                "id": index + 1,
                "text": text.format(channel=f"**{channel}**"),
                "kind": kind,
                "target": target,
                "channel": channel,
            }
        )
    return tuple(quests)


def daily_wordle_answer(now: datetime) -> str:
    answers = (
        "audio",
        "voice",
        "track",
        "party",
        "speak",
        "habit",
        "hours",
        "guild",
        "quest",
        "crown",
        "grind",
        "squad",
        "night",
        "level",
    )
    return answers[now.astimezone(IST).date().toordinal() % len(answers)]


def quest_complete(user_quest: dict[str, Any], member: discord.Member) -> bool:
    if user_quest.get("kind") == "wordle":
        return False
    guild_report = report["guilds"].get(str(member.guild.id), {})
    started = parse_timestamp(user_quest["assigned_at"])
    now = utc_now()
    seconds = live_period_seconds(guild_report, member.id, started, now)
    if user_quest["kind"] in {"voice_minutes", "voice_minutes_any"}:
        return seconds >= int(user_quest["target"]) * 60
    if user_quest["kind"] == "long_session":
        return seconds >= int(user_quest["target"]) * 60
    if user_quest["kind"] == "join_channel":
        return any(
            session.get("channel_name") == user_quest["channel"]
            and parse_timestamp(session["joined_at"]) >= started
            for report_part in iter_user_reports(guild_report, member.id)
            for session in report_part.get("sessions", [])
        )
    return seconds >= int(user_quest.get("target", 0)) * 60


async def announce_quest_ready(member: discord.Member) -> None:
    guild_report = report.get("guilds", {}).get(str(member.guild.id))
    user_record = (guild_report or {}).get("users", {}).get(str(member.id))
    user_quest = (user_record or {}).get("quest")
    if not guild_report or not user_record or not user_quest:
        return
    today = utc_now().astimezone(IST).date().isoformat()
    if (
        user_quest.get("day") != today
        or user_quest.get("completed")
        or user_quest.get("claimable")
    ):
        return
    channel = getattr(member.voice, "channel", None)
    is_join_objective = (
        user_quest.get("kind") == "join_channel"
        and channel is not None
        and channel.name == user_quest.get("channel")
    )
    if not is_join_objective and not quest_complete(user_quest, member):
        return
    user_quest["claimable"] = True
    save_report()
    channel_id = user_quest.get("channel_id")
    destination = member.guild.get_channel(int(channel_id)) if channel_id else None
    if destination is None:
        destination = member.guild.system_channel
    if destination is None or not hasattr(destination, "send"):
        return
    embed = discord.Embed(
        title="🎁 Quest ready to claim",
        description=(
            f"{member.mention} completed today's quest!\n\n"
            "Use `/quest` again to claim your boss-battle powerup."
        ),
        color=discord.Color.green(),
        timestamp=utc_now(),
    )
    embed.add_field(name="Completed objective", value=user_quest["text"], inline=False)
    embed.set_footer(
        text=f"Requested By {user_quest.get('requested_by', member.display_name)}"
    )
    try:
        await destination.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        return


def roulette_effect_for(guild_id: int, member_id: int) -> dict[str, Any] | None:
    key = (guild_id, member_id)
    effect = active_roulette_effects.get(key)
    if effect is None:
        return None
    if effect["expires_at"] <= utc_now():
        active_roulette_effects.pop(key, None)
        return None
    return effect


async def expire_roulette_effect(key: tuple[int, int], expires_at: datetime) -> None:
    delay = max(0, (expires_at - utc_now()).total_seconds())
    await asyncio.sleep(delay)
    effect = active_roulette_effects.get(key)
    if effect and effect["expires_at"] <= utc_now():
        active_roulette_effects.pop(key, None)


def format_local(value: datetime | None) -> str:
    if value is None:
        return "No completed session"
    return value.astimezone(IST).strftime("%I:%M %p")


def format_date(value: datetime) -> str:
    return value.astimezone(IST).strftime("%d %b %Y")


def format_duration(seconds: int) -> str:
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    if hours == 0 and days == 0:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_hours_minutes(seconds: int) -> str:
    total_minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m"


def user_fact(guild_report: dict[str, Any], member_id: int, total_seconds: int) -> str:
    user_record = guild_report.get("users", {}).get(str(member_id), {})
    recent_ids = set(user_record.get("recent_fact_ids", []))
    fact = choose_fact(total_seconds, recent_ids=recent_ids)
    recent_ids.add(fact["id"])
    user_record["recent_fact_ids"] = list(recent_ids)[-20:]
    return fact["text"]


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(IST)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return month_start.astimezone(timezone.utc), next_month.astimezone(timezone.utc)


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return the Sunday 00:00 IST week containing ``now``."""
    local_now = now.astimezone(IST)
    days_since_sunday = (local_now.weekday() + 1) % 7
    week_start = (local_now - timedelta(days=days_since_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start.astimezone(timezone.utc), (
        week_start + timedelta(days=7)
    ).astimezone(timezone.utc)


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(IST)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start.astimezone(timezone.utc), (
        day_start + timedelta(days=1)
    ).astimezone(timezone.utc)


def tracked_guild_only(guild: discord.Guild | None) -> bool:
    return guild is not None and (
        TRACKED_GUILD_ID is None or guild.id == TRACKED_GUILD_ID
    )


def is_allowed_server(interaction: discord.Interaction) -> bool:
    return tracked_guild_only(interaction.guild)


def empty_report() -> dict[str, Any]:
    return {
        "generated_at": isoformat(utc_now()),
        "guilds": {},
        "cprofile_users": [],
        "weekly_state": {"period_start": "", "users": {}},
    }


def iter_user_reports(
    guild_report: dict[str, Any], member_id: int
) -> list[dict[str, Any]]:
    user_record = guild_report.get("users", {}).get(str(member_id))
    if not user_record:
        return []
    if member_id == MAIN_ID:
        return owner_reports(guild_report, member_id, specific_only=False)
    return [user_record]


def user_total_seconds(guild_report: dict[str, Any], member_id: int) -> int:
    return sum(
        int(user_report.get("total_seconds", 0))
        for user_report in iter_user_reports(guild_report, member_id)
    )


def ensure_user_milestones(
    guild_report: dict[str, Any],
    member_id: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or utc_now()
    user_record = guild_report.get("users", {}).get(str(member_id))
    if not user_record:
        return []
    existing = {
        int(item["hours"]): item
        for item in user_record.get("milestones", [])
        if isinstance(item, dict) and str(item.get("hours", "")).isdigit()
    }
    sessions = sorted(
        (
            session
            for user_report in iter_user_reports(guild_report, member_id)
            for session in user_report.get("sessions", [])
        ),
        key=lambda session: parse_timestamp(session["joined_at"]),
    )
    accumulated = 0
    for session in sessions:
        before = accumulated
        accumulated += int(session.get("duration_seconds", 0))
        for index, hours in enumerate(MILESTONE_HOURS):
            if before < hours * 3600 <= accumulated and hours not in existing:
                hit_at = parse_timestamp(session["left_at"])
                existing[hours] = {
                    "hours": hours,
                    "badge": MILESTONE_BADGES[index],
                    "achieved_at": isoformat(hit_at),
                }
    total_seconds = user_total_seconds(guild_report, member_id)
    for index, hours in enumerate(MILESTONE_HOURS):
        if total_seconds >= hours * 3600 and hours not in existing:
            existing[hours] = {
                "hours": hours,
                "badge": MILESTONE_BADGES[index],
                "achieved_at": isoformat(now),
            }
    user_record["milestones"] = [
        existing[hours] for hours in MILESTONE_HOURS if hours in existing
    ]
    return user_record["milestones"]


def period_seconds_for_user(
    guild_report: dict[str, Any],
    member_id: int,
    range_start: datetime,
    range_end: datetime,
) -> int:
    return sum(
        session_seconds_in_range(session, range_start, range_end)
        for user_report in iter_user_reports(guild_report, member_id)
        for session in user_report.get("sessions", [])
    )


def daily_activity_dates(
    guild_report: dict[str, Any],
    member_id: int,
    now: datetime | None = None,
) -> set[date]:
    dates: set[date] = set()
    for user_report in iter_user_reports(guild_report, member_id):
        for session in user_report.get("sessions", []):
            joined_at = parse_timestamp(session["joined_at"]).astimezone(IST)
            left_at = parse_timestamp(session["left_at"]).astimezone(IST)
            current = joined_at.date()
            while current <= left_at.date():
                dates.add(current)
                current += timedelta(days=1)
    if now is not None:
        for (guild_id, active_member_id), session in active_sessions.items():
            if (
                guild_id == int(guild_report.get("guild_id", guild_id))
                and active_member_id == member_id
            ):
                dates.add(session["joined_at"].astimezone(IST).date())
                dates.add(now.astimezone(IST).date())
    return dates


def daily_streak_for_user(
    guild_report: dict[str, Any],
    member_id: int,
    now: datetime | None = None,
) -> int:
    now = now or utc_now()
    dates = daily_activity_dates(guild_report, member_id, now)
    local_today = now.astimezone(IST).date()
    streak = 0
    cursor = local_today
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def achievement_for_seconds(seconds: int) -> tuple[int, str]:
    hours = max(0, seconds) / 3600
    for index, (lower, upper, label) in enumerate(ACHIEVEMENT_RANKS):
        if hours >= lower and (upper is None or hours < upper):
            return index, label
    return 0, ACHIEVEMENT_RANKS[0][2]


def achievement_color(rank_index: int) -> discord.Color:
    colors = (
        (110, 120, 135),
        (70, 160, 95),
        (220, 175, 55),
        (225, 110, 45),
        (205, 55, 55),
        (150, 60, 180),
        (245, 35, 95),
        (255, 215, 70),
    )
    red, green, blue = colors[min(max(rank_index, 0), len(colors) - 1)]
    return discord.Color.from_rgb(red, green, blue)


def weekly_seconds_for_user(
    guild_report: dict[str, Any],
    member_id: int,
    now: datetime,
) -> int:
    week_start, week_end = week_bounds(now)
    total = period_seconds_for_user(
        guild_report, member_id, week_start, min(week_end, now)
    )
    for (guild_id, active_member_id), session in active_sessions.items():
        if (
            guild_id != int(guild_report.get("guild_id", guild_id))
            or active_member_id != member_id
        ):
            continue
        total += max(
            0,
            int(
                (
                    min(now, week_end) - max(session["joined_at"], week_start)
                ).total_seconds()
            ),
        )
    return total


def rank_color_for_member(guild_id: int, member_id: int) -> discord.Color:
    guild_report = report["guilds"].get(str(guild_id))
    if not guild_report:
        return achievement_color(0)
    weekly_seconds = weekly_seconds_for_user(guild_report, member_id, utc_now())
    rank_index, _ = achievement_for_seconds(weekly_seconds)
    return achievement_color(rank_index)


def live_period_seconds(
    guild_report: dict[str, Any],
    member_id: int,
    range_start: datetime,
    range_end: datetime,
) -> int:
    total = period_seconds_for_user(guild_report, member_id, range_start, range_end)
    for (guild_id, active_member_id), session in active_sessions.items():
        if guild_id != guild_report["guild_id"] or active_member_id != member_id:
            continue
        total += max(
            0,
            int(
                (
                    min(utc_now(), range_end) - max(session["joined_at"], range_start)
                ).total_seconds()
            ),
        )
    return total


def live_total_seconds(guild_report: dict[str, Any], member_id: int) -> int:
    total = user_total_seconds(guild_report, member_id)
    guild_id = int(guild_report.get("guild_id", 0))
    for (active_guild_id, active_member_id), session in active_sessions.items():
        if active_guild_id == guild_id and active_member_id == member_id:
            total += max(0, int((utc_now() - session["joined_at"]).total_seconds()))
    return total


def boss_damage_by_player(
    guild_report: dict[str, Any],
    participant_ids: list[int],
    started_at: datetime,
    now: datetime,
) -> dict[int, int]:
    end = min(now, started_at + timedelta(hours=24))
    damage = {}
    for member_id in participant_ids:
        seconds = period_seconds_for_user(guild_report, member_id, started_at, end)
        tiers = seconds // (25 * 60)
        personal_multiplier = min(1.75, 1 + tiers * 0.10)
        damage[member_id] = int(seconds * personal_multiplier * 100)
    active_by_channel: dict[int, list[tuple[int, datetime]]] = {}
    participant_set = set(participant_ids)
    for (guild_id, member_id), session in active_sessions.items():
        if (
            guild_id != int(guild_report["guild_id"])
            or member_id not in participant_set
        ):
            continue
        active_by_channel.setdefault(int(session["channel_id"]), []).append(
            (member_id, max(started_at, session["joined_at"]))
        )
    for channel_sessions in active_by_channel.values():
        multiplier = 1 + min(6, max(0, len(channel_sessions) - 1)) * 0.25
        for member_id, joined_at in channel_sessions:
            seconds = max(0, int((end - joined_at).total_seconds()))
            tiers = seconds // (25 * 60)
            personal_multiplier = min(1.75, 1 + tiers * 0.10)
            damage[member_id] += int(seconds * personal_multiplier * multiplier * 100)
    battle = guild_report.get("boss_battle") or {}
    for effect in battle.get("effects", []):
        effect_end = parse_timestamp(effect["expires_at"])
        effect_start = parse_timestamp(effect["started_at"])
        if not effect_start <= now < effect_end:
            continue
        if effect["type"] == "strength_splash":
            damage[effect["user_id"]] = int(damage.get(effect["user_id"], 0) * 1.10)
        elif effect["type"] == "luck_splash":
            damage[effect["user_id"]] = int(damage.get(effect["user_id"], 0) * 1.15)
        elif effect["type"] == "invisibility_splash":
            for member_id in effect.get("current_members", []):
                damage[member_id] = int(damage.get(member_id, 0) * 1.05)
                if effect.get("boss_harder"):
                    damage[member_id] = int(damage[member_id] * 0.75)
        if effect["type"] == "poison_splash":
            for member_id in effect.get("weakened_members", []):
                damage[member_id] = int(damage.get(member_id, 0) * 0.90)
    return damage


def enroll_boss_player(member: discord.Member) -> None:
    guild_report = report["guilds"].get(str(member.guild.id))
    boss = guild_report.get("boss_battle") if guild_report else None
    if not boss or boss.get("status") != "active":
        return
    participants = boss.setdefault("participants", {})
    if str(member.id) not in participants:
        participants[str(member.id)] = {"name": member.display_name}
        save_report()


def boss_weekly_target_points(
    guild_report: dict[str, Any],
    participant_ids: list[int],
    now: datetime,
) -> int:
    week_start, week_end = week_bounds(now)
    elapsed_days = max(
        1 / 24,
        min(
            7,
            (min(now, week_end) - week_start).total_seconds() / 86400,
        ),
    )
    weekly_total = sum(
        period_seconds_for_user(
            guild_report,
            member_id,
            week_start,
            min(now, week_end),
        )
        for member_id in participant_ids
    )
    daily_average = weekly_total / elapsed_days
    player_count = max(1, len(participant_ids))
    average_per_player = daily_average / player_count
    # Scale health with the party while accounting for the maximum shared-VC bonus.
    party_target = average_per_player * player_count * random.uniform(0.90, 1.10)
    group_fairness = 1 + min(6, player_count - 1) * 0.18
    return max(
        200_000,
        min(2_000_000, int(party_target * group_fairness * 100)),
    )


def boss_hp_points(boss: dict[str, Any]) -> int:
    if "hp_points" in boss:
        return int(boss["hp_points"])
    return int(boss.get("hp_seconds", 200_000))


def boss_name(now: datetime, participant_count: int) -> str:
    names = (
        "The Midnight Megabyte",
        "The Chrono-Yapper",
        "The VC Hydra",
        "The Latency Leviathan",
        "The Weekend Warden",
        "The Session Serpent",
        "The Call Colossus",
        "The AFK Archdemon",
    )
    return f"{random.choice(names)} of {now.astimezone(IST):%H%M}"


def boss_current_health(
    boss: dict[str, Any], now: datetime
) -> tuple[int, dict[int, int]]:
    guild_report = report["guilds"].get(str(boss["guild_id"]), {})
    participant_ids = [int(user_id) for user_id in boss.get("participants", {})]
    calculated_damage = boss_damage_by_player(
        guild_report,
        participant_ids,
        parse_timestamp(boss["started_at"]),
        now,
    )
    last_update = (
        parse_timestamp(boss["last_damage_update"])
        if boss.get("last_damage_update")
        else None
    )
    if last_update is None or (now - last_update).total_seconds() >= 300:
        boss["damage_snapshot"] = calculated_damage
        boss["last_damage_update"] = isoformat(now)
    damage = {
        int(member_id): int(value)
        for member_id, value in boss.get("damage_snapshot", calculated_damage).items()
    }
    poison_seconds = 0
    for effect in boss.get("effects", []):
        if effect["type"] != "poison_splash":
            continue
        start = parse_timestamp(effect["started_at"])
        end = min(now, parse_timestamp(effect["expires_at"]))
        if end > start:
            poison_seconds += int((end - start).total_seconds() // 10)
    hp_points = boss_hp_points(boss)
    poison_damage = int(hp_points * 0.0003 * poison_seconds)
    regeneration = 0
    for effect in boss.get("effects", []):
        if (
            effect["type"] == "luck_splash"
            and effect.get("boss_regenerates")
            and parse_timestamp(effect["started_at"]) <= now
        ):
            end = min(now, parse_timestamp(effect["expires_at"]))
            regeneration += int(
                hp_points
                * 0.01
                * max(
                    0,
                    int(
                        (end - parse_timestamp(effect["started_at"])).total_seconds()
                        // 60
                    ),
                )
            )
    health = max(0, hp_points - sum(damage.values()) - poison_damage + regeneration)
    return health, damage


async def boss_alert_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(GENERAL_CHANNEL_ID)
        except (discord.HTTPException, discord.NotFound):
            return None
    return channel if hasattr(channel, "send") else None


async def send_boss_alert(
    guild: discord.Guild,
    boss: dict[str, Any],
    title: str,
    description: str,
    damage: dict[int, int],
) -> None:
    channel = await boss_alert_channel(guild)
    if channel is None:
        return
    top_damage = sorted(damage.items(), key=lambda item: item[1], reverse=True)[:5]
    stats = (
        "\n".join(
            f"**{index}.** {member_label(guild, member_id, boss['participants'].get(str(member_id), {}).get('name'))}"
            f" — {seconds:,} HP damage"
            for index, (member_id, seconds) in enumerate(top_damage, start=1)
        )
        or "No VC damage recorded yet."
    )
    health, _ = boss_current_health(boss, utc_now())
    elapsed = max(1, (utc_now() - parse_timestamp(boss["started_at"])).total_seconds())
    total_damage = max(0, boss_hp_points(boss) - health)
    rate = total_damage / elapsed if total_damage else 0
    estimate = (
        format_duration(int(health / rate))
        if rate > 0
        else "Unknown — the party needs to enter VC"
    )
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.orange(),
        timestamp=utc_now(),
    )
    embed.add_field(
        name="Boss health",
        value=(
            f"**{health:,} HP** remaining\n"
            f"Damage milestone: **{int(total_damage / max(1, boss_hp_points(boss)) * 100)}%**\n"
            f"ETA at current pace: **{estimate}**"
        ),
        inline=False,
    )
    embed.add_field(name="Top damage dealers", value=stats, inline=False)
    await channel.send(embed=embed)


async def monitor_boss(guild_id: int) -> None:
    while True:
        guild_report = report["guilds"].get(str(guild_id), {})
        boss = guild_report.get("boss_battle")
        if not boss or boss.get("status") != "active":
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        now = utc_now()
        health, damage = boss_current_health(boss, now)
        hp = boss_hp_points(boss)
        threshold = int((1 - health / hp) * 10) if hp else 10
        sent_thresholds = {int(value) for value in boss.get("alerts", [])}
        for reached in range(1, min(10, threshold) + 1):
            if reached not in sent_thresholds:
                boss.setdefault("alerts", []).append(reached)
                save_report()
                await send_boss_alert(
                    guild,
                    boss,
                    f"⚠️ {boss['name']} — {reached * 10}% damage dealt",
                    f"The party has reduced the boss to **{health:,} HP**.",
                    damage,
                )
        if health <= 0:
            boss["status"] = "defeated"
            boss["defeated_at"] = isoformat(now)
            for member_id, participant in boss.get("participants", {}).items():
                user_record = guild_report.setdefault("users", {}).setdefault(
                    str(member_id), {"user_name": participant["name"]}
                )
                user_record["boss_crowns"] = 1
                user_record["boss_crown_expires_at"] = isoformat(
                    now + timedelta(hours=24)
                )
            save_report()
            winners = ", ".join(
                member_label(guild, int(member_id), participant["name"])
                for member_id, participant in boss["participants"].items()
            )
            await send_boss_alert(
                guild,
                boss,
                f"👑 BOSS DEFEATED: {boss['name']}",
                f"The VC party defeated the boss!\nCrowns awarded to: {winners}",
                damage,
            )
            return
        if now >= parse_timestamp(boss["expires_at"]):
            boss["status"] = "defeated" if health <= 0 else "lost"
            boss["finished_at"] = isoformat(now)
            save_report()
            mentions = " ".join(
                f"<@{member_id}>" for member_id in boss.get("summoned_member_ids", [])
            )
            await send_boss_alert(
                guild,
                boss,
                f"💀 HUGE LOSS: {boss['name']} survived",
                f"The 24-hour battle expired with **{health:,} HP** health left.\n"
                f"Summoned players: {mentions or 'Nobody'}",
                damage,
            )
            return
        await asyncio.sleep(60)


def progress_bar(current: int, target: int, width: int = 12) -> str:
    filled = min(width, int(current / target * width)) if target > 0 else width
    return "▰" * filled + "▱" * (width - filled)


def parse_week_input(
    value: str | None, now: datetime
) -> tuple[datetime, datetime, str]:
    if value:
        try:
            selected = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError as error:
            raise ValueError("Dates must use YYYY-MM-DD format.") from error
        start, end = week_bounds(selected)
    else:
        start, end = week_bounds(now)
    label = f"{start.astimezone(IST):%d %b %Y} – {(end - timedelta(seconds=1)).astimezone(IST):%d %b %Y}"
    return start, end, label


def timecapsule_snapshot(
    guild_report: dict[str, Any], range_start: datetime, range_end: datetime
) -> dict[str, dict[int, int]]:
    users: dict[int, int] = {}
    channels: dict[int, int] = {}
    for user_id in guild_report.get("users", {}):
        for user_report in iter_user_reports(guild_report, int(user_id)):
            for session in user_report.get("sessions", []):
                seconds = session_seconds_in_range(session, range_start, range_end)
                if seconds <= 0:
                    continue
                users[int(user_id)] = users.get(int(user_id), 0) + seconds
                channel_id = int(session.get("channel_id", 0))
                channels[channel_id] = channels.get(channel_id, 0) + seconds
    return {"users": users, "channels": channels}


def delta_text(seconds: int) -> str:
    return f"{'+' if seconds >= 0 else '-'}{format_duration(abs(seconds))}"


def ensure_report_schema() -> None:
    report.setdefault("guilds", {})
    report.setdefault("cprofile_users", [])
    report.setdefault("weekly_state", {"period_start": "", "users": {}})
    report["weekly_state"].setdefault("period_start", "")
    report["weekly_state"].setdefault("users", {})
    for guild_id, guild_report in report["guilds"].items():
        guild_report.setdefault("users", {})
        guild_report.setdefault("goals", {})
        guild_report.setdefault("boss_battle", None)
        guild_report["guild_id"] = int(guild_id)
        for user_record in guild_report["users"].values():
            user_record.setdefault("daily_streak", 0)
            user_record.setdefault("last_active_date", "")
            user_record.setdefault("milestones", [])
            user_record.setdefault("recent_fact_ids", [])
            user_record.setdefault("boss_crowns", 0)
            user_record.setdefault("boss_crown_expires_at", "")
            user_record.setdefault("quest", None)
            user_record.setdefault("quests_completed", 0)
            user_record.setdefault("powerups", [])


def session_seconds_in_range(
    session: dict[str, Any],
    range_start: datetime,
    range_end: datetime,
) -> int:
    joined_at = max(parse_timestamp(session["joined_at"]), range_start)
    left_at = min(parse_timestamp(session["left_at"]), range_end)
    return max(0, int((left_at - joined_at).total_seconds()))


def load_report() -> dict[str, Any]:
    if not REPORT_PATH.exists():
        return empty_report()

    with REPORT_PATH.open("r", encoding="utf-8") as report_file:
        report_text = report_file.read().strip()

    if not report_text:
        return empty_report()

    report = json.loads(report_text)

    if not isinstance(report, dict) or not isinstance(report.get("guilds"), dict):
        raise ValueError(
            f"{REPORT_PATH} must contain a JSON object with a 'guilds' object"
        )
    report.setdefault("cprofile_users", [])
    return report


report = load_report()
ensure_report_schema()


def save_report() -> None:
    report["generated_at"] = isoformat(utc_now())
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write("\n")
    temporary_path.replace(REPORT_PATH)


async def reset_weekly_period(now: datetime | None = None) -> None:
    now = now or utc_now()
    week_start, _ = week_bounds(now)
    state = report.setdefault(
        "weekly_state", {"period_start": isoformat(week_start), "users": {}}
    )
    previous_start_text = state.get("period_start", "")
    current_start_text = isoformat(week_start)
    if previous_start_text == current_start_text:
        return

    previous_start = (
        parse_timestamp(previous_start_text) if previous_start_text else None
    )
    state["period_start"] = current_start_text
    state["users"] = {}
    save_report()
    if previous_start is None:
        return

    for guild_id, guild_report in report.get("guilds", {}).items():
        if TRACKED_GUILD_ID is not None and int(guild_id) != TRACKED_GUILD_ID:
            continue
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            continue
        user_ids = set(guild_report.get("users", {}))
        user_ids.update(
            str(member_id)
            for (active_guild_id, member_id) in active_sessions
            if active_guild_id == int(guild_id)
        )
        for user_id in user_ids:
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except (discord.HTTPException, discord.NotFound):
                    continue
            seconds = period_seconds_for_user(
                guild_report, int(user_id), previous_start, week_start
            )
            for (active_guild_id, active_member_id), session in active_sessions.items():
                if active_guild_id == int(guild_id) and active_member_id == int(
                    user_id
                ):
                    seconds += max(
                        0,
                        int(
                            (
                                min(week_start, utc_now())
                                - max(previous_start, session["joined_at"])
                            ).total_seconds()
                        ),
                    )
            try:
                await member.send(
                    f"📊 Your previous weekly voice stats ({format_date(previous_start)} "
                    f"– {format_date(week_start - timedelta(seconds=1))}): "
                    f"**{format_duration(seconds)}**."
                )
            except (discord.Forbidden, discord.HTTPException):
                continue


async def send_achievement_notification(
    guild_id: int,
    member_id: int,
    rank_index: int,
    label: str,
    seconds: int,
) -> None:
    guild = bot.get_guild(guild_id)
    if guild is None:
        return
    member = guild.get_member(member_id)
    if member is None:
        try:
            member = await guild.fetch_member(member_id)
        except (discord.HTTPException, discord.NotFound):
            return
    embed = discord.Embed(
        title="🎉 Weekly achievement unlocked!",
        description=f"Congratulations {member.mention}!",
        color=rank_color_for_member(guild_id, member_id),
        timestamp=utc_now(),
    )
    embed.add_field(name="Achievement", value=label, inline=True)
    embed.add_field(
        name="Weekly voice time", value=format_hours_minutes(seconds), inline=True
    )
    try:
        await member.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass
    channel = bot.get_channel(ACHIEVEMENT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ACHIEVEMENT_CHANNEL_ID)
        except (discord.HTTPException, discord.NotFound):
            return
    if hasattr(channel, "send"):
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


def schedule_achievement_notification(
    guild_id: int,
    member_id: int,
    rank_index: int,
    label: str,
    seconds: int,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        send_achievement_notification(guild_id, member_id, rank_index, label, seconds)
    )


async def weekly_reset_worker() -> None:
    while not bot.is_closed():
        await asyncio.sleep(60)
        try:
            await reset_weekly_period()
        except (discord.HTTPException, OSError):
            continue


def update_weekly_achievement(
    guild_report: dict[str, Any],
    member_id: int,
    seconds: int,
    now: datetime,
) -> None:
    week_start, _ = week_bounds(now)
    state = report.setdefault(
        "weekly_state", {"period_start": isoformat(week_start), "users": {}}
    )
    if state.get("period_start") != isoformat(week_start):
        state["period_start"] = isoformat(week_start)
        state["users"] = {}
    users = state.setdefault("users", {})
    entry = users.setdefault(
        str(member_id), {"rank_index": 0, "notified_rank_index": -1}
    )
    rank_index, label = achievement_for_seconds(seconds)
    previous_rank = int(entry.get("rank_index", 0))
    notified_rank = int(entry.get("notified_rank_index", -1))
    entry["rank_index"] = rank_index
    entry["label"] = label
    entry["weekly_seconds"] = seconds
    if previous_rank >= 0 and rank_index > previous_rank and rank_index > notified_rank:
        entry["notified_rank_index"] = rank_index
        schedule_achievement_notification(
            int(guild_report.get("guild_id", TRACKED_GUILD_ID or 0)),
            member_id,
            rank_index,
            label,
            seconds,
        )
    save_report()


def is_tracked_channel(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    if TRACKED_GUILD_ID is not None and channel.guild.id != TRACKED_GUILD_ID:
        return False
    return True


def owner_scope(member_id: int, channel_id: int) -> str:
    if MAIN_ID is None or member_id != MAIN_ID:
        return "all"
    if TRACKED_VOICE_CHANNEL_ID is not None and channel_id == TRACKED_VOICE_CHANNEL_ID:
        return "specific"
    return "other"


def get_user_report(
    guild_report: dict[str, Any],
    member_id: int,
    channel_id: int,
    create: bool = False,
) -> dict[str, Any] | None:
    users = (
        guild_report.setdefault("users", {})
        if create
        else guild_report.get("users", {})
    )
    user_report = users.get(str(member_id))
    if user_report is None and create:
        user_report = {"user_name": "", "total_seconds": 0, "sessions": []}
        users[str(member_id)] = user_report

    if user_report is None or owner_scope(member_id, channel_id) == "all":
        return user_report

    scope = owner_scope(member_id, channel_id)
    if "specific" not in user_report and "other" not in user_report:
        legacy = {
            "total_seconds": user_report.get("total_seconds", 0),
            "sessions": user_report.get("sessions", []),
        }
        user_report["specific"] = legacy
        user_report["other"] = {"total_seconds": 0, "sessions": []}
        user_report.pop("total_seconds", None)
        user_report.pop("sessions", None)
    if create:
        user_report.setdefault(scope, {"total_seconds": 0, "sessions": []})
    return user_report.get(scope)


def owner_reports(
    guild_report: dict[str, Any],
    member_id: int,
    specific_only: bool,
) -> list[dict[str, Any]]:
    user_report = guild_report.get("users", {}).get(str(member_id))
    if not user_report:
        return []
    if "specific" not in user_report and "other" not in user_report:
        return [user_report]
    if specific_only:
        return [user_report.get("specific", {"total_seconds": 0, "sessions": []})]
    return [
        user_report.get("specific", {"total_seconds": 0, "sessions": []}),
        user_report.get("other", {"total_seconds": 0, "sessions": []}),
    ]


def start_session(
    member: discord.Member,
    channel: discord.VoiceChannel,
    joined_at: datetime | None = None,
) -> None:
    active_sessions[(member.guild.id, member.id)] = {
        "guild_id": member.guild.id,
        "guild_name": member.guild.name,
        "user_id": member.id,
        "user_name": member.display_name,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "joined_at": joined_at or utc_now(),
    }


def resume_recent_session(
    member: discord.Member,
    channel: discord.VoiceChannel,
    joined_at: datetime,
) -> bool:
    guild_report = report["guilds"].get(str(member.guild.id))
    if not guild_report:
        return False

    user_record = guild_report.get("users", {}).get(str(member.id))
    if not user_record:
        return False
    candidate_reports = (
        owner_reports(guild_report, member.id, specific_only=False)
        if member.id == MAIN_ID
        else [user_record]
    )
    candidates = [
        (user_report, session)
        for user_report in candidate_reports
        for session in user_report.get("sessions", [])
    ]
    if not candidates:
        return False
    previous_report, last_session = max(
        candidates,
        key=lambda item: parse_timestamp(item[1]["left_at"]),
    )
    original_joined_at = parse_timestamp(last_session["joined_at"])
    left_at = parse_timestamp(last_session["left_at"])
    if joined_at - left_at >= SESSION_MERGE_GAP:
        return False

    previous_report["total_seconds"] -= last_session["duration_seconds"]
    previous_report["sessions"].remove(last_session)
    start_session(member, channel, joined_at=original_joined_at)
    save_report()
    return True


def finish_session(session_key: tuple[int, int], left_at: datetime) -> None:
    session = active_sessions.pop(session_key)
    joined_at = session["joined_at"]
    duration = left_at - joined_at
    if duration < MIN_SESSION_DURATION:
        return
    duration_seconds = int(duration.total_seconds())

    guild_report = report["guilds"].setdefault(
        str(session["guild_id"]),
        {
            "guild_id": session["guild_id"],
            "guild_name": session["guild_name"],
            "users": {},
        },
    )
    user_record = guild_report["users"].setdefault(
        str(session["user_id"]),
        {
            "user_name": session["user_name"],
            "total_seconds": 0,
            "sessions": [],
            "daily_streak": 0,
            "last_active_date": "",
        },
    )
    user_record["user_name"] = session["user_name"]
    user_report = get_user_report(
        guild_report,
        session["user_id"],
        session["channel_id"],
        create=True,
    )
    if user_report is None:
        raise ValueError("Unable to create a voice activity report")
    user_report["total_seconds"] += duration_seconds
    user_report["sessions"].append(
        {
            "channel_id": session["channel_id"],
            "channel_name": session["channel_name"],
            "joined_at": isoformat(joined_at),
            "left_at": isoformat(left_at),
            "duration_seconds": duration_seconds,
        }
    )
    ensure_user_milestones(guild_report, session["user_id"], left_at)
    user_record["last_active_date"] = left_at.astimezone(IST).date().isoformat()
    user_record["daily_streak"] = daily_streak_for_user(
        guild_report, session["user_id"], left_at
    )
    save_report()
    update_weekly_achievement(
        guild_report,
        session["user_id"],
        weekly_seconds_for_user(guild_report, session["user_id"], left_at),
        left_at,
    )


def weekly_daily_seconds(
    guild_report: dict[str, Any], member_id: int, now: datetime
) -> list[tuple[date, int]]:
    week_start, _ = week_bounds(now)
    local_start = week_start.astimezone(IST)
    days: list[tuple[date, int]] = []
    for offset in range(7):
        current_local = local_start + timedelta(days=offset)
        start = current_local.astimezone(timezone.utc)
        end = (current_local + timedelta(days=1)).astimezone(timezone.utc)
        seconds = period_seconds_for_user(guild_report, member_id, start, min(end, now))
        for (guild_id, active_member_id), session in active_sessions.items():
            if (
                guild_id == int(guild_report.get("guild_id", guild_id))
                and active_member_id == member_id
            ):
                seconds += max(
                    0,
                    int(
                        (
                            min(now, end) - max(session["joined_at"], start)
                        ).total_seconds()
                    ),
                )
        days.append((current_local.date(), seconds))
    return days


def weekly_graph_embed(
    guild_report: dict[str, Any],
    member: discord.Member,
    now: datetime,
    requested_by: str,
) -> discord.Embed:
    values = weekly_daily_seconds(guild_report, member.id, now)
    max_seconds = max((seconds for _, seconds in values), default=0)
    lines = []
    for day, seconds in values:
        bar_length = int(seconds / max_seconds * 12) if max_seconds else 0
        bar = "▰" * bar_length or "—"
        lines.append(
            f"**{day.strftime('%a %d %b')}**  {bar} {format_hours_minutes(seconds)}"
        )
    embed = discord.Embed(
        title=f"{member.display_name}'s Weekly Graph",
        description="\n".join(lines) or "No voice activity recorded this week.",
        color=rank_color_for_member(member.guild.id, member.id),
        timestamp=now,
    )
    embed.set_footer(text=f"Requested By {requested_by}")
    return embed


class ProfileView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        profile_embed: discord.Embed,
        guild_report: dict[str, Any],
        member: discord.Member,
    ) -> None:
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.profile_embed = profile_embed
        self.guild_report = guild_report
        self.member = member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the command user can use these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="📊 Weekly graph", style=discord.ButtonStyle.secondary)
    async def graph_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        button.disabled = True
        self.back_button.disabled = False
        await interaction.response.edit_message(
            embed=weekly_graph_embed(
                self.guild_report, self.member, utc_now(), interaction.user.display_name
            ),
            view=self,
        )

    @discord.ui.button(
        label="↩️ Profile", style=discord.ButtonStyle.secondary, disabled=True
    )
    async def back_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        button.disabled = True
        self.graph_button.disabled = False
        await interaction.response.edit_message(embed=self.profile_embed, view=self)


MEME_CAPTIONS = (
    "Real Footage of GOJO VS SUKUNA",
    "Mil Gaya Mahoraga (GEGE PLEASE)",
    "Mahoraga Daddy Save Me Please",
    "Agito AUR Mahoraga Come to Save Sukuna",
    "Agito and Mahoraga Hold Hands with Sukuna AWWW",
    "Sukuna OF Leaked",
    "Adaptation of D begins",
    "Actually Mahoraga with Sukuna (leaked)",
)


def meme_path(page: int) -> Path:
    jpg_path = MEME_DIR / f"{page}.jpg"
    if jpg_path.exists():
        return jpg_path
    png_path = MEME_DIR / f"{page}.png"
    if png_path.exists():
        return png_path
    raise FileNotFoundError(f"Missing meme image for page {page}: {jpg_path}")


def meme_embed(
    member: discord.Member,
    rank: str,
    rank_index: int,
    page: int,
    requested_by: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{member.display_name} • {rank}",
        description=MEME_CAPTIONS[page - 1],
        color=achievement_color(rank_index),
        timestamp=utc_now(),
    )
    embed.set_image(url=f"attachment://meme-{page}.jpg")
    embed.set_footer(text=f"{page}/{len(MEME_CAPTIONS)} • Requested By {requested_by}")
    return embed


class MemeView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        member: discord.Member,
        rank: str,
        rank_index: int,
    ) -> None:
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.member = member
        self.rank = rank
        self.rank_index = rank_index
        self.page = 1
        self.previous_button.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the command user can change meme pages.", ephemeral=True
            )
            return False
        return True

    async def render(self, interaction: discord.Interaction) -> None:
        self.previous_button.disabled = self.page == 1
        self.next_button.disabled = self.page == len(MEME_CAPTIONS)
        image_path = meme_path(self.page)
        file = discord.File(image_path, filename=f"meme-{self.page}.jpg")
        await interaction.response.edit_message(
            embed=meme_embed(
                self.member,
                self.rank,
                self.rank_index,
                self.page,
                interaction.user.display_name,
            ),
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page -= 1
        await self.render(interaction)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render(interaction)


class GoalOverwriteView(discord.ui.View):
    def __init__(
        self, requester_id: int, guild_report: dict[str, Any], period: str, hours: float
    ) -> None:
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.guild_report = guild_report
        self.period = period
        self.hours = hours

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=message_embed(
                    "Only the goal owner can confirm this.", title="Not your goal"
                )
            )
            return False
        return True

    @discord.ui.button(label="Overwrite", style=discord.ButtonStyle.danger)
    async def overwrite(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.guild_report.setdefault("goals", {}).setdefault(
            str(self.requester_id), {}
        )[self.period] = {
            "hours": self.hours,
            "created_at": isoformat(utc_now()),
        }
        save_report()
        self.stop()
        await interaction.response.edit_message(
            embed=message_embed(
                f"✅ {self.period.title()} goal updated to **{self.hours:g} hour(s)**.",
                title="Goal updated",
            ),
            view=None,
        )

    @discord.ui.button(label="Keep existing", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=message_embed("No changes made.", title="Goal unchanged"),
            view=None,
        )


def duel_hours_sentence(name: str, seconds: int) -> str:
    hours, minutes = divmod(max(0, int(seconds)) // 60, 60)
    return f"{name} has {hours} hours {minutes} minutes in vc!"


async def edit_message_safe(message: discord.Message, **kwargs: Any) -> None:
    for _ in range(8):
        try:
            await message.edit(**kwargs)
            return
        except discord.RateLimited as error:
            await asyncio.sleep(error.retry_after)
        except discord.HTTPException:
            await asyncio.sleep(1)


def resolve_duel_week(
    choices: dict[int, str], challenger_id: int, challenged_id: int
) -> tuple[str, str]:
    challenger_pick = choices.get(challenger_id)
    challenged_pick = choices.get(challenged_id)
    label = {"past": "Past", "present": "Present"}
    if challenger_pick and challenged_pick:
        if challenger_pick == challenged_pick:
            picked = challenger_pick
            return picked, f"Both fighters chose **{label[picked]}**."
        picked = random.choice(("past", "present"))
        return picked, (
            f"Split decision! "
            f"**{label[challenger_pick]}** vs **{label[challenged_pick]}**. "
            f"Random arena: **{label[picked]}**."
        )
    if challenger_pick or challenged_pick:
        picked = challenger_pick or challenged_pick
        return picked, f"Only one fighter locked in. Arena: **{label[picked]}**."
    return "present", "No one chose in time. Default arena: **Present**."


def duel_scoreboard_embed(
    challenger: discord.Member,
    challenged: discord.Member,
    challenger_rounds: int,
    challenged_rounds: int,
    title: str,
    description: str,
    color: discord.Color,
) -> discord.Embed:
    challenger_name = member_label(
        challenger.guild, challenger.id, challenger.display_name
    )
    challenged_name = member_label(
        challenged.guild, challenged.id, challenged.display_name
    )
    scoreboard = (
        f"**Challenger:** {challenger_name} — {challenger_rounds} rounds won\n"
        f"**Challenged:** {challenged_name} — {challenged_rounds} rounds won\n"
        f"**Score:** {challenger_rounds}/{challenged_rounds}"
    )
    embed = discord.Embed(
        title=title,
        description=f"{scoreboard}\n\n{description}",
        color=color,
        timestamp=utc_now(),
    )
    embed.set_footer(text=f"Requested By {challenger.display_name}")
    return embed


class DuelWeekView(discord.ui.View):
    def __init__(self, challenger_id: int, challenged_id: int) -> None:
        super().__init__(timeout=45)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.choices: dict[int, str] = {}
        self.finished = asyncio.Event()

    def _choice_label(self, user_id: int) -> str:
        return "Past" if self.choices[user_id] == "past" else "Present"

    async def _pick(self, interaction: discord.Interaction, choice: str) -> None:
        user_id = interaction.user.id
        if user_id not in {self.challenger_id, self.challenged_id}:
            await interaction.response.send_message(
                "You are not in this duel.", ephemeral=True
            )
            return
        if user_id in self.choices:
            await interaction.response.send_message(
                f"You already chose {self._choice_label(user_id)}",
                ephemeral=True,
            )
            return
        self.choices[user_id] = choice
        await interaction.response.send_message(
            f"Locked in: {self._choice_label(user_id)}",
            ephemeral=True,
        )
        if len(self.choices) == 2:
            self.finished.set()
            self.stop()

    @discord.ui.button(label="Past", style=discord.ButtonStyle.secondary)
    async def past_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "past")

    @discord.ui.button(label="Present", style=discord.ButtonStyle.primary)
    async def present_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "present")

    async def on_timeout(self) -> None:
        self.finished.set()


async def animate_sentence(
    message: discord.Message,
    build_embed,
    sentence: str,
    delay: float = 0.18,
) -> None:
    words = sentence.split()
    for index in range(1, len(words) + 1):
        await edit_message_safe(
            message, embed=build_embed(" ".join(words[:index])), view=None
        )
        await asyncio.sleep(delay)


async def run_vc_duel(
    message: discord.Message,
    guild_report: dict[str, Any] | None,
    challenger: discord.Member,
    challenged: discord.Member,
    week_key: str,
    now: datetime,
    phase_key: str | None = None,
) -> dict[str, Any]:
    present_start, _ = week_bounds(now)
    if week_key == "past":
        week_start, week_end = week_bounds(present_start - timedelta(seconds=1))
        week_title = "Past"
    else:
        week_start, week_end = present_start, week_bounds(now)[1]
        week_title = "Present"
    week_label = (
        f"{week_start.astimezone(IST):%d %b %Y} – "
        f"{(week_end - timedelta(seconds=1)).astimezone(IST):%d %b %Y}"
    )
    color = rank_color_for_member(challenger.guild.id, challenger.id)
    challenger_rounds = 0
    challenged_rounds = 0
    round_records: list[dict[str, Any]] = []
    days = week_dates(week_start)

    for round_index, day in enumerate(days, start=1):
        completed_phases = ""
        day_label = day.strftime("%a %d %b")
        title = f"Round {round_index}/7 — {day_label}"
        challenger_day = 0
        challenged_day = 0
        challenger_phase_wins = 0
        challenged_phase_wins = 0
        phases = phase_windows(day)
        if phase_key:
            phases = [phase for phase in phases if phase["roman"].lower() == phase_key]
        for phase in phases:
            range_end = min(phase["end"], now)
            challenger_seconds = (
                live_period_seconds(
                    guild_report, challenger.id, phase["start"], range_end
                )
                if guild_report
                else 0
            )
            challenged_seconds = (
                live_period_seconds(
                    guild_report, challenged.id, phase["start"], range_end
                )
                if guild_report
                else 0
            )
            challenger_day += challenger_seconds
            challenged_day += challenged_seconds
            heading = f"PHASE {phase['roman']} ({phase['label']})"
            challenger_name = member_label(
                challenger.guild, challenger.id, challenger.display_name
            )
            challenged_name = member_label(
                challenged.guild, challenged.id, challenged.display_name
            )
            first_line = duel_hours_sentence(challenger_name, challenger_seconds)
            second_line = duel_hours_sentence(challenged_name, challenged_seconds)
            if challenger_seconds > challenged_seconds:
                winner_line = f"Winner of Phase {phase['roman']}: {challenger_name}"
                challenger_phase_wins += 1
            elif challenged_seconds > challenger_seconds:
                winner_line = f"Winner of Phase {phase['roman']}: {challenged_name}"
                challenged_phase_wins += 1
            else:
                winner_line = f"Winner of Phase {phase['roman']}: Draw"

            starter = duel_scoreboard_embed(
                challenger,
                challenged,
                challenger_rounds,
                challenged_rounds,
                title,
                (completed_phases + "\n\n" if completed_phases else "")
                + f"**{heading}**",
                color,
            )
            await edit_message_safe(message, embed=starter, view=None)
            await asyncio.sleep(0.35)
            await animate_sentence(
                message,
                lambda partial, heading=heading, completed_phases=completed_phases: (
                    duel_scoreboard_embed(
                        challenger,
                        challenged,
                        challenger_rounds,
                        challenged_rounds,
                        title,
                        (completed_phases + "\n\n" if completed_phases else "")
                        + f"**{heading}**\n{partial}",
                        color,
                    )
                ),
                first_line,
            )
            await animate_sentence(
                message,
                lambda partial, heading=heading, completed_phases=completed_phases, first_line=first_line: (
                    duel_scoreboard_embed(
                        challenger,
                        challenged,
                        challenger_rounds,
                        challenged_rounds,
                        title,
                        (completed_phases + "\n\n" if completed_phases else "")
                        + f"**{heading}**\n{first_line}\n{partial}",
                        color,
                    )
                ),
                second_line,
            )
            completed_phases = (
                completed_phases + "\n\n" if completed_phases else ""
            ) + f"**{heading}**\n{first_line}\n{second_line}\n{winner_line}"
            await edit_message_safe(
                message,
                embed=duel_scoreboard_embed(
                    challenger,
                    challenged,
                    challenger_rounds,
                    challenged_rounds,
                    title,
                    completed_phases,
                    color,
                ),
                view=None,
            )
            await asyncio.sleep(0.45)

        if challenger_phase_wins > challenged_phase_wins:
            round_winner_id = challenger.id
            challenger_rounds += 1
        elif challenged_phase_wins > challenger_phase_wins:
            round_winner_id = challenged.id
            challenged_rounds += 1
        elif challenger_day > challenged_day:
            round_winner_id = challenger.id
            challenger_rounds += 1
        elif challenged_day > challenger_day:
            round_winner_id = challenged.id
            challenged_rounds += 1
        else:
            round_winner_id = None
        round_winner_name = (
            challenger_name
            if round_winner_id == challenger.id
            else challenged_name
            if round_winner_id == challenged.id
            else "Draw"
        )
        completed_phases += f"\n\n**Round winner:** {round_winner_name}"
        await edit_message_safe(
            message,
            embed=duel_scoreboard_embed(
                challenger,
                challenged,
                challenger_rounds,
                challenged_rounds,
                title,
                completed_phases,
                color,
            ),
            view=None,
        )
        round_records.append(
            {
                "day_label": day_label,
                "challenger_seconds": challenger_day,
                "challenged_seconds": challenged_day,
                "winner_id": round_winner_id,
                "winner_name": round_winner_name,
            }
        )
        if challenger_rounds >= 4 or challenged_rounds >= 4:
            break
        if round_index < 7:
            for remaining in range(5, 0, -1):
                next_round = min(round_index + 1, 7)
                transition_embed = duel_scoreboard_embed(
                    challenger,
                    challenged,
                    challenger_rounds,
                    challenged_rounds,
                    "⚔️ VC Duel",
                    (
                        f"Round {round_index} finished — "
                        f"Round {next_round} begins in **{remaining}**"
                    ),
                    color,
                )
                await edit_message_safe(message, embed=transition_embed, view=None)
                await asyncio.sleep(1)

    if challenger_rounds > challenged_rounds:
        winner_id = challenger.id
        winner_name = challenger.display_name
    elif challenged_rounds > challenger_rounds:
        winner_id = challenged.id
        winner_name = challenged.display_name
    else:
        winner_id = None
        winner_name = "Draw"
    result_embed = duel_scoreboard_embed(
        challenger,
        challenged,
        challenger_rounds,
        challenged_rounds,
        "⚔️ Duel Complete",
        (
            f"**{challenger.display_name}** vs **{challenged.display_name}**\n"
            f"Arena: **{week_title}** ({week_label})\n"
            f"**Winner:** {winner_name}"
        ),
        color,
    )
    await edit_message_safe(message, embed=result_embed, view=None)
    return {
        "challenger_id": challenger.id,
        "challenged_id": challenged.id,
        "challenger_name": challenger.display_name,
        "challenged_name": challenged.display_name,
        "week": week_key,
        "week_label": week_label,
        "phase": phase_key or "all",
        "played_at": isoformat(now),
        "winner_id": winner_id,
        "winner_name": winner_name,
        "challenger_rounds": challenger_rounds,
        "challenged_rounds": challenged_rounds,
        "rounds": round_records,
    }


@bot.event
async def on_ready() -> None:
    global commands_synced, weekly_reset_task
    if not commands_synced:
        if TRACKED_GUILD_ID is not None:
            test_guild = discord.Object(id=TRACKED_GUILD_ID)
            tree.copy_global_to(guild=test_guild)
            await tree.sync(guild=test_guild)
        else:
            await tree.sync()
        commands_synced = True
    await reset_weekly_period()
    if weekly_reset_task is None or weekly_reset_task.done():
        weekly_reset_task = asyncio.create_task(weekly_reset_worker())
    for guild in bot.guilds:
        guild_report = report["guilds"].get(str(guild.id), {})
        active_boss = guild_report.get("boss_battle") or {}
        if active_boss.get("status") == "active" and (
            guild.id not in boss_monitor_tasks or boss_monitor_tasks[guild.id].done()
        ):
            boss_monitor_tasks[guild.id] = asyncio.create_task(monitor_boss(guild.id))
        for voice_channel in guild.voice_channels:
            if not is_tracked_channel(voice_channel):
                continue
            for member in voice_channel.members:
                if not member.bot and (guild.id, member.id) not in active_sessions:
                    start_session(member, voice_channel)
                if not member.bot:
                    enroll_boss_player(member)
    print(f"Logged in as {bot.user}. Tracking active voice sessions.")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if member.bot:
        return
    await reset_weekly_period()
    session_key = (member.guild.id, member.id)
    was_tracked = is_tracked_channel(before.channel)
    is_now_tracked = is_tracked_channel(after.channel)

    if was_tracked and (not is_now_tracked or before.channel.id != after.channel.id):
        if session_key in active_sessions:
            finish_session(session_key, utc_now())

    if is_now_tracked and (not was_tracked or before.channel.id != after.channel.id):
        joined_at = utc_now()
        if not resume_recent_session(member, after.channel, joined_at):
            start_session(member, after.channel)
        enroll_boss_player(member)
        await announce_quest_ready(member)
    elif was_tracked and not is_now_tracked:
        await announce_quest_ready(member)
























































































































































































































































































































































































































































































































































































































































































































































































































































































































def channel_statistics(guild_id: int) -> list[dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    guild_report = report["guilds"].get(str(guild_id), {})
    for user_id in guild_report.get("users", {}):
        for report_part in iter_user_reports(guild_report, int(user_id)):
            for session in report_part.get("sessions", []):
                channel_id = int(session.get("channel_id", 0))
                entry = stats.setdefault(
                    channel_id,
                    {
                        "channel_id": channel_id,
                        "name": session.get("channel_name", f"Channel {channel_id}"),
                        "seconds": 0,
                        "sessions": 0,
                    },
                )
                entry["seconds"] += int(session.get("duration_seconds", 0))
                entry["sessions"] += 1
    for (active_guild_id, _), session in active_sessions.items():
        if active_guild_id != guild_id:
            continue
        entry = stats.setdefault(
            int(session["channel_id"]),
            {
                "channel_id": int(session["channel_id"]),
                "name": session["channel_name"],
                "seconds": 0,
                "sessions": 0,
            },
        )
        entry["seconds"] += max(
            0, int((utc_now() - session["joined_at"]).total_seconds())
        )
        entry["sessions"] += 1
    return sorted(stats.values(), key=lambda item: item["seconds"], reverse=True)


def museum_records(guild_report: dict[str, Any]) -> dict[str, Any]:
    sessions: list[tuple[int, dict[str, Any]]] = []
    channel_totals: dict[int, int] = {}
    day_totals: dict[date, int] = {}
    for user_id in guild_report.get("users", {}):
        for user_report in iter_user_reports(guild_report, int(user_id)):
            for session in user_report.get("sessions", []):
                duration = int(session.get("duration_seconds", 0))
                if duration <= 0:
                    continue
                member_id = int(user_id)
                sessions.append((member_id, session))
                channel_id = int(session.get("channel_id", 0))
                channel_totals[channel_id] = (
                    channel_totals.get(channel_id, 0) + duration
                )
                session_day = (
                    parse_timestamp(session["joined_at"]).astimezone(IST).date()
                )
                day_totals[session_day] = day_totals.get(session_day, 0) + duration
    user_totals = {
        int(user_id): user_total_seconds(guild_report, int(user_id))
        for user_id in guild_report.get("users", {})
    }
    return {
        "longest_session": max(
            sessions, key=lambda item: int(item[1]["duration_seconds"]), default=None
        ),
        "top_user": max(user_totals.items(), key=lambda item: item[1], default=None),
        "top_channel": max(
            channel_totals.items(), key=lambda item: item[1], default=None
        ),
        "peak_day": max(day_totals.items(), key=lambda item: item[1], default=None),
    }


def owner_only(interaction: discord.Interaction) -> bool:
    return MAIN_ID is not None and interaction.user.id == MAIN_ID


def history_embed(
    guild_name: str,
    stats: list[dict[str, Any]],
    page: int,
    requested_by: str,
    color: discord.Color,
) -> discord.Embed:
    page_size = 10
    page_count = max(1, (len(stats) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    entries = stats[page * page_size : (page + 1) * page_size]
    description = (
        "\n".join(
            f"**{page * page_size + index + 1}.** {entry['name']} — "
            f"{format_duration(entry['seconds'])} ({entry['sessions']} session(s))"
            for index, entry in enumerate(entries)
        )
        or "No voice-channel statistics recorded."
    )
    embed = discord.Embed(
        title=f"{guild_name} Voice Channel History",
        description=description,
        color=color,
        timestamp=utc_now(),
    )
    embed.set_footer(text=f"Page {page + 1}/{page_count} • Requested By {requested_by}")
    return embed


class HistoryView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        guild_id: int,
        guild_name: str,
        stats: list[dict[str, Any]],
    ) -> None:
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.stats = stats
        self.page = 0
        self.page_count = max(1, (len(stats) + 9) // 10)
        self.previous_button.disabled = True
        self.next_button.disabled = self.page_count <= 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the command user can use these buttons.", ephemeral=True
            )
            return False
        return True

    async def render(self, interaction: discord.Interaction) -> None:
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1
        await interaction.response.edit_message(
            embed=history_embed(
                self.guild_name,
                self.stats,
                self.page,
                interaction.user.display_name,
                rank_color_for_member(self.guild_id, interaction.user.id),
            ),
            view=self,
        )

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page -= 1
        await self.render(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render(interaction)


def boss_status_embed(
    guild: discord.Guild,
    boss: dict[str, Any],
    page: int = 0,
    requested_by: str | None = None,
) -> discord.Embed:
    health, damage = boss_current_health(boss, utc_now())
    participant_ids = list(boss.get("participants", {}))
    page_count = max(1, (len(participant_ids) + 3) // 4)
    page = max(0, min(page, page_count - 1))
    boss_effects = []
    for effect in boss.get("effects", []):
        if parse_timestamp(effect["expires_at"]) <= utc_now():
            continue
        boss_effect = effect.get("boss_effect")
        if boss_effect is None:
            boss_effect = {
                "poison_splash": "Poisoned",
                "strength_splash": "Strength effect",
                "luck_splash": "Luck effect",
                "invisibility_splash": "Invisibility effect",
                "awkward_splash": "Awkward boast only",
            }.get(effect.get("type"), "Active boss effect")
        boss_effects.append(boss_effect)
    deadline_seconds = max(
        0,
        int((parse_timestamp(boss["expires_at"]) - utc_now()).total_seconds()),
    )
    embed = discord.Embed(
        title=f"👹 {boss['name']} — Battle Stats",
        description=f"**Deadline:** {format_duration(deadline_seconds)} remaining",
        color=discord.Color.dark_red(),
        timestamp=utc_now(),
    )
    embed.add_field(
        name="👹 Boss status",
        value=(
            f"**Health:** `{health:,} / {boss_hp_points(boss):,} HP`\n"
            f"**Damage dealt:** `{max(0, boss_hp_points(boss) - health):,} HP`\n"
            f"**Time remaining:** `{format_duration(deadline_seconds)}`\n"
            f"**Effects:** {', '.join(boss_effects) or 'None'}"
        ),
        inline=False,
    )
    for member_id in participant_ids[page * 4 : (page + 1) * 4]:
        participant = boss["participants"][member_id]
        member_int = int(member_id)
        effect_names = [
            POWERUPS[effect["type"]]["name"]
            for effect in boss.get("effects", [])
            if effect.get("user_id") == member_int
            and parse_timestamp(effect["expires_at"]) > utc_now()
            and effect["type"] in POWERUPS
        ]
        embed.add_field(
            name=f"**{member_label(guild, member_int, participant.get('name'))}**",
            value=(
                f"Damage done (in VC time): **{damage.get(member_int, 0):,} HP**\n"
                f"Potion effects: {', '.join(effect_names) or 'None'}"
            ),
            inline=True,
        )
    footer = f"Player page {page + 1}/{page_count}"
    if requested_by:
        footer += f" • Requested By {requested_by}"
    embed.set_footer(text=footer)
    return embed


class BossStatusView(discord.ui.View):
    def __init__(
        self, requester_id: int, guild: discord.Guild, boss: dict[str, Any]
    ) -> None:
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.guild = guild
        self.boss = boss
        self.page = 0
        self.page_count = max(1, (len(boss.get("participants", {})) + 3) // 4)
        self.previous_button.disabled = True
        self.next_button.disabled = self.page_count <= 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the command user can change battle-stat pages.", ephemeral=True
            )
            return False
        return True

    async def render(self, interaction: discord.Interaction) -> None:
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1
        await interaction.response.edit_message(
            embed=boss_status_embed(
                self.guild,
                self.boss,
                self.page,
                interaction.user.display_name,
            ),
            view=self,
        )

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page -= 1
        await self.render(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page += 1
        await self.render(interaction)


class BossEndView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        guild_report: dict[str, Any],
        boss: dict[str, Any],
        channel: discord.abc.Messageable,
    ) -> None:
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.guild_report = guild_report
        self.boss = boss
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=message_embed(
                    "Only the bot owner can confirm ending this battle.",
                    title="Boss battle control",
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="End battle", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.boss.get("status") != "active":
            self.stop()
            await interaction.response.edit_message(
                embed=message_embed(
                    "This boss battle is no longer active.",
                    title="Boss battle already ended",
                ),
                view=None,
            )
            return
        self.boss["status"] = "ended_by_owner"
        self.boss["ended_at"] = isoformat(utc_now())
        self.boss["ended_by"] = interaction.user.id
        save_report()
        self.stop()
        await interaction.response.edit_message(
            embed=message_embed(
                f"✅ **{self.boss['name']}** has been ended permanently.",
                title="Boss battle ended",
                color=discord.Color.red(),
            ),
            view=None,
        )
        public_embed = discord.Embed(
            title="🛑 Boss battle ended",
            description=(
                f"**{self.boss['name']}** was ended by the server owner.\n"
                "The battle will no longer deal damage, send alerts, or accept "
                "powerups."
            ),
            color=discord.Color.red(),
            timestamp=utc_now(),
        )
        public_embed.set_footer(text=f"Requested By {interaction.user.display_name}")
        try:
            await self.channel.send(embed=public_embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=message_embed(
                "The boss battle is still active. No changes were made.",
                title="Boss battle preserved",
            ),
            view=None,
        )


def channel_detail_statistics(
    guild_report: dict[str, Any], channel_id: int
) -> dict[str, Any]:
    total_seconds = 0
    session_count = 0
    member_seconds: dict[int, int] = {}
    hour_seconds = [0] * 24
    sessions: list[dict[str, Any]] = []
    for user_id in guild_report.get("users", {}):
        member_id = int(user_id)
        for user_report in iter_user_reports(guild_report, member_id):
            for session in user_report.get("sessions", []):
                if int(session.get("channel_id", 0)) != channel_id:
                    continue
                duration = int(session.get("duration_seconds", 0))
                if duration <= 0:
                    continue
                total_seconds += duration
                session_count += 1
                member_seconds[member_id] = member_seconds.get(member_id, 0) + duration
                sessions.append(session)

    for (active_guild_id, member_id), session in active_sessions.items():
        if active_guild_id != int(guild_report["guild_id"]):
            continue
        if int(session["channel_id"]) != channel_id:
            continue
        duration = max(0, int((utc_now() - session["joined_at"]).total_seconds()))
        total_seconds += duration
        session_count += 1
        member_seconds[member_id] = member_seconds.get(member_id, 0) + duration
        sessions.append(
            {
                "joined_at": isoformat(session["joined_at"]),
                "duration_seconds": duration,
                "active": True,
            }
        )

    for session in sessions:
        joined_at = parse_timestamp(session["joined_at"]).astimezone(IST)
        session_end = joined_at + timedelta(
            seconds=int(session.get("duration_seconds", 0))
        )
        if session_end <= joined_at:
            continue
        bucket_start = joined_at.replace(minute=0, second=0, microsecond=0)
        while bucket_start < session_end:
            bucket_end = bucket_start + timedelta(hours=1)
            hour_seconds[bucket_start.hour] += max(
                0,
                int(
                    (
                        min(session_end, bucket_end) - max(joined_at, bucket_start)
                    ).total_seconds()
                ),
            )
            bucket_start = bucket_end
    return {
        "total_seconds": total_seconds,
        "session_count": session_count,
        "member_seconds": member_seconds,
        "hour_seconds": hour_seconds,
    }












































































def weekly_heatmap_values(
    guild_report: dict[str, Any], now: datetime
) -> list[list[int]]:
    week_start, _ = week_bounds(now)
    local_start = week_start.astimezone(IST)
    values = [[0 for _ in range(12)] for _ in range(7)]
    sessions = [
        session
        for user_id in guild_report.get("users", {})
        for user_report in iter_user_reports(guild_report, int(user_id))
        for session in user_report.get("sessions", [])
    ]
    for (guild_id, _), active in active_sessions.items():
        if guild_id == int(guild_report["guild_id"]):
            sessions.append(
                {
                    "joined_at": isoformat(active["joined_at"]),
                    "left_at": isoformat(now),
                    "duration_seconds": max(
                        0, int((now - active["joined_at"]).total_seconds())
                    ),
                }
            )
    for session in sessions:
        joined_at = parse_timestamp(session["joined_at"])
        session_end = joined_at + timedelta(
            seconds=int(session.get("duration_seconds", 0))
        )
        for day_index in range(7):
            day_start = local_start + timedelta(days=day_index)
            for bucket in range(12):
                bucket_start = day_start + timedelta(hours=bucket * 2)
                bucket_end = bucket_start + timedelta(hours=2)
                overlap = max(
                    timedelta(0),
                    min(session_end, bucket_end.astimezone(timezone.utc))
                    - max(joined_at, bucket_start.astimezone(timezone.utc)),
                )
                values[day_index][bucket] += int(overlap.total_seconds())
    return values






























































































































































































































def activate_boss_powerup(
    guild: discord.Guild,
    boss: dict[str, Any],
    user_id: int,
    powerup_key: str,
) -> dict[str, Any]:
    now = utc_now()
    active_members = [
        member.id
        for voice_channel in guild.voice_channels
        if is_tracked_channel(voice_channel)
        for member in voice_channel.members
        if not member.bot
    ]
    durations = {
        "poison_splash": timedelta(minutes=10),
        "strength_splash": timedelta(minutes=5),
        "luck_splash": timedelta(minutes=10),
        "invisibility_splash": timedelta(minutes=10),
        "awkward_splash": timedelta(minutes=5),
    }
    effect: dict[str, Any] = {
        "type": powerup_key,
        "user_id": user_id,
        "started_at": isoformat(now),
        "expires_at": isoformat(now + durations[powerup_key]),
    }
    if powerup_key == "poison_splash":
        effect["weakened_members"] = active_members if random.random() < 0.15 else []
        effect["boss_effect"] = "Poisoned: loses 0.03% max HP every 10 seconds"
        effect["activation_message"] = "☠️ Poison is active for 10 minutes."
        return effect
    if powerup_key == "strength_splash":
        if random.random() < 0.15:
            uses = sum(
                1
                for item in boss.get("effects", [])
                if item["type"] == "strength_splash"
            )
            boss["hp_points"] = int(boss_hp_points(boss) * (1.10 + uses * 0.05))
            effect["boss_strengthened"] = True
        effect["boss_effect"] = (
            "Strengthened: may gain 10% max HP (plus 5% per repeated proc)"
            if effect.get("boss_strengthened")
            else "No boss strength proc"
        )
        effect["activation_message"] = "💪 Strength is active for 5 minutes."
        return effect
    if powerup_key == "luck_splash":
        effect["boss_regenerates"] = random.random() < 0.10
        effect["boss_effect"] = (
            "Regenerating: gains 1% max HP per minute for 10 minutes"
            if effect["boss_regenerates"]
            else "No boss regeneration proc"
        )
        effect["activation_message"] = "🍀 Luck is active for 10 minutes."
        return effect
    if powerup_key == "invisibility_splash":
        effect["current_members"] = active_members
        effect["boss_harder"] = random.random() < 0.10
        effect["boss_effect"] = (
            "Hardened: has a 25% chance to miss each calculated hit"
            if effect["boss_harder"]
            else "No boss hardening proc"
        )
        effect["activation_message"] = "🫥 Invisibility is active for 10 minutes."
        return effect
    effect["boast"] = (
        f"**{member_label(guild, user_id)}**'s stats are immensely increased!"
        if random.random() < 0.50
        else f"**{boss['name']}**'s stats are immensely increased!"
    )
    effect["activation_message"] = f"😬 Awkward effect: {effect['boast']}"
    effect["boss_effect"] = "Awkward boast only: no mechanical boss effect"
    return effect
































































































































































































































































































































































































































































































































































































































































































































































































































def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Copy .env.example to .env and set it."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
