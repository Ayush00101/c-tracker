"""Odd-Even cricket game commands and private interaction views."""

import asyncio
import random
from typing import Any

import discord
from discord import app_commands
from services import oddeven as game_store
from services.runtime import bot, member_label, message_embed, utc_now


def players(game: dict[str, Any]) -> tuple[int, int]:
    return game["challenger_id"], game["opponent_id"]


def player_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member_label(guild, user_id, member.display_name if member else str(user_id))


def game_embed(guild: discord.Guild, game: dict[str, Any], title: str = "🏏 Odd-Even") -> discord.Embed:
    first = player_name(guild, game["challenger_id"])
    second = player_name(guild, game["opponent_id"])
    innings = game.get("innings", 0)
    score = game.get("scores", {"first": 0, "second": 0})
    balls = game.get("balls", {"first": 0, "second": 0})
    if innings == 1:
        score_line = f"{first}: **{score['first']}** ({balls['first']} balls)"
    elif innings == 2:
        score_line = (
            f"{first}: **{score['first']}** ({balls['first']} balls)\n"
            f"{second}: **{score['second']}** ({balls['second']} balls)"
        )
    else:
        score_line = "No innings started yet."
    key = "first" if innings == 1 else "second"
    current_ball = balls[key] if innings in (1, 2) else 0
    over_text = (
        f"{current_ball // 6}.{current_ball % 6}"
        if innings in (1, 2)
        else "0.0"
    )
    target = (
        f"\n**Target:** {score['first'] + 1}"
        if innings == 2
        else ""
    )
    embed = discord.Embed(
        title=title,
        description=(
            f"{first} vs {second}\n\n"
            f"**Phase:** {game.get('phase', 'challenge').replace('_', ' ').title()}\n"
            f"**Score:**\n{score_line}\n\n"
            f"**Over:** {over_text}{target}\n"
            f"**Wickets:** {game.get('wickets', {'first': 0, 'second': 0})['first']} - "
            f"{game.get('wickets', {'first': 0, 'second': 0})['second']}\n"
            f"**Batsman:** {player_name(guild, game['batsman_id']) if game.get('batsman_id') else 'Not selected'}\n"
            f"**Bowler:** {player_name(guild, game['bowler_id']) if game.get('bowler_id') else 'Not selected'}\n"
            f"**Last result:** {game.get('last_result', 'a')}"
        ),
        color=discord.Color.gold(),
        timestamp=utc_now(),
    )
    embed.set_footer(text="Requested By the game creator")
    return embed


async def game_message(guild: discord.Guild, game: dict[str, Any]) -> discord.Message | None:
    channel = guild.get_channel(game["channel_id"])
    if not isinstance(channel, discord.abc.Messageable):
        return None
    if game.get("message_id"):
        try:
            return await channel.fetch_message(game["message_id"])
        except (discord.NotFound, discord.HTTPException):
            pass
    return None


async def edit_board(guild: discord.Guild, game: dict[str, Any], title: str = "🏏 Odd-Even") -> None:
    message = await game_message(guild, game)
    if message is not None:
        await message.edit(embed=game_embed(guild, game, title=title), view=None)


async def notify_timeout(guild: discord.Guild, game_id: str, phase: str, waiting_for: list[int]) -> None:
    await asyncio.sleep(game_store.CHOICE_TIMEOUT_SECONDS)
    game = game_store.get_game(game_id)
    if not game or game.get("status") not in {"challenge", "active"} or game.get("phase") != phase:
        return
    channel = guild.get_channel(game["channel_id"])
    if channel is not None:
        mentions = " ".join(f"<@{user_id}>" for user_id in waiting_for)
        await channel.send(f"⏰ {mentions} please choose now. You have 30 seconds before the match is awarded by timeout.")
    await asyncio.sleep(game_store.WARNING_SECONDS)
    game = game_store.get_game(game_id)
    if not game or game.get("phase") != phase:
        return
    winner = next((user_id for user_id in players(game) if user_id not in waiting_for), None)
    await finish_match(guild, game, winner, "timeout")


