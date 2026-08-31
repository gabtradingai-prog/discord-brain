# discord-brain

Export a Discord server to local Markdown so an AI assistant can read it.

No hosted bot. No server. No uptime to maintain. A script pulls messages through
the Discord REST API, writes one `.md` file per channel, downloads every
attachment next to it, and exits. Your assistant then reads plain files.

**Standard library only.** `urllib`, nothing else. No `pip install`, no virtualenv,
nothing that breaks in six months.

## Why this exists

An AI assistant knows nothing about you between sessions. It cannot open your
Discord, so every conversation restarts from whatever you remember to tell it —
which means your memory, with your biases.

Pulling the history down as Markdown fixes that in the simplest way available:
the assistant reads files.

## Install

Nothing to install. Python 3.8+ and the standard library.

```bash
git clone https://github.com/<you>/discord-brain.git
cd discord-brain
```

## Setup

Four steps on the Discord developer portal, about ten minutes. They are detailed
in [GUIDE.md](GUIDE.md), including the trap below.

1. Create an application and a bot at <https://discord.com/developers/applications>,
   copy the token.
2. Put it in a `.env` next to the script:
   ```
   DISCORD_BOT_TOKEN=your_token_here
   ```
3. **Bot tab → Privileged Gateway Intents → enable MESSAGE CONTENT INTENT.**
4. OAuth2 → URL Generator → scope `bot`, permissions **View Channels** and
   **Read Message History** only. Open the URL, pick your server.

> **The trap that costs hours.** Skip step 3 and the API returns no error. It
> returns author, timestamp and message id, and leaves `content`, `embeds` and
> `attachments` **empty**. You get files full of message headers with nothing
> under them, and you go hunting for a bug in your rendering code.
>
> General rule: when an API hands you blanks instead of an error, check a
> permission before you check your code.

## Usage

```bash
python discord_export.py list                  # servers and channels the bot sees
python discord_export.py export <guild_id>     # export everything
python discord_export.py export <guild_id> --since 2026-01-01
```

Run `list` first, always. If your server is not there, the invite did not go
through. Then open any exported file and confirm there is **actual text** under
the message headers — that is the check that catches the trap above.

## What it handles

Everything below is a problem you hit in practice, on any API:

- **Pagination** — 100 messages max per call; pages backward on the `before`
  cursor, then reverses so the file reads oldest to newest.
- **Rate limits (429)** — reads `retry_after` from the response instead of
  guessing a fixed delay.
- **Missing permissions (403)** — skips that channel and keeps going. One private
  channel must not kill an export of 1500 messages.
- **Server errors (5xx)** — retries with growing delays.
- **Attachments** — downloaded *during* the export, never after. Discord CDN URLs
  are signed and expire in about 48 hours, so an export that only keeps links
  produces an archive that rots in two days.
- **Filesystem-hostile names** — channel names carry emoji and separators; they
  are sanitised, and the real name is written back into the file header.
- **Name collisions** — two channels can share a name. Without a guard the second
  silently overwrites the first.
- **Export timestamp in every file** — the single most important line, because it
  is what stops anyone drawing conclusions from stale data.

## Output

```
export/<server-name>/
├── <category>-<channel>.md
└── _images/
    └── <category>-<channel>/
        └── <attachment-id>_<filename>.png
```

Each file opens with its context:

```markdown
# #general

- Serveur : My Server
- Categorie : || Main ||
- Messages : 412
- Exporte : 2026-08-31 00:07
```

## Building more on top

The exporter is layer 1 of a three-layer design, and the split matters:

| Layer | Job | Breaks when |
|---|---|---|
| 1 — Export | Pull messages, write raw Markdown | Token expires, API changes |
| 2 — Analyse | Count, cross-reference, produce numbers | Your naming convention changes |
| 3 — Publish | Write results somewhere you actually read | You move your notes folder |

Each layer fails for a different reason. Keep them in separate files, and a
broken analysis never costs you a good export.

**Layer 1 interprets nothing.** It copies. All meaning belongs in layer 2 — which
means changing your mind about what to measure never means re-downloading
anything.

[GUIDE.md](GUIDE.md) walks through building layers 2 and 3, and covers swapping
the source (Slack, Notion, Gmail, a CSV) without touching them.

## Security

- The token lives in `.env`, never in code, never in a script.
- `.gitignore` covers `.env` and `export/` from the first commit.
- Read permissions only. The bot cannot write, delete, or change anything.
- Everything stays local. Nothing is uploaded anywhere.

## Licence

MIT.
