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