async def finish_match(guild: discord.Guild, game: dict[str, Any], winner_id: int | None, reason: str) -> None:
    winner = player_name(guild, winner_id) if winner_id else "Draw"
    game["last_result"] = f"{winner} — {reason}"
    completed = game_store.finish_game(game, winner_id, reason)
    await edit_board(guild, game, "🏏 Odd-Even — Match Complete")
    channel = guild.get_channel(game["channel_id"])
    if channel is not None:
        await channel.send(
            embed=game_embed(guild, completed, "🏏 Odd-Even — Final Result")
        )
    if game_store.LOG_CHANNEL_ID:
        log_channel = guild.get_channel(game_store.LOG_CHANNEL_ID)
        if log_channel is not None:
            log = discord.Embed(
                title="🏏 Odd-Even Match Log",
                description=(
                    f"**Winner:** {winner}\n"
                    f"**Reason:** {reason}\n"
                    f"**Scores:** {completed['scores']['first']} - {completed['scores']['second']}\n"
                    f"**Balls:** {completed['balls']['first']} - {completed['balls']['second']}"
                ),
                color=discord.Color.green() if winner_id else discord.Color.orange(),
                timestamp=utc_now(),
            )
            log.set_footer(text="Odd-Even match history")
            await log_channel.send(embed=log)


async def prompt_parity(guild: discord.Guild, game: dict[str, Any]) -> None:
    game["phase"] = "toss_parity"
    game_store.persist()
    channel = guild.get_channel(game["channel_id"])
    if channel is not None:
        await channel.send(
            f"<@{game['toss_player_id']}>, open your private toss choice.",
            view=PrivatePromptView(
                game["id"], game["toss_player_id"], "Choose Even or Odd.", "parity"
            ),
        )
    asyncio.create_task(notify_timeout(guild, game["id"], "toss_parity", [game["toss_player_id"]]))


async def prompt_toss_numbers(guild: discord.Guild, game: dict[str, Any]) -> None:
    game["phase"] = "toss_numbers"
    game["toss_numbers"] = {}
    game_store.persist()
    channel = guild.get_channel(game["channel_id"])
    if channel is not None:
        for user_id in players(game):
            await channel.send(
                f"<@{user_id}>, open your private toss number choices.",
                view=PrivatePromptView(
                    game["id"], user_id, "Choose a number from 1 to 6.", "toss"
                ),
            )
    asyncio.create_task(notify_timeout(guild, game["id"], "toss_numbers", list(players(game))))


async def prompt_ball(guild: discord.Guild, game: dict[str, Any]) -> None:
    game["phase"] = "ball"
    game["ball_choices"] = {}
    game_store.persist()
    channel = guild.get_channel(game["channel_id"])
    if channel is not None:
        for user_id in (game["batsman_id"], game["bowler_id"]):
            await channel.send(
                f"<@{user_id}>, open your private ball choice for over "
                f"{game['balls'][_innings_key(game)] // 6 + 1}, ball "
                f"{game['balls'][_innings_key(game)] % 6 + 1}.",
                view=PrivatePromptView(
                    game["id"], user_id, "Choose your batting/bowling number.", "ball"
                ),
            )
    asyncio.create_task(notify_timeout(guild, game["id"], "ball", [game["batsman_id"], game["bowler_id"]]))


def _innings_key(game: dict[str, Any]) -> str:
    return "first" if game["innings"] == 1 else "second"


