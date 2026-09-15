"""Admin command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="csv", description="Send the voice activity CSV report to the owner")
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
        [
            "guild_id",
            "guild_name",
            "user_id",
            "user_name",
            "channel_id",
            "channel_name",
            "joined_at",
            "left_at",
            "duration_seconds",
        ]
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
            embed=message_embed(
                "I could not DM the CSV report. Please enable your DMs and try again.",
                title="CSV delivery failed",
            )
        )
        return
    await interaction.response.send_message(
        embed=message_embed(
            "The CSV report was sent to your DMs.", title="CSV report sent"
        )
    )


@app_commands.command(name="history", description="Browse voice-channel history (owner only)")
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


@app_commands.command(
    name="current", description="Show non-bot users currently in voice channels"
)
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


class AdminCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('history', 'csv', 'current')


async def setup(bot):
    await bot.add_cog(AdminCog())
    for command in (history, csv_report, current):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
