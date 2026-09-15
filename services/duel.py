"""VC duel persistence and IST phase windows."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
DUEL_PATH = Path(__file__).with_name("duels.json")
PHASES = (
    ("I", "Morning", time(8, 0), time(14, 0), False),
    ("II", "Evening", time(14, 0), time(20, 0), False),
    ("III", "Night", time(20, 0), time(2, 0), True),
    ("IV", "Midnight", time(2, 0), time(8, 0), False),
)


def empty_duel_store() -> dict[str, Any]:
    return {"generated_at": "", "duels": []}


def load_duels() -> dict[str, Any]:
    if not DUEL_PATH.exists():
        return empty_duel_store()
    with DUEL_PATH.open("r", encoding="utf-8") as duel_file:
        text = duel_file.read().strip()
    if not text:
        return empty_duel_store()
    data = json.loads(text)
    if not isinstance(data, dict):
        return empty_duel_store()
    data.setdefault("duels", [])
    return data


def save_duels(store: dict[str, Any], generated_at: str) -> None:
    store["generated_at"] = generated_at
    temporary_path = DUEL_PATH.with_suffix(DUEL_PATH.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as duel_file:
        json.dump(store, duel_file, indent=2)
        duel_file.write("\n")
    temporary_path.replace(DUEL_PATH)


def record_duel(store: dict[str, Any], duel: dict[str, Any], generated_at: str) -> None:
    store.setdefault("duels", []).append(duel)
    save_duels(store, generated_at)


def last_duel_for_user(store: dict[str, Any], user_id: int) -> dict[str, Any] | None:
    matches = [
        duel
        for duel in store.get("duels", [])
        if int(duel.get("challenger_id", 0)) == user_id
        or int(duel.get("challenged_id", 0)) == user_id
    ]
    if not matches:
        return None
    return matches[-1]


def week_dates(week_start: datetime) -> list[date]:
    local_start = week_start.astimezone(IST).date()
    return [local_start + timedelta(days=offset) for offset in range(7)]


def _combine(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=IST).astimezone(timezone.utc)


def phase_windows(day: date) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for roman, name, start_clock, end_clock, wraps in PHASES:
        start = _combine(day, start_clock)
        end_day = day + timedelta(days=1) if wraps else day
        end = _combine(end_day, end_clock)
        start_label = datetime.combine(day, start_clock).strftime("%I:%M %p").lstrip("0")
        end_label = datetime.combine(end_day, end_clock).strftime("%I:%M %p").lstrip("0")
        windows.append(
            {
                "roman": roman,
                "name": name,
                "label": f"{start_label} – {end_label}",
                "start": start,
                "end": end,
            }
        )
    return windows