async def resolve_toss(guild: discord.Guild, game: dict[str, Any]) -> None:
    first, second = players(game)
    total = game["toss_numbers"][str(first)] + game["toss_numbers"][str(second)]
    parity = "even" if total % 2 == 0 else "odd"
    winner = game["toss_player_id"] if parity == game["toss_choice"] else (
        second if game["toss_player_id"] == first else first
    )
    game["toss_winner_id"] = winner
    game["phase"] = "role_choice"
    game["last_result"] = (
        f"Toss numbers: {game['toss_numbers'][str(first)]} + "
        f"{game['toss_numbers'][str(second)]} = {total} ({parity}). "
        f"Toss winner: {player_name(guild, winner)}."
    )
    game_store.persist()
    await edit_board(guild, game, "🏏 Odd-Even — Toss Result")
    message = await game_message(guild, game)
    if message is not None:
        await message.edit(view=RoleView(game["id"], winner))


async def resolve_numbers(guild: discord.Guild, game: dict[str, Any], mode: str) -> None:
    choices = game["toss_numbers"] if mode == "toss" else game["ball_choices"]
    if len(choices) != 2:
        return
    if mode == "toss":
        await resolve_toss(guild, game)
        return
    batsman = game["batsman_id"]
    bowler = game["bowler_id"]
    bat_number = choices[str(batsman)]
    bowl_number = choices[str(bowler)]
    key = _innings_key(game)
    game["balls"][key] += 1
    if bat_number == bowl_number:
        game["wickets"][key] = 1
        game["last_result"] = f"WICKET! Both chose {bat_number}."
        game_store.persist()
        await edit_board(guild, game, "🏏 Odd-Even — Wicket")
        if game["innings"] == 1:
            await asyncio.sleep(5)
            game["innings"] = 2
            game["batsman_id"] = game["opponent_id"] if game["batting_first_id"] == game["challenger_id"] else game["challenger_id"]
            game["bowler_id"] = game["batting_first_id"]
            game["last_result"] = "Second innings begins."
            await prompt_ball(guild, game)
            await edit_board(guild, game, "🏏 Odd-Even — Second Innings")
        else:
            await finish_match(guild, game, None if game["scores"]["second"] == game["scores"]["first"] else game["batting_first_id"], "dismissal")
        return
    game["scores"][key] += bat_number
    game["last_result"] = f"{bat_number} runs. Batsman chose {bat_number}; bowler chose {bowl_number}."
    if game["innings"] == 2 and game["scores"]["second"] > game["scores"]["first"]:
        await finish_match(guild, game, game["batsman_id"], "chase")
        return
    game_store.persist()
    await edit_board(guild, game)
    await prompt_ball(guild, game)


class ChallengeView(discord.ui.View):
    def __init__(self, game_id: str, opponent_id: int):
        super().__init__(timeout=game_store.CHOICE_TIMEOUT_SECONDS)
        self.game_id = game_id
        self.opponent_id = opponent_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Only the challenged player can accept.", ephemeral=True)
            return
        game = game_store.get_game(self.game_id)
        if not game or game["status"] != "challenge":
            await interaction.response.send_message("This challenge is no longer active.", ephemeral=True)
            return
        game["status"] = "active"
        game["started_at"] = game_store.now_iso()
        game["toss_player_id"] = random.choice(players(game))
        game_store.persist()
        await interaction.response.edit_message(embed=game_embed(interaction.guild, game, "🏏 Odd-Even — Toss"), view=None)
        await prompt_parity(interaction.guild, game)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Only the challenged player can decline.", ephemeral=True)
            return
        game = game_store.get_game(self.game_id)
        if game:
            game_store.finish_game(game, None, "declined")
        await interaction.response.edit_message(content="Challenge declined.", embed=None, view=None)
        self.stop()

    async def on_timeout(self) -> None:
        game = game_store.get_game(self.game_id)
        if game and game["status"] == "challenge":
            game_store.finish_game(game, None, "challenge expired")


