"""Application entrypoint.

Runtime state and callbacks live in ``services.runtime``; command ownership is
organized into loadable cogs under ``cogs``.
"""

from services.runtime import TOKEN, bot

COG_MODULES = (
    "cogs.profile",
    "cogs.admin",
    "cogs.fun",
    "cogs.duel",
    "cogs.boss",
    "cogs.quest",
    "cogs.stats",
    "cogs.oddeven",
)


async def load_cogs() -> None:
    for module in COG_MODULES:
        await bot.load_extension(module)


async def setup_hook() -> None:
    await load_cogs()


# commands.Bot invokes setup_hook during login; assigning it keeps the runtime
# module focused on behavior while this file owns application assembly.
bot.setup_hook = setup_hook


def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Copy .env.example to .env and set it."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
