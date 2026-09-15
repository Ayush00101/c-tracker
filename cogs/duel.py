"""Duel command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="duel", description="Challenge another member to a weekly VC duel")
@app_commands.describe(
    user="The member you want to duel",
    phase="Optional phase to use for every daily round",
)
@app_commands.choices(
    phase=[
        app_commands.Choice(name="All phases", value="all"),
        app_commands.Choice(name="Morning (8 AM–2 PM)", value="i"),
        app_commands.Choice(name="Evening (2 PM–8 PM)", value="ii"),
        app_commands.Choice(name="Night (8 PM–2 AM)", value="iii"),
        app_commands.Choice(name="Midnight (2 AM–8 AM)", value="iv"),
    ]
)
async def duel(
    interaction: discord.Interaction,
    user: discord.Member,
    phase: app_commands.Choice[str] | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    challenger = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    if challenger is None:
        await interaction.response.send_message(
            "I could not find your member profile in this server.", ephemeral=True
        )
        return
    if user.bot:
        await interaction.response.send_message(
            embed=message_embed("You cannot duel a bot.", title="Duel unavailable")
        )
        return
    if user.id == challenger.id:
        await interaction.response.send_message(
            embed=message_embed("You cannot duel yourself.", title="Duel unavailable")
        )
        return
    if challenger.id in active_duel_users or user.id in active_duel_users:
        await interaction.response.send_message(
            embed=message_embed(
                "One of you is already in a duel. Wait for that match to finish.",
                title="Duel unavailable",
            )
        )
        return

    color = rank_color_for_member(interaction.guild.id, challenger.id)
    selected_phase = None if phase is None or phase.value == "all" else phase.value
    phase_text = phase.name if phase is not None else "All phases"
    intro = discord.Embed(
        title="⚔️ VC Duel",
        description=(
            f"**{challenger.display_name}** is matching **{user.display_name}**!\n"
            f"Battle format: **{phase_text}**"
        ),
        color=color,
        timestamp=utc_now(),
    )
    intro.set_footer(text=f"Requested By {interaction.user.display_name}")
    active_duel_users.add(challenger.id)
    active_duel_users.add(user.id)
    try:
        await interaction.response.send_message(embed=intro)
        message = await interaction.original_response()
        for remaining in range(5, 0, -1):
            countdown = discord.Embed(
                title="⚔️ VC Duel",
                description=(
                    f"**{challenger.display_name}** is matching **{user.display_name}**!\n\n"
                    f"Battle format: **{phase_text}**\n"
                    f"Match begins in **{remaining}**"
                ),
                color=color,
                timestamp=utc_now(),
            )
            countdown.set_footer(text=f"Requested By {interaction.user.display_name}")
            await edit_message_safe(message, embed=countdown)
            await asyncio.sleep(1)
        view = DuelWeekView(challenger.id, user.id)
        choice_embed = discord.Embed(
            title="⚔️ VC Duel",
            description=(
                f"**{challenger.display_name}** is matching **{user.display_name}**!\n\n"
                f"Battle format: **{phase_text}**\n"
                "Choose your arena:\n**Past** = last week\n**Present** = this week"
            ),
            color=color,
            timestamp=utc_now(),
        )
        choice_embed.set_footer(text=f"Requested By {interaction.user.display_name}")
        await edit_message_safe(message, embed=choice_embed, view=view)
        try:
            await asyncio.wait_for(view.finished.wait(), timeout=46)
        except asyncio.TimeoutError:
            view.finished.set()
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        week_key, announcement = resolve_duel_week(view.choices, challenger.id, user.id)
        lock_embed = discord.Embed(
            title="⚔️ VC Duel",
            description=(
                f"**{challenger.display_name}** is matching **{user.display_name}**!\n\n"
                f"Battle format: **{phase_text}**\n"
                f"{announcement}"
            ),
            color=color,
            timestamp=utc_now(),
        )
        lock_embed.set_footer(text=f"Requested By {interaction.user.display_name}")
        await edit_message_safe(message, embed=lock_embed, view=view)
        await asyncio.sleep(2)
        guild_report = report["guilds"].get(str(interaction.guild.id))
        result = await run_vc_duel(
            message,
            guild_report,
            challenger,
            user,
            week_key,
            utc_now(),
            selected_phase,
        )
        record_duel(duel_store, result, isoformat(utc_now()))
    finally:
        active_duel_users.discard(challenger.id)
        active_duel_users.discard(user.id)


@app_commands.command(name="lastduel", description="Show a member's last VC duel")
@app_commands.describe(user="The member whose last duel you want to view")
async def lastduel(
    interaction: discord.Interaction, user: discord.Member | None = None
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    target = user or (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    duel = last_duel_for_user(duel_store, target.id)
    if duel is None:
        await interaction.response.send_message(
            f"{target.display_name} has no recorded duels yet.", ephemeral=True
        )
        return
    week_title = "Past" if duel.get("week") == "past" else "Present"
    phase_title = {
        "i": "Morning",
        "ii": "Evening",
        "iii": "Night",
        "iv": "Midnight",
        "all": "All phases",
    }.get(str(duel.get("phase", "all")), "All phases")
    embed = discord.Embed(
        title=f"{target.display_name}'s Last Duel",
        description=(
            f"**{duel.get('challenger_name')}** vs **{duel.get('challenged_name')}**\n"
            f"Arena: **{week_title}** ({duel.get('week_label', 'Unknown week')})\n"
            f"Phase format: **{phase_title}**\n"
            f"**Winner:** {duel.get('winner_name', 'Draw')}\n"
            f"Final score: **{duel.get('challenger_rounds', 0)}/"
            f"{duel.get('challenged_rounds', 0)}**"
        ),
        color=rank_color_for_member(interaction.guild.id, target.id),
        timestamp=parse_timestamp(duel["played_at"])
        if duel.get("played_at")
        else utc_now(),
    )
    for index, round_data in enumerate(duel.get("rounds", []), start=1):
        embed.add_field(
            name=f"Round {index} — {round_data.get('day_label', 'Day')}",
            value=(
                f"{duel.get('challenger_name')}: "
                f"{format_hours_minutes(int(round_data.get('challenger_seconds', 0)))}\n"
                f"{duel.get('challenged_name')}: "
                f"{format_hours_minutes(int(round_data.get('challenged_seconds', 0)))}\n"
                f"Winner: {round_data.get('winner_name', 'Draw')}"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


class DuelCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('duel', 'lastduel')


async def setup(bot):
    await bot.add_cog(DuelCog())
    for command in (duel, lastduel):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