class PrivatePromptView(discord.ui.View):
    def __init__(self, game_id: str, user_id: int, prompt: str, mode: str):
        super().__init__(timeout=game_store.CHOICE_TIMEOUT_SECONDS)
        self.game_id, self.user_id, self.prompt, self.mode = (
            game_id,
            user_id,
            prompt,
            mode,
        )

    @discord.ui.button(label="Open private choices", style=discord.ButtonStyle.primary)
    async def open_choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This private choice belongs to another player.", ephemeral=True)
            return
        game = game_store.get_game(self.game_id)
        expected = "toss_parity" if self.mode == "parity" else "toss_numbers" if self.mode == "toss" else "ball"
        if not game or game["phase"] != expected:
            await interaction.response.send_message("This choice is no longer active.", ephemeral=True)
            return
        view: discord.ui.View
        if self.mode == "parity":
            view = ParityView(self.game_id, self.user_id)
        else:
            view = NumberView(self.game_id, self.user_id, self.mode)
        await interaction.response.send_message(self.prompt, view=view, ephemeral=True)


class ParityView(discord.ui.View):
    def __init__(self, game_id: str, user_id: int):
        super().__init__(timeout=None)
        self.game_id, self.user_id = game_id, user_id

    async def choose(self, interaction: discord.Interaction, value: str) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your toss choice.", ephemeral=True)
            return
        game = game_store.get_game(self.game_id)
        if not game or game["phase"] != "toss_parity":
            await interaction.response.send_message("This choice is no longer active.", ephemeral=True)
            return
        game["toss_choice"] = value
        game_store.persist()
        await interaction.response.edit_message(content=f"Locked in: {value.title()}.", view=None)
        await prompt_toss_numbers(interaction.guild, game)

    @discord.ui.button(label="Even", style=discord.ButtonStyle.primary)
    async def even(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, "even")

    @discord.ui.button(label="Odd", style=discord.ButtonStyle.primary)
    async def odd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, "odd")


