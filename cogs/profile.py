"""Profile command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(
    name="profile", description="Show a user's voice channel activity profile"
)
@app_commands.describe(user="The server member whose voice profile you want to view")
async def profile(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
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

    total_seconds = sum(
        int(user_report.get("total_seconds", 0)) for user_report in reports
    )
    max_seconds = max(
        (int(session["duration_seconds"]) for session in sessions),
        default=0,
    )
    if active_session:
        current_duration = int(
            (utc_now() - active_session["joined_at"]).total_seconds()
        )
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

    recent_text = (
        "\n".join(
            (
                f"{'🟢' if session.get('active') else '•'} "
                f"{format_local(parse_timestamp(session['joined_at']))}, "
                f"{format_date(parse_timestamp(session['joined_at']))} — "
                f"{'In progress' if session.get('active') else format_duration(int(session['duration_seconds']))}"
            )
            for session in reversed(recent_sessions)
        )
        or "No sessions recorded"
    )

    daily_streak = (
        daily_streak_for_user(guild_report, target.id, utc_now()) if guild_report else 0
    )
    weekly_seconds = (
        weekly_seconds_for_user(guild_report, target.id, utc_now())
        if guild_report
        else 0
    )
    weekly_rank_index, weekly_rank = achievement_for_seconds(weekly_seconds)
    target_label = member_label(interaction.guild, target.id, target.display_name)
    embed = discord.Embed(
        title=(
            f"{target_label} • {weekly_rank}'s C Timings"
            if MAIN_ID is not None and target.id == MAIN_ID
            else f"{target_label} • {weekly_rank}'s VC Timings"
        ),
        color=achievement_color(weekly_rank_index),
        timestamp=utc_now(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(
        name="Total time", value=format_duration(total_seconds), inline=True
    )
    embed.add_field(
        name="Longest session", value=format_duration(max_seconds), inline=True
    )
    embed.add_field(
        name="Times in VC",
        value=str(len(sessions) + int(bool(active_session))),
        inline=True,
    )
    embed.add_field(name="Daily streak", value=f"🔥 {daily_streak} day(s)", inline=True)
    embed.add_field(
        name="Quests completed",
        value=str(
            guild_report.get("users", {})
            .get(str(target.id), {})
            .get("quests_completed", 0)
        ),
        inline=True,
    )
    embed.add_field(
        name="Current weekly rank",
        value=weekly_rank,
        inline=True,
    )
    roulette_effect = roulette_effect_for(interaction.guild.id, target.id)
    if roulette_effect:
        embed.add_field(
            name="🎰 Temporary roulette effect",
            value=(
                f"**{roulette_effect['role']}**\n"
                f"{roulette_effect['stat']}\n"
                f"Expires <t:{int(roulette_effect['expires_at'].timestamp())}:R>"
            ),
            inline=False,
        )
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    footer = f"Requested By {interaction.user.display_name}"
    if guild_report:
        footer += f" • {user_fact(guild_report, target.id, total_seconds)}"
    embed.set_footer(text=footer)
    await interaction.response.send_message(
        embed=embed,
        view=ProfileView(
            interaction.user.id,
            embed,
            guild_report or {"guild_id": interaction.guild.id, "users": {}},
            target,
        ),
    )


@app_commands.command(name="achievements", description="Show a user's voice achievements")
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

    target_label = member_label(interaction.guild, target.id, target.display_name)
    embed = discord.Embed(
        title=f"{target_label}'s Achievements",
        description="Voice milestones and current period records",
        color=achievement_color(weekly_rank_index),
        timestamp=now,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(
        name="Weekly highest",
        value=weekly_rank,
        inline=True,
    )
    embed.add_field(
        name="Monthly highest",
        value=format_hours_minutes(monthly_seconds),
        inline=True,
    )
    embed.add_field(
        name="Daily",
        value=f"{format_hours_minutes(daily_seconds)}\n🔥 {daily_streak} day streak",
        inline=True,
    )
    if milestones:
        for milestone in milestones:
            achieved_at = parse_timestamp(milestone["achieved_at"])
            embed.add_field(
                name=f"{milestone['badge']} {milestone['hours']} hour milestone",
                value=(
                    f"Achieved: {format_local(achieved_at)}, {format_date(achieved_at)}"
                ),
                inline=False,
            )
    else:
        embed.add_field(
            name="Milestones",
            value="No 50-hour milestones reached yet.",
            inline=False,
        )
    embed.set_footer(
        text=(
            f"Requested By {interaction.user.display_name} • "
            f"{user_fact(guild_report, target.id, user_total_seconds(guild_report, target.id))}"
        )
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="progress", description="Show progress toward your next ranks")
@app_commands.describe(user="The member whose progress you want to view")
async def progress(
    interaction: discord.Interaction, user: discord.Member | None = None
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
        )
        return
    target = user or interaction.user
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            embed=message_embed(
                "No voice activity recorded.", title="No voice activity"
            )
        )
        return
    now = utc_now()
    weekly = weekly_seconds_for_user(guild_report, target.id, now)
    rank_index, rank = achievement_for_seconds(weekly)
    _, upper, _ = ACHIEVEMENT_RANKS[rank_index]
    total = live_total_seconds(guild_report, target.id)
    milestone = next((hours for hours in MILESTONE_HOURS if total < hours * 3600), None)
    weekly_value = f"**{rank}**"
    if upper is not None:
        weekly_value += f"\n{progress_bar(weekly, int(upper * 3600))}\n{format_duration(max(0, int(upper * 3600 - weekly)))} until **{ACHIEVEMENT_RANKS[rank_index + 1][2]}**"
    overall_value = f"{format_hours_minutes(total)} total"
    if milestone is not None:
        overall_value += f"\n{progress_bar(total, milestone * 3600)}\n{format_duration(max(0, milestone * 3600 - total))} until {milestone}-hour milestone"
    target_label = member_label(interaction.guild, target.id, target.display_name)
    embed = discord.Embed(
        title=f"{target_label}'s Progress",
        color=achievement_color(rank_index),
        timestamp=now,
    )
    embed.add_field(name="Weekly rank", value=weekly_value, inline=False)
    embed.add_field(name="Overall hours", value=overall_value, inline=False)
    embed.set_footer(
        text=(
            f"Requested By {interaction.user.display_name} • "
            f"{user_fact(guild_report, target.id, total)}"
        )
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="timeline", description="View your unlocked VC journey timeline")
async def timeline(interaction: discord.Interaction) -> None:
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
    total_seconds = live_total_seconds(guild_report, target.id) if guild_report else 0
    unlock_seconds = 50 * 3600
    if total_seconds < unlock_seconds:
        remaining = unlock_seconds - total_seconds
        message = (
            "🔒 Your VC timeline is still sealed. "
            f"Spend **{format_hours_minutes(remaining)}** more in VC to unlock it."
        )
        if target.id == MAIN_ID:
            message = (
                "🔒 Even the server owner cannot skip the lore lock. "
                f"You need **{format_hours_minutes(remaining)}** more in VC "
                "before `/timeline` unlocks."
            )
        await interaction.response.send_message(
            embed=message_embed(message, title="Timeline locked")
        )
        return

    sessions = [
        session
        for user_report in iter_user_reports(guild_report, target.id)
        for session in user_report.get("sessions", [])
    ]
    sessions.sort(key=lambda session: parse_timestamp(session["joined_at"]))
    milestones = ensure_user_milestones(guild_report, target.id, utc_now())
    events: list[tuple[datetime, str]] = []
    if sessions:
        first_session = sessions[0]
        first_join = parse_timestamp(first_session["joined_at"])
        events.append(
            (
                first_join,
                f"🌱 **The journey begins** — joined {first_session.get('channel_name', 'VC')}",
            )
        )
    for milestone in milestones:
        achieved_at = milestone.get("achieved_at")
        if not achieved_at:
            continue
        events.append(
            (
                parse_timestamp(achieved_at),
                f"{milestone.get('badge', '🏆')} **{milestone['hours']}-hour milestone** unlocked",
            )
        )
    for session in sessions[-5:]:
        joined_at = parse_timestamp(session["joined_at"])
        events.append(
            (
                joined_at,
                f"🎧 Entered **{session.get('channel_name', 'VC')}** "
                f"for {format_hours_minutes(int(session.get('duration_seconds', 0)))}",
            )
        )
    events.sort(key=lambda item: item[0])
    timeline_lines = [
        f"`{event_time.astimezone(IST):%d %b %Y}`  {description}"
        for event_time, description in events[-10:]
    ]
    weekly_seconds = weekly_seconds_for_user(guild_report, target.id, utc_now())
    rank_index, rank = achievement_for_seconds(weekly_seconds)
    owner_flavor = ""
    if target.id == MAIN_ID:
        owner_flavor = (
            "\n\n👑 **Owner's note:** The timeline has accepted your quest log. "
            "Please try not to turn the next chapter into another all-nighter."
        )
    embed = discord.Embed(
        title=f"{member_label(interaction.guild, target.id, target.display_name)}'s VC Timeline",
        description=(
            f"**50-hour unlock achieved.**\n"
            f"Lifetime VC time: **{format_hours_minutes(total_seconds)}**\n"
            f"Current weekly rank: **{rank}**\n\n"
            + ("\n".join(timeline_lines) or "No timeline events recorded yet.")
            + owner_flavor
        ),
        color=achievement_color(rank_index),
        timestamp=utc_now(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="cprofile", description="Show the owner's specific channel profile")
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
            guild_report.get("users", {}).get(str(target.id)) if guild_report else None
        )
        sessions = [
            session
            for session in (user_record or {}).get("sessions", [])
            if TRACKED_VOICE_CHANNEL_ID is None
            or int(session.get("channel_id", 0)) == TRACKED_VOICE_CHANNEL_ID
        ]
    active_session = active_sessions.get((interaction.guild.id, target.id))
    active_is_specific = active_session is not None and (
        TRACKED_VOICE_CHANNEL_ID is None
        or active_session["channel_id"] == TRACKED_VOICE_CHANNEL_ID
    )
    total_seconds = int(user_report.get("total_seconds", 0)) if user_report else 0
    if active_session and active_is_specific:
        current_duration = int(
            (utc_now() - active_session["joined_at"]).total_seconds()
        )
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
    recent_text = (
        "\n".join(
            f"{'🟢' if session.get('active') else '•'} "
            f"{format_local(parse_timestamp(session['joined_at']))}, "
            f"{format_date(parse_timestamp(session['joined_at']))} — "
            f"{'In progress' if session.get('active') else format_duration(int(session['duration_seconds']))}"
            for session in reversed(recent_sessions)
        )
        or "No sessions recorded"
    )
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
    embed.add_field(
        name="Total time", value=format_duration(total_seconds), inline=True
    )
    embed.add_field(
        name="Longest session", value=format_duration(max_seconds), inline=True
    )
    embed.add_field(
        name="Times in VC",
        value=str(len(sessions) + int(active_is_specific)),
        inline=True,
    )
    embed.add_field(
        name="Current weekly rank",
        value=cprofile_rank,
        inline=True,
    )
    embed.add_field(name="Last session", value=last_session_text, inline=False)
    embed.add_field(name="Last 5 sessions", value=recent_text, inline=False)
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="add", description="Allow a user to use the owner's cprofile")
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
    await interaction.response.send_message(
        embed=message_embed(message, title="Access updated")
    )


class ProfileCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('profile', 'achievements', 'progress', 'timeline', 'cprofile', 'add')


async def setup(bot):
    await bot.add_cog(ProfileCog())
    for command in (profile, achievements, progress, timeline, cprofile, add_cprofile_user):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
