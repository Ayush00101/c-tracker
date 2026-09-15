"""Stats command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="recap", description="Show a user's weekly voice recap")
@app_commands.describe(user="The member to recap")
async def recap(
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
                "No weekly activity recorded.", title="No weekly activity"
            )
        )
        return
    now = utc_now()
    start, end = week_bounds(now)
    total = live_period_seconds(guild_report, target.id, start, min(end, now))
    values = weekly_daily_seconds(guild_report, target.id, now)
    rank_index, rank = achievement_for_seconds(total)
    target_label = member_label(interaction.guild, target.id, target.display_name)
    embed = discord.Embed(
        title=f"{target_label}'s Weekly Recap",
        color=achievement_color(rank_index),
        timestamp=now,
    )
    embed.add_field(name="Total", value=format_duration(total), inline=True)
    embed.add_field(name="Rank", value=rank, inline=True)
    embed.add_field(
        name="Daily breakdown",
        value="\n".join(
            f"**{day:%a}** — {format_duration(seconds)}" for day, seconds in values
        ),
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Requested By {interaction.user.display_name} • "
            f"{user_fact(guild_report, target.id, total)}"
        )
    )
    await interaction.response.send_message(
        embed=embed, view=ProfileView(interaction.user.id, embed, guild_report, target)
    )


@app_commands.command(name="goal", description="Set a daily, weekly, or monthly voice goal")
@app_commands.describe(hours="Target hours", period="daily, weekly, or monthly")
@app_commands.choices(
    period=[
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Monthly", value="monthly"),
    ]
)
async def goal(
    interaction: discord.Interaction,
    hours: float,
    period: app_commands.Choice[str] | None = None,
) -> None:
    if interaction.guild is None or not 0 < hours <= 10000:
        await interaction.response.send_message(
            embed=message_embed(
                "Use this command in the tracked server with a positive goal.",
                title="Goal unavailable",
            )
        )
        return
    period_name = period.value if period else "daily"
    guild_report = report["guilds"].setdefault(
        str(interaction.guild.id),
        {"guild_id": interaction.guild.id, "users": {}, "goals": {}},
    )
    existing = (
        guild_report.setdefault("goals", {})
        .get(str(interaction.user.id), {})
        .get(period_name)
    )
    if existing:
        await interaction.response.send_message(
            embed=message_embed(
                f"You already have a {period_name} goal. Overwrite it?",
                title="Goal already exists",
            ),
            view=GoalOverwriteView(
                interaction.user.id, guild_report, period_name, hours
            ),
        )
        return
    guild_report["goals"].setdefault(str(interaction.user.id), {})[period_name] = {
        "hours": hours,
        "created_at": isoformat(utc_now()),
    }
    save_report()
    await interaction.response.send_message(
        embed=message_embed(
            f"🎯 {period_name.title()} goal set to **{hours:g} hour(s)**.",
            title="Goal set",
        )
    )


@app_commands.command(
    name="mygoal", description="Show your voice goal progress with boxed bars"
)
async def mygoal(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            "No voice activity recorded yet. Set a goal with `/goal`.", ephemeral=True
        )
        return
    member = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    goals = guild_report.get("goals", {}).get(str(member.id), {})
    if not goals:
        await interaction.response.send_message(
            embed=message_embed(
                "You have no goals yet. Use `/goal` to set one.", title="No goals"
            )
        )
        return
    now = utc_now()
    rank_index, _ = achievement_for_seconds(
        weekly_seconds_for_user(guild_report, member.id, now)
    )
    embed = discord.Embed(
        title=f"{member_label(interaction.guild, member.id, member.display_name)}'s Goals",
        color=achievement_color(rank_index),
        timestamp=now,
    )
    period_specs = (
        ("daily", "Daily", day_bounds),
        ("weekly", "Weekly", week_bounds),
        ("monthly", "Monthly", month_bounds),
    )
    for key, label, bounds in period_specs:
        goal = goals.get(key)
        if not isinstance(goal, dict) or not goal.get("hours"):
            continue
        target_seconds = int(float(goal["hours"]) * 3600)
        range_start, range_end = bounds(now)
        current = live_period_seconds(
            guild_report, member.id, range_start, min(range_end, now)
        )
        boxes = progress_bar(current, target_seconds)
        embed.add_field(
            name=label,
            value=(
                f"{boxes}\n"
                f"{format_hours_minutes(current)} / {format_hours_minutes(target_seconds)}"
            ),
            inline=False,
        )
        if key == "weekly":
            values = weekly_daily_seconds(guild_report, member.id, now)
            max_seconds = max(
                target_seconds, max((seconds for _, seconds in values), default=0)
            )
            lines = []
            for day, seconds in values:
                bar_length = int(seconds / max_seconds * 12) if max_seconds else 0
                bar = "▰" * bar_length or "—"
                lines.append(
                    f"**{day.strftime('%a %d %b')}**  {bar} {format_hours_minutes(seconds)}"
                )
            embed.add_field(
                name="This week",
                value="\n".join(lines),
                inline=False,
            )
    if not embed.fields:
        await interaction.response.send_message(
            "You have no goals yet. Use `/goal` to set one.", ephemeral=True
        )
        return
    embed.set_footer(
        text=(
            f"Requested By {interaction.user.display_name} • "
            f"{user_fact(guild_report, member.id, live_total_seconds(guild_report, member.id))}"
        )
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(
    name="timecapsule", description="Review or compare historical weekly activity"
)
@app_commands.describe(
    week="YYYY-MM-DD week date", compare_week="Optional second YYYY-MM-DD week date"
)
async def timecapsule(
    interaction: discord.Interaction,
    week: str | None = None,
    compare_week: str | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            embed=message_embed(
                "No historical activity recorded.", title="No historical activity"
            )
        )
        return
    try:
        start, end, label = parse_week_input(week, utc_now())
        comparison = None
        if compare_week:
            cstart, cend, clabel = parse_week_input(compare_week, utc_now())
            comparison = timecapsule_snapshot(guild_report, cstart, cend)
    except ValueError as error:
        await interaction.response.send_message(
            embed=message_embed(str(error), title="Invalid time capsule")
        )
        return
    snapshot = timecapsule_snapshot(guild_report, start, end)
    users = sorted(snapshot["users"].items(), key=lambda item: item[1], reverse=True)[
        :10
    ]
    channels = sorted(
        snapshot["channels"].items(), key=lambda item: item[1], reverse=True
    )[:10]

    def lines(items: list[tuple[int, int]], key: str) -> str:
        return (
            "\n".join(
                f"**{(interaction.guild.get_member(item_id).display_name if key == 'users' and interaction.guild.get_member(item_id) else interaction.guild.get_channel(item_id).name if key == 'channels' and interaction.guild.get_channel(item_id) else item_id)}** — {format_duration(seconds)}"
                + (
                    f" ({delta_text(seconds - comparison.get(key, {}).get(item_id, 0))})"
                    if comparison
                    else ""
                )
                for item_id, seconds in items
            )
            or "No activity recorded."
        )

    embed = discord.Embed(
        title="⏳ Time Capsule",
        description=label,
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    embed.add_field(name="Users", value=lines(users, "users"), inline=False)
    embed.add_field(name="Channels", value=lines(channels, "channels"), inline=False)
    if comparison:
        embed.set_footer(
            text=(
                f"Compared with {compare_week} • Deltas use + / - • "
                f"Requested By {interaction.user.display_name}"
            )
        )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="leaderboard", description="Show this server's VC leaderboard")
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


@app_commands.command(
    name="channelstats", description="Show detailed statistics for a voice channel"
)
@app_commands.describe(
    channel="Optional voice channel; defaults to the busiest channel"
)
async def channelstats(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            "No voice-channel activity recorded.", ephemeral=True
        )
        return
    stats = channel_statistics(interaction.guild.id)
    selected = channel
    if selected is None and stats:
        selected = interaction.guild.get_channel(stats[0].get("channel_id", 0))
    if selected is None:
        await interaction.response.send_message(
            "No voice channel with recorded activity was found.", ephemeral=True
        )
        return
    detail = channel_detail_statistics(guild_report, selected.id)
    if detail["session_count"] == 0:
        await interaction.response.send_message(
            f"No activity recorded for **{selected.name}**.", ephemeral=True
        )
        return
    top_members = sorted(
        detail["member_seconds"].items(), key=lambda item: item[1], reverse=True
    )[:5]
    member_lines = "\n".join(
        f"**{index}.** "
        f"{(interaction.guild.get_member(member_id).display_name if interaction.guild.get_member(member_id) else member_id)}"
        f" — {format_duration(seconds)}"
        for index, (member_id, seconds) in enumerate(top_members, start=1)
    )
    busiest_hour = max(
        enumerate(detail["hour_seconds"]), key=lambda item: item[1], default=(0, 0)
    )
    average = detail["total_seconds"] // detail["session_count"]
    embed = discord.Embed(
        title=f"📊 #{selected.name} Channel Stats",
        description="Detailed voice activity for this channel.",
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    embed.add_field(
        name="Total usage",
        value=format_hours_minutes(detail["total_seconds"]),
        inline=True,
    )
    embed.add_field(name="Sessions", value=str(detail["session_count"]), inline=True)
    embed.add_field(name="Average session", value=format_duration(average), inline=True)
    embed.add_field(
        name="Busiest hour (IST)",
        value=f"{busiest_hour[0]:02d}:00–{(busiest_hour[0] + 1) % 24:02d}:00",
        inline=True,
    )
    embed.add_field(name="Most active members", value=member_lines, inline=False)
    embed.set_footer(
        text=f"Requested By {interaction.user.display_name} • Hour graph uses IST"
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="heatmap", description="Show this week's VC activity heatmap")
async def heatmap(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            embed=message_embed(
                "No voice activity recorded for this server.", title="No voice activity"
            )
        )
        return
    now = utc_now()
    values = weekly_heatmap_values(guild_report, now)
    peak = max((seconds for row in values for seconds in row), default=0)
    shades = ("▫️", "▪️", "◽", "◾", "⬛")
    lines = []
    for index, row in enumerate(values):
        cells = "".join(
            shades[min(4, int(seconds / peak * 4) if peak else 0)] for seconds in row
        )
        total = sum(row)
        day = (
            now.astimezone(IST)
            - timedelta(days=(now.astimezone(IST).weekday() + 1) % 7)
        ).date() + timedelta(days=index)
        lines.append(f"**{day:%a}** {cells}  `{format_hours_minutes(total)}`")
    embed = discord.Embed(
        title=f"🔥 {interaction.guild.name} VC Heatmap",
        description=(
            "Current IST week • each cell represents a two-hour block\n"
            "`00 02 04 06 08 10 12 14 16 18 20 22`\n\n" + "\n".join(lines)
        ),
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=now,
    )
    embed.set_footer(
        text=f"Requested By {interaction.user.display_name} • Darker = more VC time"
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="vcstats", description="Show the voice-channel leaderboard")
async def vcstats(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    stats = channel_statistics(interaction.guild.id)
    rankings = (
        "\n".join(
            f"**{index + 1}.** {entry['name']} — {format_duration(entry['seconds'])}"
            for index, entry in enumerate(stats[:10])
        )
        or "No voice-channel activity recorded."
    )
    embed = discord.Embed(
        title=f"{interaction.guild.name} VC Channel Leaderboard",
        description=rankings,
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


class StatsCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('recap', 'goal', 'mygoal', 'timecapsule', 'leaderboard', 'channelstats', 'heatmap', 'vcstats')


async def setup(bot):
    await bot.add_cog(StatsCog())
    for command in (recap, goal, mygoal, timecapsule, leaderboard, channelstats, heatmap, vcstats):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
