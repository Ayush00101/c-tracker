"""Persistence and pure state helpers for the Odd-Even cricket game."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "oddeven_games.json"
LOG_CHANNEL_ID = int(os.getenv("ODDEVEN_LOG_CHANNEL_ID", "0") or 0)
ALLOWED_NUMBERS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20)
CHOICE_TIMEOUT_SECONDS = 60
WARNING_SECONDS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_store() -> dict[str, Any]:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_PATH.exists():
        return {"active": {}, "completed": []}
    try:
        with DATA_PATH.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"active": {}, "completed": []}
    if not isinstance(value, dict):
        return {"active": {}, "completed": []}
    value.setdefault("active", {})
    value.setdefault("completed", [])
    return value


STORE = load_store()


def save_store() -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(STORE, file, indent=2)
        file.write("\n")
    temporary.replace(DATA_PATH)


def create_game(guild_id: int, channel_id: int, challenger_id: int, opponent_id: int) -> dict[str, Any]:
    game = {
        "id": uuid.uuid4().hex,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "challenger_id": challenger_id,
        "opponent_id": opponent_id,
        "status": "challenge",
        "created_at": now_iso(),
        "phase": "challenge",
        "toss_player_id": None,
        "toss_choice": None,
        "toss_numbers": {},
        "toss_winner_id": None,
        "batting_first_id": None,
        "innings": 0,
        "batsman_id": None,
        "bowler_id": None,
        "scores": {"first": 0, "second": 0},
        "balls": {"first": 0, "second": 0},
        "wickets": {"first": 0, "second": 0},
        "ball_choices": {},
        "last_result": "Waiting for opponent acceptance.",
        "started_at": None,
        "ended_at": None,
        "end_reason": None,
        "message_id": None,
        "pending_draw": None,
    }
    STORE["active"][game["id"]] = game
    save_store()
    return game


def active_games_for_player(player_id: int) -> list[dict[str, Any]]:
    return [
        game
        for game in STORE["active"].values()
        if player_id in {game["challenger_id"], game["opponent_id"]}
        and game.get("status") in {"challenge", "active"}
    ]


def get_game(game_id: str) -> dict[str, Any] | None:
    return STORE["active"].get(game_id)


def persist() -> None:
    save_store()


def finish_game(game: dict[str, Any], winner_id: int | None, reason: str) -> dict[str, Any]:
    game["status"] = "completed"
    game["ended_at"] = now_iso()
    game["end_reason"] = reason
    game["winner_id"] = winner_id
    completed = dict(game)
    STORE["active"].pop(game["id"], None)
    STORE["completed"].append(completed)
    save_store()
    return completed
