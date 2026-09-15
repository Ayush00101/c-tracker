"""Boss command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="powerups", description="View your boss battle powerups")
async def powerups(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
        )
        return
    record = (
        report["guilds"]
        .get(str(interaction.guild.id), {})
        .get("users", {})
        .get(str(interaction.user.id), {})
    )
    inventory = record.get("powerups", [])
    if not inventory:
        await interaction.response.send_message(
            embed=message_embed(
                "You have no powerups. Complete `/quest` to earn one.",
                title="No powerups",
            )
        )
        return
    counts = {key: inventory.count(key) for key in dict.fromkeys(inventory)}
    description = "\n".join(
        f"**{POWERUPS[key]['name']}** ×{count} — {POWERUPS[key]['description']}"
        for key, count in counts.items()
        if key in POWERUPS
    )
    embed = discord.Embed(
        title=f"{member_label(interaction.guild, interaction.user.id, interaction.user.display_name)}'s Powerups",
        description=description,
        color=rank_color_for_member(interaction.guild.id, interaction.user.id),
        timestamp=utc_now(),
    )
    embed.set_footer(
        text=(
            "Powerups can only be used during an active /bossbattle. • "
            f"Requested By {interaction.user.display_name}"
        )
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="bossbattle", description="Summon a 24-hour VC boss battle")
@app_commands.describe(powerup="Optional powerup to use during the active boss battle")
@app_commands.choices(
    powerup=[
        app_commands.Choice(name=data["name"], value=key)
        for key, data in POWERUPS.items()
    ]
)
async def bossbattle(
    interaction: discord.Interaction,
    powerup: app_commands.Choice[str] | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return
    guild_report = report["guilds"].setdefault(
        str(interaction.guild.id),
        {
            "guild_id": interaction.guild.id,
            "guild_name": interaction.guild.name,
            "users": {},
        },
    )
    existing = guild_report.get("boss_battle")
    if existing and existing.get("status") == "active":
        if powerup is not None:
            inventory = (
                guild_report.get("users", {})
                .get(str(interaction.user.id), {})
                .get("powerups", [])
            )
            if powerup.value not in inventory:
                await interaction.response.send_message(
                    "You do not own that powerup. Complete `/quest` first.",
                    ephemeral=True,
                )
                return
            inventory.remove(powerup.value)
            effect = activate_boss_powerup(
                interaction.guild,
                existing,
                interaction.user.id,
                powerup.value,
            )
            existing.setdefault("effects", []).append(effect)
            save_report()
            view = BossStatusView(interaction.user.id, interaction.guild, existing)
            status_embed = boss_status_embed(
                interaction.guild,
                existing,
                requested_by=interaction.user.display_name,
            )
            status_embed.add_field(
                name="Potion activated",
                value=f"🧪 **{POWERUPS[powerup.value]['name']}** used.\n{effect['activation_message']}",
                inline=False,
            )
            await interaction.response.send_message(
                embed=status_embed,
                view=view,
            )
            return
        view = BossStatusView(interaction.user.id, interaction.guild, existing)
        await interaction.response.send_message(
            embed=boss_status_embed(
                interaction.guild,
                existing,
                requested_by=interaction.user.display_name,
            ),
            view=view,
        )
        return

    members = [
        member
        for voice_channel in interaction.guild.voice_channels
        if is_tracked_channel(voice_channel)
        for member in voice_channel.members
        if not member.bot
    ]
    if not members:
        await interaction.response.send_message(
            "The boss needs players to summon it. No non-bot users are currently in a tracked VC.",
            ephemeral=True,
        )
        return
    now = utc_now()
    participant_ids = [member.id for member in members]
    participants = {str(member.id): {"name": member.display_name} for member in members}
    hp_points = boss_weekly_target_points(guild_report, participant_ids, now)
    initial_powerup_key: str | None = None
    if powerup is not None:
        inventory = (
            guild_report.get("users", {})
            .get(str(interaction.user.id), {})
            .get("powerups", [])
        )
        if powerup.value not in inventory:
            await interaction.response.send_message(
                "You do not own that powerup. Complete `/quest` first.",
                ephemeral=True,
            )
            return
        inventory.remove(powerup.value)
        initial_powerup_key = powerup.value
    boss = {
        "status": "active",
        "name": boss_name(now, len(members)),
        "guild_id": interaction.guild.id,
        "started_at": isoformat(now),
        "expires_at": isoformat(now + timedelta(hours=24)),
        "hp_points": hp_points,
        "participants": participants,
        "summoned_member_ids": participant_ids,
        "alerts": [],
        "effects": [],
        "damage_snapshot": {},
        "last_damage_update": "",
    }
    if initial_powerup_key:
        boss["effects"].append(
            activate_boss_powerup(
                interaction.guild,
                boss,
                interaction.user.id,
                initial_powerup_key,
            )
        )
    guild_report["boss_battle"] = boss
    save_report()
    names = ", ".join(
        member_label(interaction.guild, member.id, member.display_name)
        for member in members
    )
    embed = discord.Embed(
        title=f"👹 BOSS SUMMONED: {boss['name']}",
        description=(
            f"{names}\n\n"
            f"**Required party damage:** {hp_points:,} HP of combined VC damage\n"
            f"**Deadline:** <t:{int(parse_timestamp(boss['expires_at']).timestamp())}:F>\n\n"
            "Every player's time in VC damages the boss. The battle reports every 10%."
        ),
        color=discord.Color.dark_red(),
        timestamp=now,
    )
    embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    monitor_task = asyncio.create_task(monitor_boss(interaction.guild.id))
    boss_monitor_tasks[interaction.guild.id] = monitor_task


@app_commands.command(
    name="endboss", description="Owner-only: permanently end the active boss battle"
)
async def endboss(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.",
                title="Server only",
            )
        )
        return
    if not owner_only(interaction):
        await interaction.response.send_message(
            embed=message_embed(
                "Only the bot owner can permanently end a boss battle.",
                title="Owner only",
            ),
            ephemeral=True,
        )
        return
    guild_report = report["guilds"].get(str(interaction.guild.id))
    boss = guild_report.get("boss_battle") if guild_report else None
    if not boss or boss.get("status") != "active":
        await interaction.response.send_message(
            embed=message_embed(
                "There is no active boss battle to end.",
                title="No active boss battle",
            )
        )
        return
    confirmation = discord.Embed(
        title="⚠️ End boss battle?",
        description=(
            f"Are you sure you want to permanently end **{boss['name']}**?\n\n"
            "This stops damage tracking, alerts, powerup use, and the 24-hour "
            "battle. This action cannot be undone."
        ),
        color=discord.Color.orange(),
        timestamp=utc_now(),
    )
    confirmation.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(
        embed=confirmation,
        view=BossEndView(
            interaction.user.id,
            guild_report,
            boss,
            interaction.channel,
        ),
        ephemeral=True,
    )


class BossCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('powerups', 'bossbattle', 'endboss')


async def setup(bot):
    await bot.add_cog(BossCog())
    for command in (powerups, bossbattle, endboss):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)
