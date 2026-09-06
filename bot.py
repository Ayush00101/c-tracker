import json
import os
from datetime import datetime, timedelta, timezone
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
SESSION_MERGE_GAP = timedelta(minutes=10)
MIN_SESSION_DURATION = timedelta(minutes=5)

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
active_sessions: dict[tuple[int, int], dict[str, Any]] = {}
commands_synced = False


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
        return {"generated_at": isoformat(utc_now()), "guilds": {}}

    with REPORT_PATH.open("r", encoding="utf-8") as report_file:
        report_text = report_file.read().strip()

    if not report_text:
        return {"generated_at": isoformat(utc_now()), "guilds": {}}

    report = json.loads(report_text)

    if not isinstance(report, dict) or not isinstance(report.get("guilds"), dict):
        raise ValueError(f"{REPORT_PATH} must contain a JSON object with a 'guilds' object")
    return report


report = load_report()


def save_report() -> None:
    report["generated_at"] = isoformat(utc_now())
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write("\n")
    temporary_path.replace(REPORT_PATH)


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
    user_report = (
        get_user_report(guild_report, member.id, channel.id)
        if guild_report
        else None
    )
    if not user_report or not user_report.get("sessions"):
        return False

    last_session = user_report["sessions"][-1]
    original_joined_at = parse_timestamp(last_session["joined_at"])
    left_at = parse_timestamp(last_session["left_at"])
    if (
        last_session["channel_id"] != channel.id
        or joined_at - left_at >= SESSION_MERGE_GAP
    ):
        return False

    user_report["total_seconds"] -= last_session["duration_seconds"]
    user_report["sessions"].pop()
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
        {"guild_name": session["guild_name"], "users": {}},
    )
    user_record = guild_report["users"].setdefault(
        str(session["user_id"]),
        {"user_name": session["user_name"], "total_seconds": 0, "sessions": []},
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
    save_report()


@bot.event
async def on_ready() -> None:
    global commands_synced
    if not commands_synced:
        if TRACKED_GUILD_ID is not None:
            test_guild = discord.Object(id=TRACKED_GUILD_ID)
            tree.copy_global_to(guild=test_guild)
            await tree.sync(guild=test_guild)
        else:
            await tree.sync()
        commands_synced = True
    for guild in bot.guilds:
        for voice_channel in guild.voice_channels:
            if not is_tracked_channel(voice_channel):
                continue
            for member in voice_channel.members:
                if (guild.id, member.id) not in active_sessions:
                    start_session(member, voice_channel)
    print(f"Logged in as {bot.user}. Tracking active voice sessions.")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
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

    embed = discord.Embed(
        title=(
            f"{target.display_name}'s C Timings"
            if MAIN_ID is not None and target.id == MAIN_ID
            else f"{target.display_name}'s VC Timings"
        ),
        color=discord.Color.blurple(),
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
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="leaderboard", description="Show this server's monthly VC leaderboard")
async def leaderboard(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    now = utc_now()
    month_start, next_month = month_bounds(now)
    guild_report = report["guilds"].get(str(interaction.guild.id))
    totals: dict[int, dict[str, Any]] = {}

    if guild_report:
        for user_id, user_record in guild_report.get("users", {}).items():
            member_id = int(user_id)
            reports = (
                owner_reports(guild_report, member_id, specific_only=False)
                if member_id == MAIN_ID
                else [user_record]
            )
            monthly_seconds = sum(
                session_seconds_in_range(session, month_start, min(next_month, now))
                for user_report in reports
                for session in user_report.get("sessions", [])
            )
            totals[member_id] = {
                "name": user_record.get("user_name", f"User {member_id}"),
                "seconds": monthly_seconds,
            }

    for (guild_id, member_id), session in active_sessions.items():
        if guild_id != interaction.guild.id:
            continue
        session_start = max(session["joined_at"], month_start)
        session_end = min(now, next_month)
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
    month_label = now.astimezone(IST).strftime("%B %Y")
    if ranked:
        medals = ("🥇", "🥈", "🥉")
        leaderboard_text = "\n".join(
            f"{medals[index] if index < 3 else f'**{index + 1}.**'} "
            f"{entry['name']} — {format_duration(entry['seconds'])}"
            for index, entry in enumerate(ranked[:10])
        )
    else:
        leaderboard_text = "No qualifying VC activity recorded this month."

    embed = discord.Embed(
        title=f"{interaction.guild.name} VC Leaderboard",
        description=f"Monthly voice activity — {month_label}",
        color=discord.Color.gold(),
        timestamp=now,
    )
    embed.add_field(name="Rankings", value=leaderboard_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="cprofile", description="Show the owner's specific channel profile")
async def cprofile(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    target = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    guild_report = report["guilds"].get(str(interaction.guild.id))
    reports = owner_reports(guild_report, target.id, specific_only=True) if guild_report else []
    user_report = reports[0] if reports else None
    active_session = active_sessions.get((interaction.guild.id, target.id))
    active_is_specific = (
        active_session is not None
        and owner_scope(target.id, active_session["channel_id"]) == "specific"
    )
    sessions = list(user_report.get("sessions", [])) if user_report else []
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
    embed = discord.Embed(
        title=f"{target.display_name}'s C Timings",
        color=discord.Color.blurple(),
        timestamp=utc_now(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Total time", value=format_duration(total_seconds), inline=True)
    embed.add_field(name="Longest session", value=format_duration(max_seconds), inline=True)
    embed.add_field(name="Times in VC", value=str(len(sessions) + int(active_is_specific)), inline=True)
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it.")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
