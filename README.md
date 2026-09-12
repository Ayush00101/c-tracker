# Discord Voice Channel Tracker

This bot records how long users spend in a Discord voice channel. Completed
sessions are saved as readable, indented JSON in `vc_report.txt`.

## Setup

1. Install Python 3.10 or newer.
2. Create a Discord application and bot in the
   [Discord Developer Portal](https://discord.com/developers/applications).
3. Enable the **Server Members Intent** and **Message Content Intent** only if
   you later add features that require them. This first version needs the
   default intents plus voice states.
4. Invite the bot to the server with the `bot` scope and permission to view the
   server and connect to voice channels.
5. Create a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

6. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
7. Optionally set `TRACKED_GUILD_ID` and `TRACKED_VOICE_CHANNEL_ID` to limit
   tracking. Leave them blank to track all visible voice channels.
8. Start the bot:

   ```powershell
   py bot.py
   ```

The report file is intentionally excluded from Git by `.gitignore`, because it
contains server activity data.

## `/profile`

After the bot is online, Discord will show a `/profile` slash command with a
required `user` member option. It displays total time, longest session, number
of sessions, and the user's latest join/leave information. Times in the embed
are shown in IST.

If a user leaves and rejoins the same tracked voice channel within 10 minutes,
the bot merges that break into the previous session instead of counting a new
session.

Visits shorter than 5 minutes are ignored and are not saved or counted as
sessions.

Users who move between voice channels within 10 minutes keep one continuous
session. Bot accounts are ignored. The `/current` command shows the non-bot
users currently connected to each voice channel in the server.

Additional commands include:

- `/leaderboard` with daily, weekly, or monthly periods
- `/recap` for a user's weekly voice summary and daily breakdown
- `/timecapsule` for a historical weekly snapshot or two-week comparison
- `/progress` for progress toward the next weekly rank and overall milestone
- `/timeline` for a personal VC journey timeline, unlocked at 50 lifetime hours
- `/roulette` for one of exactly 150 temporary VC roles/stat effects lasting
  two hours
- `/goal` to set a daily, weekly, or monthly voice-time goal
- `/mygoal` to view goal progress with boxed progress bars and a weekly breakdown
- `/duel` to challenge another member to an animated weekly VC duel; optionally
  select one phase (Morning, Evening, Night, or Midnight) for every round.
  Each completed round shows a five-second countdown before the next round,
  and the duel ends immediately when either fighter wins four rounds.
- `/lastduel` to view the latest completed duel round summary
- `/achievements` with weekly, monthly, daily, streak, and 50-hour milestones
- `/vcstats` for voice-channel rankings
- `/channelstats` for detailed usage, busiest hours, average sessions, and
  most active members in a selected voice channel
- `/heatmap` for a graph-style IST weekly activity heatmap using two-hour cells
- `/bossbattle` to summon a 24-hour VC boss scaled from recent weekly activity;
  party VC time deals damage, 10% alerts show damage leaders, and victories
  award crowns that last 24 hours. Additional players can join while the boss
  is active by entering a tracked VC; grouped players deal bonus damage.
  Every 25 minutes of a player's battle VC time increases their personal
  damage multiplier by an additive 10%, capped at 175% per player. Boss
  health is a numeric HP value based on the party's average daily VC time for
  the current week, with a minimum of 200,000 HP. Optional earned
  powerups can be used with the command while a boss is active. Reusing the
  command shows live battle stats; the boss appears first, followed by four
  player cards per page with Previous/Next controls. Damage is measured every
  minute but committed to the battle every five minutes. Boss base HP is at
  least 200,000 HP.
- `/quest` for one daily quest from exactly 50 voice-channel-based quests.
  The public quest embed includes a practical hint explaining how to complete
  the objective; 10% of assignments ask for the hidden daily Wordle answer.
  The quest message channel is remembered, and completing a voice objective
  posts a public ready-to-claim embed there. Run `/quest` again to claim the
  powerup.
- `/powerups` to view earned boss-only powerups in a public embed.
- `/museum` for historical server records and notable VC achievements
- `/lobotomykaisen` for the ranked eight-page meme gallery with emoji navigation
- `/history` for the owner's paginated channel history
- `/csv` for an owner-only CSV export sent by DM

Weekly achievement ranks reset every Sunday at midnight IST. Users receive
rank-up notifications and a weekly summary by DM. User statistics commands
include a button to switch between the normal stats embed and a weekly
activity graph.

`/timecapsule` accepts week dates in `YYYY-MM-DD` format. It provides overview,
user, channel, and highest-used-channel graph pages. Supplying a second week
shows GitHub-style `+`/`-` differences against that comparison week.

## Fun fact bank

[fun_facts.py](./fun_facts.py) contains an offline, template-driven bank of
1,000 profile facts. It generates different numeric combinations from the
user's tracked time and avoids recently shown fact IDs when callers provide a
recent-ID set.

Current category counts:

- Dank: 250
- Dark: 200
- Adult humor: 150
- History: 80
- Gaming: 70
- Productivity: 70
- Food: 60
- Sleep: 60
- Programming: 60

Use `choose_fact(total_seconds, recent_ids=...)` to select and render a fact.