class NumberView(discord.ui.View):
    def __init__(self, game_id: str, user_id: int, mode: str):
        super().__init__(timeout=None)
        self.game_id, self.user_id, self.mode = game_id, user_id, mode
        for number in game_store.ALLOWED_NUMBERS if mode == "ball" else range(1, 7):
            button = discord.ui.Button(label=str(number), style=discord.ButtonStyle.secondary)
            button.callback = self._callback(number)
            self.add_item(button)

    def _callback(self, number: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This is not your private number choice.", ephemeral=True)
                return
            game = game_store.get_game(self.game_id)
            expected_phase = "toss_numbers" if self.mode == "toss" else "ball"
            if not game or game["phase"] != expected_phase:
                await interaction.response.send_message("This choice is no longer active.", ephemeral=True)
                return
            choices = game["toss_numbers"] if self.mode == "toss" else game["ball_choices"]
            if str(self.user_id) in choices:
                await interaction.response.send_message("You already chose a number.", ephemeral=True)
                return
            choices[str(self.user_id)] = number
            game_store.persist()
            await interaction.response.edit_message(content=f"Locked in: {number}.", view=None)
            if len(choices) == 2:
                await resolve_numbers(interaction.guild, game, self.mode)
        return callback


class RoleView(discord.ui.View):
    def __init__(self, game_id: str, user_id: int):
        super().__init__(timeout=None)
        self.game_id, self.user_id = game_id, user_id

    async def choose(self, interaction: discord.Interaction, batting: bool) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the toss winner can choose roles.", ephemeral=True)
            return
        game = game_store.get_game(self.game_id)
        if not game or game["phase"] != "role_choice":
            await interaction.response.send_message("The role choice is no longer active.", ephemeral=True)
            return
        game["batting_first_id"] = self.user_id if batting else (game["opponent_id"] if self.user_id == game["challenger_id"] else game["challenger_id"])
        game["innings"] = 1
        game["batsman_id"] = game["batting_first_id"]
        game["bowler_id"] = game["opponent_id"] if game["batsman_id"] == game["challenger_id"] else game["challenger_id"]
        game["last_result"] = f"{player_name(interaction.guild, game['batsman_id'])} bats first."
        game_store.persist()
        await interaction.response.edit_message(view=None)
        await edit_board(interaction.guild, game, "🏏 Odd-Even — First Innings")
        await prompt_ball(interaction.guild, game)

    @discord.ui.button(label="Batting", style=discord.ButtonStyle.success)
    async def batting(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, True)

    @discord.ui.button(label="Bowling", style=discord.ButtonStyle.primary)
    async def bowling(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.choose(interaction, False)


class DrawView(discord.ui.View):
    def __init__(self, game_id: str, opponent_id: int):
        super().__init__(timeout=60)
        self.game_id, self.opponent_id = game_id, opponent_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Only the other player can answer this draw offer.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept draw", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        game = game_store.get_game(self.game_id)
        if game:
            await finish_match(interaction.guild, game, None, "draw")
        await interaction.response.edit_message(content="Draw accepted.", embed=None, view=None)
        self.stop()

    @discord.ui.button(label="Decline draw", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        game = game_store.get_game(self.game_id)
        if game:
            game["pending_draw"] = None
            game_store.persist()
        await interaction.response.edit_message(content="Draw declined.", embed=None, view=None)
        self.stop()


def find_player_game(user_id: int) -> dict[str, Any] | None:
    games = game_store.active_games_for_player(user_id)
    return games[0] if games else None


@app_commands.command(name="oddeven", description="Challenge a member to an Odd-Even cricket match")
@app_commands.describe(opponent="The non-bot server member you want to challenge")
async def oddeven(interaction: discord.Interaction, opponent: discord.Member) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(embed=message_embed("Use this command inside a server.", title="Server only"))
        return
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message(embed=message_embed("Choose a different non-bot opponent.", title="Invalid opponent"))
        return
    if find_player_game(interaction.user.id) or find_player_game(opponent.id):
        await interaction.response.send_message(embed=message_embed("One of the players is already in a match.", title="Match unavailable"))
        return
    game = game_store.create_game(interaction.guild.id, interaction.channel_id, interaction.user.id, opponent.id)
    embed = game_embed(interaction.guild, game, "🏏 Odd-Even — Challenge")
    embed.description += f"\n\n{opponent.mention}, you have 60 seconds to accept."
    await interaction.response.send_message(embed=embed, view=ChallengeView(game["id"], opponent.id))
    game["message_id"] = (await interaction.original_response()).id
    game_store.persist()


@app_commands.command(name="offerdraw", description="Offer a draw to the other Odd-Even player")
async def offerdraw(interaction: discord.Interaction) -> None:
    game = find_player_game(interaction.user.id)
    if interaction.guild is None or not game or game["status"] != "active":
        await interaction.response.send_message(embed=message_embed("You are not in an active Odd-Even match.", title="No active match"), ephemeral=True)
        return
    if game.get("pending_draw"):
        await interaction.response.send_message("A draw offer is already pending.", ephemeral=True)
        return
    opponent_id = next(user_id for user_id in players(game) if user_id != interaction.user.id)
    game["pending_draw"] = interaction.user.id
    game_store.persist()
    await interaction.response.send_message(embed=message_embed(f"<@{opponent_id}> has been offered a draw.", title="Draw offered"))
    message = await interaction.original_response()
    await message.edit(view=DrawView(game["id"], opponent_id))


@app_commands.command(name="forfeitgame", description="Forfeit your active Odd-Even match")
async def forfeitgame(interaction: discord.Interaction) -> None:
    game = find_player_game(interaction.user.id)
    if interaction.guild is None or not game:
        await interaction.response.send_message(embed=message_embed("You are not in an Odd-Even match.", title="No active match"), ephemeral=True)
        return
    winner = next(user_id for user_id in players(game) if user_id != interaction.user.id)
    await interaction.response.send_message(embed=message_embed("You forfeited the match.", title="Match forfeited"), ephemeral=True)
    await finish_match(interaction.guild, game, winner, "forfeit")


async def setup(bot_instance) -> None:
    bot_instance.tree.add_command(oddeven)
    bot_instance.tree.add_command(offerdraw)
    bot_instance.tree.add_command(forfeitgame)
