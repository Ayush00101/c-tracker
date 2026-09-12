import csv
import asyncio
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from dotenv import load_dotenv


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
TRACKED_GUILD_ID = _optional_int(os.getenv("TRACKED_GUILD_ID"))
TRACKED_VOICE_CHANNEL_ID = _optional_int(os.getenv("TRACKED_VOICE_CHANNEL_ID"))
MAIN_ID = _optional_int(os.getenv("MAIN_ID"))
IST = timezone(timedelta(hours=5, minutes=30))
ACHIEVEMENT_CHANNEL_ID = 1401597433057771613
SESSION_MERGE_GAP = timedelta(minutes=10)
MIN_SESSION_DURATION = timedelta(minutes=5)
ACHIEVEMENT_RANKS = (
    (0, 5, "Chodu"),
    (5, 10, "Tip Of Dihh"),
    (10, 15, "C ka Bacha Hai Tu"),
    (15, 20, "\"C\"hootad Licker"),
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

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
active_sessions: dict[tuple[int, int], dict[str, Any]] = {}
commands_synced = False
weekly_reset_task: asyncio.Task[None] | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


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


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(IST)
    month_start = local_now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1, month=1
        )
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
            if guild_id == int(guild_report.get("guild_id", guild_id)) and active_member_id == member_id:
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
    total = period_seconds_for_user(guild_report, member_id, week_start, min(week_end, now))
    for (guild_id, active_member_id), session in active_sessions.items():
        if guild_id != int(guild_report.get("guild_id", guild_id)) or active_member_id != member_id:
            continue
        total += max(
            0,
            int(
                (
                    min(now, week_end)
                    - max(session["joined_at"], week_start)
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
                    min(utc_now(), range_end)
                    - max(session["joined_at"], range_start)
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


def progress_bar(current: int, target: int, width: int = 12) -> str:
    filled = min(width, int(current / target * width)) if target > 0 else width
    return "▰" * filled + "▱" * (width - filled)


def parse_week_input(value: str | None, now: datetime) -> tuple[datetime, datetime, str]:
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
        guild_report["guild_id"] = int(guild_id)
        for user_record in guild_report["users"].values():
            user_record.setdefault("daily_streak", 0)
            user_record.setdefault("last_active_date", "")
            user_record.setdefault("milestones", [])


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
        raise ValueError(f"{REPORT_PATH} must contain a JSON object with a 'guilds' object")
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

    previous_start = parse_timestamp(previous_start_text) if previous_start_text else None
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
                if active_guild_id == int(guild_id) and active_member_id == int(user_id):
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
    hours = seconds / 3600
    embed = discord.Embed(
        title="🎉 Weekly achievement unlocked!",
        description=f"Congratulations {member.mention}!",
        color=rank_color_for_member(guild_id, member_id),
        timestamp=utc_now(),
    )
    embed.add_field(name="Achievement", value=label, inline=True)
    embed.add_field(name="Weekly voice time", value=f"{hours:.2f} hours", inline=True)
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
    users = guild_report.setdefault("users", {}) if create else guild_report.get("users", {})
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
        {"guild_id": session["guild_id"], "guild_name": session["guild_name"], "users": {}},
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
        seconds = period_seconds_for_user(
            guild_report, member_id, start, min(end, now)
        )
        for (guild_id, active_member_id), session in active_sessions.items():
            if guild_id == int(guild_report.get("guild_id", guild_id)) and active_member_id == member_id:
                seconds += max(
                    0,
                    int((min(now, end) - max(session["joined_at"], start)).total_seconds()),
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
        lines.append(f"**{day.strftime('%a %d %b')}**  {bar} {seconds / 3600:.2f}h")
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

    @discord.ui.button(label="↩️ Profile", style=discord.ButtonStyle.secondary, disabled=True)
    async def back_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        button.disabled = True
        self.graph_button.disabled = False
        await interaction.response.edit_message(embed=self.profile_embed, view=self)


class GoalOverwriteView(discord.ui.View):
    def __init__(self, requester_id: int, guild_report: dict[str, Any], period: str, hours: float) -> None:
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.guild_report = guild_report
        self.period = period
        self.hours = hours

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the goal owner can confirm this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Overwrite", style=discord.ButtonStyle.danger)
    async def overwrite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.guild_report.setdefault("goals", {}).setdefault(str(self.requester_id), {})[self.period] = {
            "hours": self.hours,
            "created_at": isoformat(utc_now()),
        }
        save_report()
        self.stop()
        await interaction.response.edit_message(content=f"✅ {self.period.title()} goal updated to {self.hours:g} hour(s).", view=None)

    @discord.ui.button(label="Keep existing", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="No changes made.", view=None)


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
        for voice_channel in guild.voice_channels:
            if not is_tracked_channel(voice_channel):
                continue
            for member in voice_channel.members:
                if not member.bot and (guild.id, member.id) not in active_sessions:
                    start_session(member, voice_channel)
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


@tree.command(name="profile", description="Show a user's voice channel activity profile")
@app_commands.describe(user="The server member whose voice profile you want to view")
async def profile(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    target = user
    if target is None:
        if isinstance(interaction.user, discord.Member):
            target = interaction.user
        else:
            target = await interaction.guild.fetch_member(interaction.user.id)

    if target is None:
        await interaction.response.send_message(
            "I could not find that member in this server.", ephemeral=True
        )
        return

    guild_report = report["guilds"].get(str(interaction.guild.id))
    reports = (
        owner_reports(guild_report, target.id, specific_only=False)
        if guild_report
        else []
    )
    if target.id != MAIN_ID and guild_report:
        user_report = guild_report.get("users", {}).get(str(target.id))
        reports = [user_report] if user_report else []
    active_session = active_sessions.get((interaction.guild.id, target.id))
    sessions = [
        session
        for user_report in reports
        for session in user_report.get("sessions", [])
    ]

    total_seconds = sum(int(user_report.get("total_seconds", 0)) for user_report in reports)
    max_seconds = max(
        (int(session["duration_seconds"]) for session in sessions),
        default=0,
    )
    if active_session:
        current_duration = int((utc_now() - active_session["joined_at"]).total_seconds())
        total_seconds += current_duration
        max_seconds = max(max_seconds, current_duration)

    recent_sessions: list[dict[str, Any]] = sessions[-5:]
    if active_session:
        recent_sessions.append(
            {
                "joined_at": isoformat(active_session["joined_at"]),
                "left_at": None,
                "duration_seconds": int(
                    (utc_now() - active_session["joined_at"]).total_seconds()
                ),
                "active": True,
            }
        )
    recent_sessions = recent_sessions[-5:]

    if active_session:
        active_duration = recent_sessions[-1]["duration_seconds"]
        last_session_text = (
            f"Currently in VC\n"
            f"Time: {format_local(active_session['joined_at'])} - now, "
            f"{format_date(active_session['joined_at'])}\n"
            f"Duration: {format_duration(active_duration)}"
        )
    elif sessions:
        last_session = sessions[-1]
        joined_at = parse_timestamp(last_session["joined_at"])
        left_at = parse_timestamp(last_session["left_at"])
        last_session_text = (
            f"Time: {format_local(joined_at)} - {format_local(left_at)}, "
            f"{format_date(joined_at)}\n"
            f"Duration: {format_duration(int(last_session['duration_seconds']))}"
        )
    else:
        last_session_text = "No completed session"

    recent_text = "\n".join(
        (
            f"{'🟢' if session.get('active') else '•'} "
            f"{format_local(parse_timestamp(session['joined_at']))}, "
            f"{format_date(parse_timestamp(session['joined_at']))} — "
            f"{'In progress' if session.get('active') else format_duration(int(session['duration_seconds']))}"
        )
        for session in reversed(recent_sessions)
    ) or "No sessions recorded"

    daily_streak = (
        daily_streak_for_user(guild_report, target.id, utc_now())
        if guild_report
        else 0
    )
    weekly_seconds = (
        weekly_seconds_for_user(guild_report, target.id, utc_now())
        if guild_report
        else 0
    )
    weekly_rank_index, weekly_rank = achievement_for_seconds(weekly_seconds)
    embed = discord.Embed(
        title=(
            f"{target.display_name}'s C Timings"
            if MAIN_ID is not None and target.id == MAIN_ID
            else f"{target.display_name}'s VC Timings"
        ),
        color=achievement_color(weekly_rank_index),
        timestamp=utc_now(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Total time", value=format_duration(total_seconds), inline=True)
    embed.add_field(name="Longest session", value=format_duration(max_seconds), inline=True)
    embed.add_field(
        name="Times in VC",
        value=str(len(sessions) + int(bool(active_session))),
        inline=True,
    )
    embed.add_field(name="Daily streak", value=f"🔥 {daily_streak} day(s)", inline=True)
    embed.add_field(
        name="Current weekly rank",
        value=f"{weekly_rank}\n{weekly_seconds / 3600:.2f} hours",
        inline=True,
    )
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(
        embed=embed,
        view=ProfileView(
            interaction.user.id,
            embed,
            guild_report or {"guild_id": interaction.guild.id, "users": {}},
            target,
        ),
    )


@tree.command(name="achievements", description="Show a user's voice achievements")
@app_commands.describe(user="The server member whose achievements you want to view")
async def achievements(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    target = user
    if target is None:
        target = (
            interaction.user
            if isinstance(interaction.user, discord.Member)
            else await interaction.guild.fetch_member(interaction.user.id)
        )
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            "No achievements have been recorded yet.", ephemeral=True
        )
        return

    now = utc_now()
    week_start, week_end = week_bounds(now)
    month_start, month_end = month_bounds(now)
    day_start, day_end = day_bounds(now)
    weekly_seconds = live_period_seconds(
        guild_report, target.id, week_start, min(week_end, now)
    )
    monthly_seconds = live_period_seconds(
        guild_report, target.id, month_start, min(month_end, now)
    )
    daily_seconds = live_period_seconds(
        guild_report, target.id, day_start, min(day_end, now)
    )
    daily_streak = daily_streak_for_user(guild_report, target.id, now)
    milestones = ensure_user_milestones(guild_report, target.id, now)
    save_report()
    weekly_rank_index, weekly_rank = achievement_for_seconds(weekly_seconds)

    embed = discord.Embed(
        title=f"{target.display_name}'s Achievements",
        description="Voice milestones and current period records",
        color=achievement_color(weekly_rank_index),
        timestamp=now,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(
        name="Weekly highest",
        value=f"{weekly_rank}\n{weekly_seconds / 3600:.2f} hours",
        inline=True,
    )
    embed.add_field(
        name="Monthly highest",
        value=f"{monthly_seconds / 3600:.2f} hours",
        inline=True,
    )
    embed.add_field(
        name="Daily",
        value=f"{daily_seconds / 3600:.2f} hours\n🔥 {daily_streak} day streak",
        inline=True,
    )
    if milestones:
        for milestone in milestones:
            achieved_at = parse_timestamp(milestone["achieved_at"])
            embed.add_field(
                name=f"{milestone['badge']} {milestone['hours']} hour milestone",
                value=(
                    f"Achieved: {format_local(achieved_at)}, "
                    f"{format_date(achieved_at)}"
                ),
                inline=False,
            )
    else:
        embed.add_field(
            name="Milestones",
            value="No 50-hour milestones reached yet.",
            inline=False,
        )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="recap", description="Show a user's weekly voice recap")
@app_commands.describe(user="The member to recap")
async def recap(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return
    target = user or interaction.user
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message("No weekly activity recorded.", ephemeral=True)
        return
    now = utc_now()
    start, end = week_bounds(now)
    total = live_period_seconds(guild_report, target.id, start, min(end, now))
    values = weekly_daily_seconds(guild_report, target.id, now)
    rank_index, rank = achievement_for_seconds(total)
    embed = discord.Embed(title=f"{target.display_name}'s Weekly Recap", color=achievement_color(rank_index), timestamp=now)
    embed.add_field(name="Total", value=format_duration(total), inline=True)
    embed.add_field(name="Rank", value=f"{rank}\n{total / 3600:.2f} hours", inline=True)
    embed.add_field(name="Daily breakdown", value="\n".join(f"**{day:%a}** — {format_duration(seconds)}" for day, seconds in values), inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, view=ProfileView(interaction.user.id, embed, guild_report, target))


@tree.command(name="progress", description="Show progress toward your next ranks")
@app_commands.describe(user="The member whose progress you want to view")
async def progress(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return
    target = user or interaction.user
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message("No voice activity recorded.", ephemeral=True)
        return
    now = utc_now()
    weekly = weekly_seconds_for_user(guild_report, target.id, now)
    rank_index, rank = achievement_for_seconds(weekly)
    _, upper, _ = ACHIEVEMENT_RANKS[rank_index]
    total = live_total_seconds(guild_report, target.id)
    milestone = next((hours for hours in MILESTONE_HOURS if total < hours * 3600), None)
    weekly_value = f"{weekly / 3600:.2f} hours at **{rank}**"
    if upper is not None:
        weekly_value += f"\n{progress_bar(weekly, int(upper * 3600))}\n{format_duration(max(0, int(upper * 3600 - weekly)))} until **{ACHIEVEMENT_RANKS[rank_index + 1][2]}**"
    overall_value = f"{total / 3600:.2f} total hours"
    if milestone is not None:
        overall_value += f"\n{progress_bar(total, milestone * 3600)}\n{format_duration(max(0, milestone * 3600 - total))} until {milestone}-hour milestone"
    embed = discord.Embed(title=f"{target.display_name}'s Progress", color=achievement_color(rank_index), timestamp=now)
    embed.add_field(name="Weekly rank", value=weekly_value, inline=False)
    embed.add_field(name="Overall hours", value=overall_value, inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="goal", description="Set a daily, weekly, or monthly voice goal")
@app_commands.describe(hours="Target hours", period="daily, weekly, or monthly")
@app_commands.choices(period=[
    app_commands.Choice(name="Daily", value="daily"),
    app_commands.Choice(name="Weekly", value="weekly"),
    app_commands.Choice(name="Monthly", value="monthly"),
])
async def goal(interaction: discord.Interaction, hours: float, period: app_commands.Choice[str] | None = None) -> None:
    if interaction.guild is None or not 0 < hours <= 10000:
        await interaction.response.send_message("Use this command in the tracked server with a positive goal.", ephemeral=True)
        return
    period_name = period.value if period else "daily"
    guild_report = report["guilds"].setdefault(str(interaction.guild.id), {"guild_id": interaction.guild.id, "users": {}, "goals": {}})
    existing = guild_report.setdefault("goals", {}).get(str(interaction.user.id), {}).get(period_name)
    if existing:
        await interaction.response.send_message(f"You already have a {period_name} goal. Overwrite it?", ephemeral=True, view=GoalOverwriteView(interaction.user.id, guild_report, period_name, hours))
        return
    guild_report["goals"].setdefault(str(interaction.user.id), {})[period_name] = {"hours": hours, "created_at": isoformat(utc_now())}
    save_report()
    await interaction.response.send_message(f"🎯 {period_name.title()} goal set to **{hours:g} hour(s)**.")


@tree.command(name="timecapsule", description="Review or compare historical weekly activity")
@app_commands.describe(week="YYYY-MM-DD week date", compare_week="Optional second YYYY-MM-DD week date")
async def timecapsule(interaction: discord.Interaction, week: str | None = None, compare_week: str | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message("No historical activity recorded.", ephemeral=True)
        return
    try:
        start, end, label = parse_week_input(week, utc_now())
        comparison = None
        if compare_week:
            cstart, cend, clabel = parse_week_input(compare_week, utc_now())
            comparison = timecapsule_snapshot(guild_report, cstart, cend)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    snapshot = timecapsule_snapshot(guild_report, start, end)
    users = sorted(snapshot["users"].items(), key=lambda item: item[1], reverse=True)[:10]
    channels = sorted(snapshot["channels"].items(), key=lambda item: item[1], reverse=True)[:10]
    def lines(items: list[tuple[int, int]], key: str) -> str:
        return "\n".join(
            f"**{(interaction.guild.get_member(item_id).display_name if key == 'users' and interaction.guild.get_member(item_id) else interaction.guild.get_channel(item_id).name if key == 'channels' and interaction.guild.get_channel(item_id) else item_id)}** — {format_duration(seconds)}"
            + (f" ({delta_text(seconds - comparison.get(key, {}).get(item_id, 0))})" if comparison else "")
            for item_id, seconds in items
        ) or "No activity recorded."
    embed = discord.Embed(title="⏳ Time Capsule", description=label, color=rank_color_for_member(interaction.guild.id, interaction.user.id), timestamp=utc_now())
    embed.add_field(name="Users", value=lines(users, "users"), inline=False)
    embed.add_field(name="Channels", value=lines(channels, "channels"), inline=False)
    if comparison:
        embed.set_footer(text=f"Compared with {compare_week} • Deltas use + / -")
    await interaction.response.send_message(embed=embed)


@tree.command(name="leaderboard", description="Show this server's VC leaderboard")
@app_commands.describe(period="Leaderboard period (weekly by default)")
@app_commands.choices(
    period=[
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Monthly", value="monthly"),
    ]
)
async def leaderboard(
    interaction: discord.Interaction,
    period: app_commands.Choice[str] | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    if TRACKED_GUILD_ID is None or interaction.guild.id != TRACKED_GUILD_ID:
        await interaction.response.send_message(
            "This command is available only in the configured tracked server.",
            ephemeral=True,
        )
        return

    now = utc_now()
    await reset_weekly_period(now)
    period_name = period.value if period else "weekly"
    if period_name == "daily":
        range_start, range_end = day_bounds(now)
        period_label = now.astimezone(IST).strftime("%d %b %Y")
        empty_label = "today"
    elif period_name == "monthly":
        range_start, range_end = month_bounds(now)
        period_label = now.astimezone(IST).strftime("%B %Y")
        empty_label = "this month"
    else:
        range_start, range_end = week_bounds(now)
        period_label = (
            f"{range_start.astimezone(IST):%d %b} – "
            f"{(range_end - timedelta(seconds=1)).astimezone(IST):%d %b %Y}"
        )
        empty_label = "this week"
    guild_report = report["guilds"].get(str(interaction.guild.id))
    totals: dict[int, dict[str, Any]] = {}

    if guild_report:
        for user_id, user_record in guild_report.get("users", {}).items():
            member_id = int(user_id)
            period_seconds = period_seconds_for_user(
                guild_report, member_id, range_start, min(range_end, now)
            )
            totals[member_id] = {
                "name": user_record.get("user_name", f"User {member_id}"),
                "seconds": period_seconds,
            }

    for (guild_id, member_id), session in active_sessions.items():
        if guild_id != interaction.guild.id:
            continue
        session_start = max(session["joined_at"], range_start)
        session_end = min(now, range_end)
        active_seconds = max(0, int((session_end - session_start).total_seconds()))
        if active_seconds:
            entry = totals.setdefault(
                member_id,
                {"name": session["user_name"], "seconds": 0},
            )
            entry["seconds"] += active_seconds

    ranked = sorted(
        (entry for entry in totals.values() if entry["seconds"] > 0),
        key=lambda entry: entry["seconds"],
        reverse=True,
    )
    if ranked:
        medals = ("🥇", "🥈", "🥉")
        leaderboard_text = "\n".join(
            f"{medals[index] if index < 3 else f'**{index + 1}.**'} "
            f"{entry['name']} — {format_duration(entry['seconds'])}"
            for index, entry in enumerate(ranked[:10])
        )
    else:
        leaderboard_text = f"No qualifying VC activity recorded {empty_label}."

    embed = discord.Embed(
        title=f"{interaction.guild.name} VC Leaderboard",
        description=f"{period_name.title()} voice activity — {period_label}",
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=now,
    )
    embed.add_field(name="Rankings", value=leaderboard_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


def channel_statistics(guild_id: int) -> list[dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    guild_report = report["guilds"].get(str(guild_id), {})
    for user_id in guild_report.get("users", {}):
        for report_part in iter_user_reports(guild_report, int(user_id)):
            for session in report_part.get("sessions", []):
                channel_id = int(session.get("channel_id", 0))
                entry = stats.setdefault(
                    channel_id,
                    {"name": session.get("channel_name", f"Channel {channel_id}"), "seconds": 0, "sessions": 0},
                )
                entry["seconds"] += int(session.get("duration_seconds", 0))
                entry["sessions"] += 1
    for (active_guild_id, _), session in active_sessions.items():
        if active_guild_id != guild_id:
            continue
        entry = stats.setdefault(
            int(session["channel_id"]),
            {"name": session["channel_name"], "seconds": 0, "sessions": 0},
        )
        entry["seconds"] += max(0, int((utc_now() - session["joined_at"]).total_seconds()))
        entry["sessions"] += 1
    return sorted(stats.values(), key=lambda item: item["seconds"], reverse=True)


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
    description = "\n".join(
        f"**{page * page_size + index + 1}.** {entry['name']} — "
        f"{format_duration(entry['seconds'])} ({entry['sessions']} session(s))"
        for index, entry in enumerate(entries)
    ) or "No voice-channel statistics recorded."
    embed = discord.Embed(
        title=f"{guild_name} Voice Channel History",
        description=description,
        color=color,
        timestamp=utc_now(),
    )
    embed.set_footer(
        text=f"Page {page + 1}/{page_count} • Requested By {requested_by}"
    )
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


@tree.command(name="vcstats", description="Show the voice-channel leaderboard")
async def vcstats(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    stats = channel_statistics(interaction.guild.id)
    rankings = "\n".join(
        f"**{index + 1}.** {entry['name']} — {format_duration(entry['seconds'])}"
        for index, entry in enumerate(stats[:10])
    ) or "No voice-channel activity recorded."
    embed = discord.Embed(
        title=f"{interaction.guild.name} VC Channel Leaderboard",
        description=rankings,
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="csv", description="Send the voice activity CSV report to the owner")
async def csv_report(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    if not owner_only(interaction):
        await interaction.response.send_message(
            "Only the bot owner can use this command.", ephemeral=True
        )
        return
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["guild_id", "guild_name", "user_id", "user_name", "channel_id", "channel_name",
         "joined_at", "left_at", "duration_seconds"]
    )
    rows = 0
    for guild_id, guild_report in report.get("guilds", {}).items():
        for user_id, user_record in guild_report.get("users", {}).items():
            for user_report in iter_user_reports(guild_report, int(user_id)):
                for session in user_report.get("sessions", []):
                    writer.writerow(
                        [
                            guild_id,
                            guild_report.get("guild_name", ""),
                            user_id,
                            user_record.get("user_name", ""),
                            session.get("channel_id", ""),
                            session.get("channel_name", ""),
                            session.get("joined_at", ""),
                            session.get("left_at", ""),
                            session.get("duration_seconds", 0),
                        ]
                    )
                    rows += 1
    output.seek(0)
    file = discord.File(
        io.BytesIO(output.getvalue().encode("utf-8")),
        filename="voice_activity_report.csv",
    )
    try:
        await interaction.user.send(
            content=f"Voice activity CSV report ({rows} completed session(s)).",
            file=file,
        )
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message(
            "I could not DM the CSV report. Please enable your DMs and try again.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "The CSV report was sent to your DMs.", ephemeral=True
    )


@tree.command(name="history", description="Browse voice-channel history (owner only)")
async def history(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    if not owner_only(interaction):
        await interaction.response.send_message(
            "Only the bot owner can use this command.", ephemeral=True
        )
        return
    stats = channel_statistics(interaction.guild.id)
    view = HistoryView(
        interaction.user.id,
        interaction.guild.id,
        interaction.guild.name,
        stats,
    )
    await interaction.response.send_message(
        embed=history_embed(
            interaction.guild.name,
            stats,
            0,
            interaction.user.display_name,
            rank_color_for_member(interaction.guild.id, interaction.user.id),
        ),
        view=view,
    )


@tree.command(name="current", description="Show non-bot users currently in voice channels")
async def current(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"{interaction.guild.name} — Current Voice Users",
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    visible_channels = 0
    for voice_channel in interaction.guild.voice_channels:
        if not is_tracked_channel(voice_channel):
            continue
        members = [member for member in voice_channel.members if not member.bot]
        if not members:
            continue
        visible_channels += 1
        names = "\n".join(f"• {member.display_name}" for member in members)
        embed.add_field(
            name=f"{voice_channel.name} ({len(members)})",
            value=names[:1024],
            inline=False,
        )

    if not visible_channels:
        embed.description = "No non-bot users are currently in a voice channel."
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="cprofile", description="Show the owner's specific channel profile")
async def cprofile(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    allowed_users = {int(user_id) for user_id in report.get("cprofile_users", [])}
    if interaction.user.id != MAIN_ID and interaction.user.id not in allowed_users:
        await interaction.response.send_message(
            "You are not authorized to use this command.", ephemeral=True
        )
        return
    target = interaction.guild.get_member(interaction.user.id)
    if target is None:
        target = await interaction.guild.fetch_member(interaction.user.id)
    if target is None:
        await interaction.response.send_message(
            "I could not find your member profile in this server.", ephemeral=True
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if target.id == MAIN_ID and guild_report:
        reports = owner_reports(guild_report, target.id, specific_only=True)
        user_report = reports[0] if reports else None
        sessions = list(user_report.get("sessions", [])) if user_report else []
    else:
        user_record = (
            guild_report.get("users", {}).get(str(target.id))
            if guild_report
            else None
        )
        sessions = [
            session
            for session in (user_record or {}).get("sessions", [])
            if TRACKED_VOICE_CHANNEL_ID is None
            or int(session.get("channel_id", 0)) == TRACKED_VOICE_CHANNEL_ID
        ]
    active_session = active_sessions.get((interaction.guild.id, target.id))
    active_is_specific = (
        active_session is not None
        and (
            TRACKED_VOICE_CHANNEL_ID is None
            or active_session["channel_id"] == TRACKED_VOICE_CHANNEL_ID
        )
    )
    total_seconds = int(user_report.get("total_seconds", 0)) if user_report else 0
    if active_session and active_is_specific:
        current_duration = int((utc_now() - active_session["joined_at"]).total_seconds())
        total_seconds += current_duration
    max_seconds = max(
        (int(session["duration_seconds"]) for session in sessions),
        default=0,
    )
    if active_session and active_is_specific:
        max_seconds = max(max_seconds, current_duration)
    recent_sessions = sessions[-5:]
    if active_session and active_is_specific:
        recent_sessions.append(
            {
                "joined_at": isoformat(active_session["joined_at"]),
                "duration_seconds": current_duration,
                "active": True,
            }
        )
    recent_sessions = recent_sessions[-5:]
    if active_session and active_is_specific:
        last_session_text = (
            f"Currently in VC\nTime: {format_local(active_session['joined_at'])} - now, "
            f"{format_date(active_session['joined_at'])}\n"
            f"Duration: {format_duration(current_duration)}"
        )
    elif sessions:
        last_session = sessions[-1]
        joined_at = parse_timestamp(last_session["joined_at"])
        left_at = parse_timestamp(last_session["left_at"])
        last_session_text = (
            f"Time: {format_local(joined_at)} - {format_local(left_at)}, "
            f"{format_date(joined_at)}\n"
            f"Duration: {format_duration(int(last_session['duration_seconds']))}"
        )
    else:
        last_session_text = "No completed session"
    recent_text = "\n".join(
        f"{'🟢' if session.get('active') else '•'} "
        f"{format_local(parse_timestamp(session['joined_at']))}, "
        f"{format_date(parse_timestamp(session['joined_at']))} — "
        f"{'In progress' if session.get('active') else format_duration(int(session['duration_seconds']))}"
        for session in reversed(recent_sessions)
    ) or "No sessions recorded"
    cprofile_now = utc_now()
    cprofile_week_start, cprofile_week_end = week_bounds(cprofile_now)
    cprofile_weekly_seconds = sum(
        session_seconds_in_range(
            session,
            cprofile_week_start,
            min(cprofile_week_end, cprofile_now),
        )
        for session in sessions
    )
    if active_session and active_is_specific:
        cprofile_weekly_seconds += max(
            0,
            int(
                (
                    min(cprofile_now, cprofile_week_end)
                    - max(active_session["joined_at"], cprofile_week_start)
                ).total_seconds()
            ),
        )
    cprofile_rank_index, cprofile_rank = achievement_for_seconds(
        cprofile_weekly_seconds
    )
    embed = discord.Embed(
        title=f"{target.display_name}'s C Timings",
        color=achievement_color(cprofile_rank_index),
        timestamp=utc_now(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Total time", value=format_duration(total_seconds), inline=True)
    embed.add_field(name="Longest session", value=format_duration(max_seconds), inline=True)
    embed.add_field(name="Times in VC", value=str(len(sessions) + int(active_is_specific)), inline=True)
    embed.add_field(
        name="Current weekly rank",
        value=f"{cprofile_rank}\n{cprofile_weekly_seconds / 3600:.2f} hours",
        inline=True,
    )
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="add", description="Allow a user to use the owner's cprofile")
@app_commands.describe(user="The member to authorize for /cprofile")
async def add_cprofile_user(
    interaction: discord.Interaction,
    user: discord.Member,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    if MAIN_ID is None or interaction.user.id != MAIN_ID:
        await interaction.response.send_message(
            "Only the bot owner can use this command.", ephemeral=True
        )
        return

    authorized_users = report.setdefault("cprofile_users", [])
    if user.id not in authorized_users:
        authorized_users.append(user.id)
        save_report()
        message = f"{user.mention} can now use `/cprofile`."
    else:
        message = f"{user.mention} already has `/cprofile` access."
    await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it.")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
