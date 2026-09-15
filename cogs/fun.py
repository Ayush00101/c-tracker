"""Fun command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="roulette", description="Spin the 150-outcome temporary VC roulette")
async def roulette(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    member = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    existing = roulette_effect_for(interaction.guild.id, member.id)
    if existing:
        await interaction.response.send_message(
            embed=message_embed(
                f"🎰 You already have **{existing['role']}** active. "
                f"It expires <t:{int(existing['expires_at'].timestamp())}:R>.",
                title="Roulette effect already active",
            )
        )
        return
    outcome = random.choice(ROULETTE_OUTCOMES)
    expires_at = utc_now() + timedelta(hours=2)
    key = (interaction.guild.id, member.id)
    active_roulette_effects[key] = {
        **outcome,
        "started_at": utc_now(),
        "expires_at": expires_at,
    }
    asyncio.create_task(expire_roulette_effect(key, expires_at))
    embed = discord.Embed(
        title="🎰 VC Roulette",
        description=(
            f"**{member.display_name}** spun the wheel and landed on:\n\n"
            f"## {outcome['name']}\n"
            f"{outcome['description']}"
        ),
        color=rank_color_for_member(interaction.guild.id, member.id),
        timestamp=utc_now(),
    )
    embed.add_field(name="Temporary role", value=f"**{outcome['role']}**", inline=True)
    embed.add_field(name="Temporary stat change", value=outcome["stat"], inline=True)
    embed.set_footer(
        text=(
            f"This effect lasts 2 hours and then returns to normal. • "
            f"Requested By {interaction.user.display_name}"
        )
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(
    name="lobotomykaisen",
    description="Browse the ranked voice-tracker meme gallery",
)
@app_commands.describe(user="The member whose rank should appear in the gallery title")
async def lobotomykaisen(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
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
    guild_report = report["guilds"].get(str(interaction.guild.id))
    weekly_seconds = (
        weekly_seconds_for_user(guild_report, target.id, utc_now())
        if guild_report
        else 0
    )
    rank_index, rank = achievement_for_seconds(weekly_seconds)
    try:
        image_path = meme_path(1)
    except FileNotFoundError as error:
        await interaction.response.send_message(
            embed=message_embed(str(error), title="Meme gallery unavailable")
        )
        return
    file = discord.File(image_path, filename="meme-1.jpg")
    view = MemeView(interaction.user.id, target, rank, rank_index)
    await interaction.response.send_message(
        embed=meme_embed(target, rank, rank_index, 1, interaction.user.display_name),
        file=file,
        view=view,
    )


@app_commands.command(
    name="museum", description="Display the server's historical voice records"
)
async def museum(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    if not guild_report:
        await interaction.response.send_message(
            "No historical voice records are available yet.", ephemeral=True
        )
        return

    records = museum_records(guild_report)
    embed = discord.Embed(
        title=f"{interaction.guild.name} Voice Museum",
        description="A hall of the server's most historic VC records.",
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    longest = records["longest_session"]
    if longest:
        member_id, session = longest
        member = interaction.guild.get_member(member_id)
        embed.add_field(
            name="🏛️ Longest single session",
            value=(
                f"{member.display_name if member else session.get('user_name', member_id)} — "
                f"{format_duration(int(session['duration_seconds']))}\n"
                f"{session.get('channel_name', 'Unknown VC')}"
            ),
            inline=False,
        )
    top_user = records["top_user"]
    if top_user:
        member = interaction.guild.get_member(top_user[0])
        embed.add_field(
            name="👑 Most total VC time",
            value=f"{member.display_name if member else top_user[0]} — {format_duration(top_user[1])}",
            inline=True,
        )
    top_channel = records["top_channel"]
    if top_channel:
        channel = interaction.guild.get_channel(top_channel[0])
        embed.add_field(
            name="🔥 Busiest voice channel",
            value=f"{channel.name if channel else top_channel[0]} — {format_duration(top_channel[1])}",
            inline=True,
        )
    peak_day = records["peak_day"]
    if peak_day:
        embed.add_field(
            name="📅 Peak activity day",
            value=f"{peak_day[0].strftime('%d %b %Y')} — {format_duration(peak_day[1])}",
            inline=True,
        )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


class FunCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('roulette', 'lobotomykaisen', 'museum')


async def setup(bot):
    await bot.add_cog(FunCog())
    for command in (roulette, lobotomykaisen, museum):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
