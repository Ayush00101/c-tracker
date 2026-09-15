"""Quest command cog.

Command callbacks live here; shared persistence and tracking helpers remain in
``services.runtime`` and are imported as the runtime dependency.
"""

import discord
from discord import app_commands
from services.runtime import *  # noqa: F401,F403 - injected shared runtime API


@app_commands.command(name="quest", description="Receive one random daily VC quest")
@app_commands.describe(answer="Only needed if today's quest asks for the Wordle answer")
async def quest(interaction: discord.Interaction, answer: str | None = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=message_embed(
                "This command can only be used inside a server.", title="Server only"
            )
        )
        return
    member = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await interaction.guild.fetch_member(interaction.user.id)
    )
    guild_report = report["guilds"].setdefault(
        str(interaction.guild.id),
        {
            "guild_id": interaction.guild.id,
            "guild_name": interaction.guild.name,
            "users": {},
        },
    )
    user_record = guild_report.setdefault("users", {}).setdefault(
        str(member.id), {"user_name": member.display_name}
    )
    today = utc_now().astimezone(IST).date().isoformat()
    current = user_record.get("quest")
    if current and current.get("day") == today:
        if not current.get("channel_id"):
            current["channel_id"] = interaction.channel_id
            current["requested_by"] = interaction.user.display_name
            save_report()
        if current.get("completed"):
            status = "✅ claimed"
        elif current.get("kind") == "wordle" and answer:
            current["claimable"] = answer.strip().lower() == current["answer"]
            status = (
                "✅ ready to claim" if current["claimable"] else "❌ answer not correct"
            )
        else:
            await announce_quest_ready(member)
            status = (
                "✅ ready to claim" if current.get("claimable") else "⏳ in progress"
            )
        if current.get("claimable") and not current.get("completed"):
            current["completed"] = True
            current["claimable"] = False
            user_record["quests_completed"] = (
                int(user_record.get("quests_completed", 0)) + 1
            )
            reward = random.choice(tuple(POWERUPS))
            user_record.setdefault("powerups", []).append(reward)
            save_report()
            claim_embed = message_embed(
                f"✅ Quest complete! You received **{POWERUPS[reward]['name']}**.\n"
                f"Use `/powerups` to inspect it; powerups can only affect boss battles.",
                title="Quest complete",
            )
            claim_embed.set_footer(text=f"Requested By {interaction.user.display_name}")
            await interaction.response.send_message(embed=claim_embed)
            return
        status = "✅ claimed" if current.get("completed") else status
        extra = (
            " Submit the Wordle answer with `/quest answer:<word>`."
            if current.get("kind") == "wordle"
            else ""
        )
        hint = current.get(
            "hint", "Follow the objective above before the end of today."
        )
        quest_status_embed = message_embed(
            f"Your quest is **{status}**:\n\n{current['text']}{extra}\n\n"
            f"**Hint:** {hint}",
            title="Today's quest",
        )
        quest_status_embed.set_footer(
            text=f"Requested By {interaction.user.display_name}"
        )
        await interaction.response.send_message(embed=quest_status_embed)
        return
    if current and current.get("day") != today:
        user_record["quest"] = None
    pool = quest_pool(interaction.guild)
    quest_data = dict(random.choice(pool))
    if random.random() < 0.10:
        quest_data.update(
            {
                "kind": "wordle",
                "text": "Enter today's Wordle answer using `/quest answer:<word>`.",
                "hint": "Solve today's Wordle, then submit the answer exactly with `/quest answer:<word>`.",
                "answer": daily_wordle_answer(utc_now()),
            }
        )
    else:
        quest_data["hint"] = {
            "voice_minutes": "Join the named channel and remain there until the required minutes are reached.",
            "voice_minutes_any": "Spend the required total time in any tracked voice channel today.",
            "join_channel": "Enter the named channel at least once today; the time of entry does not matter.",
            "join_channel_twice": "Leave and return to the named channel twice today.",
            "join_with_player": "Be in a tracked voice channel at the same time as another non-bot member.",
            "long_session": "Start one uninterrupted session and stay until the required duration is complete.",
        }.get(
            quest_data["kind"],
            "Complete the objective shown above before the day ends.",
        )
    quest_data.update(
        {
            "day": today,
            "assigned_at": isoformat(utc_now()),
            "completed": False,
            "claimable": False,
            "channel_id": interaction.channel_id,
            "requested_by": interaction.user.display_name,
        }
    )
    user_record["quest"] = quest_data
    save_report()
    quest_embed = message_embed(
        f"🗺️ {quest_data['text']}\n\n"
        f"**Hint:** {quest_data['hint']}\n\n"
        "You can receive one quest per day.",
        title="Daily quest assigned",
    )
    quest_embed.set_footer(text=f"Requested By {interaction.user.display_name}")
    await interaction.response.send_message(embed=quest_embed)


class QuestCog(commands.Cog):
    """Owns the slash commands in this domain."""

    command_names = ('quest',)


async def setup(bot):
    await bot.add_cog(QuestCog())
    for command in (quest,):
        if bot.tree.get_command(command.name) is None:
            bot.tree.add_command(command)

