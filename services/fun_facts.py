"""Generated, offline fun facts for profile embeds.

The bank is intentionally template-driven: changing the templates produces
many combinations without requiring an API call or storing repetitive prose.
Adult entries are suggestive jokes, not claims about real server members.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any


CATEGORY_COUNTS = {
    "dank": 250,
    "dark": 200,
    "adult": 150,
    "history": 80,
    "gaming": 70,
    "productivity": 70,
    "food": 60,
    "sleep": 60,
    "programming": 60,
}

TEMPLATES: dict[str, tuple[str, ...]] = {
    "dank": (
        "With {hours:.1f} hours in VC, you had enough time to overthink {number} simple decisions.",
        "{hours:.1f} hours online: emotionally present, physically questionable.",
        "You spent {hours:.1f} hours in VC. Your Wi-Fi knows more about your life than most people.",
        "{number} minutes of this could have been an email, but here we are.",
        "At {hours:.1f} hours, this is no longer a call. It is a residency.",
        "You have invested {hours:.1f} hours into conversations that started with 'quick question'.",
        "Your VC attendance is now legally classified as a personality trait.",
        "{hours:.1f} hours: enough time to make a plan, ignore it, and discuss it in VC.",
        "You were in VC for {hours:.1f} hours. Productivity is currently requesting evidence.",
        "Your microphone has heard {number} minutes of lore that should never be documented.",
    ),
    "dark": (
        "{hours:.1f} hours in VC is enough time for {number} tiny existential crises.",
        "You spent {hours:.1f} hours in VC. The void called; it was already in the channel.",
        "{number} minutes passed, and somehow the group chat became a historical tragedy.",
        "Your total is {hours:.1f} hours. Even your sleep schedule has entered witness protection.",
        "{hours:.1f} hours of VC: a documentary about choices and their consequences.",
        "You have spent {hours:.1f} hours talking instead of confronting the final boss: tomorrow.",
        "At {hours:.1f} hours, the only thing more active than the VC is your avoidance strategy.",
        "{number} minutes of conversation and not one character arc completed.",
    ),
    "adult": (
        "{hours:.1f} hours in VC is enough time to flirt badly, recover, and flirt worse.",
        "You spent {hours:.1f} hours in VC. Romance was not achieved, but awkward silence was.",
        "{number} minutes of adult supervision were clearly required, and nobody volunteered.",
        "{hours:.1f} hours online: suspiciously long for a 'quick chat'.",
        "Your VC history contains {hours:.1f} hours of evidence that sleep is not your only bad decision.",
        "At {hours:.1f} hours, even your excuses need a safe word: 'I was muted'.",
        "You could have had a mature conversation in {number} minutes, but chose chaos instead.",
        "{hours:.1f} hours of VC and still no one has learned to communicate normally.",
    ),
    "history": (
        "{hours:.1f} hours is about {number} minutes—the length of several ancient campaigns.",
        "Your {hours:.1f} hours could cover a surprisingly serious amount of historical reading.",
        "History remembers empires; your friends remember who stayed muted for {number} minutes.",
        "{hours:.1f} hours is enough time to explore a major historical era in miniature.",
    ),
    "gaming": (
        "{hours:.1f} hours is enough for roughly {number} average gaming matches.",
        "Your VC total could cover a full gaming session, including the inevitable settings argument.",
        "{number} minutes: enough time to queue, lose, blame lag, and queue again.",
        "At {hours:.1f} hours, your party has earned a loading-screen memorial.",
    ),
    "productivity": (
        "{hours:.1f} hours could have supported {number} focused work sprints.",
        "Your VC total is enough time for a small project, several breaks, and one dramatic restart.",
        "{number} minutes is enough to learn one useful concept and immediately explain it incorrectly.",
        "With {hours:.1f} hours, you could have built a routine—or discussed building one.",
    ),
    "food": (
        "{hours:.1f} hours is enough time to cook approximately {number} simple meals.",
        "Your VC total could cover a suspicious number of snack breaks.",
        "{number} minutes is enough to order food, regret the delivery fee, and order dessert.",
        "At {hours:.1f} hours, your snacks deserve their own attendance record.",
    ),
    "sleep": (
        "{hours:.1f} hours is also a perfectly respectable amount of sleep you did not get.",
        "{number} minutes of VC could have improved your sleep schedule. It did not.",
        "Your VC total equals {hours:.1f} hours of sleep, assuming you ever log off.",
        "At {hours:.1f} hours, your sleep schedule has filed a missing-person report.",
    ),
    "programming": (
        "{hours:.1f} hours is enough to write code, find a bug, and discover the bug was you.",
        "{number} minutes is enough to install a dependency and spend the rest debugging it.",
        "Your VC total could produce a small script, three bugs, and one heroic stack trace.",
        "At {hours:.1f} hours, even the compiler is asking you to take a break.",
    ),
}


class _DisplayHours(float):
    def __format__(self, format_spec: str) -> str:
        return f"{round(float(self))} hours"


def _values(index: int, total_seconds: int) -> dict[str, Any]:
    hours = max(0, total_seconds) / 3600
    return {
        "hours": _DisplayHours(hours),
        "number": max(1, int(hours * 6) + (index * 7) % 41),
    }


def build_fact_bank() -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for category, target_count in CATEGORY_COUNTS.items():
        templates = TEMPLATES[category]
        for index in range(target_count):
            template = templates[index % len(templates)]
            facts.append(
                {
                    "id": f"{category}-{index + 1:03d}",
                    "category": category,
                    "template": template,
                }
            )
    return facts


FACT_BANK = build_fact_bank()


def category_counts() -> dict[str, int]:
    return dict(Counter(fact["category"] for fact in FACT_BANK))


def choose_fact(
    total_seconds: int,
    recent_ids: set[str] | None = None,
    category: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, str]:
    recent_ids = recent_ids or set()
    rng = rng or random
    candidates = [
        fact
        for fact in FACT_BANK
        if (category is None or fact["category"] == category)
        and fact["id"] not in recent_ids
    ]
    if not candidates:
        candidates = [
            fact
            for fact in FACT_BANK
            if category is None or fact["category"] == category
        ]
    if not candidates:
        raise ValueError(f"No facts available for category {category!r}")

    fact = rng.choice(candidates).copy()
    fact["text"] = fact["template"].format(**_values(rng.randrange(1000), total_seconds))
    return fact
