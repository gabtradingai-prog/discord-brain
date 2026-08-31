"""
discord_export.py — export a Discord server's channels to local Markdown.

Purpose: give an AI assistant read access to a Discord history without hosting a
bot. Messages are pulled through the REST API and written as one .md file per
channel; attachments are downloaded alongside them. The assistant then reads
plain files — no integration, no server, no uptime to maintain.

Standard library only (urllib). No pip install, nothing to keep alive.

Usage:
    python discord_export.py list                 # servers + channels the bot sees
    python discord_export.py export <guild_id>    # export everything
    python discord_export.py export <guild_id> --since 2026-01-01

Setup is documented in GUIDE.md, including the one trap that costs hours:
MESSAGE CONTENT INTENT must be enabled or the API silently returns empty
content instead of an error.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://discord.com/api/v10"
ROOT = Path(__file__).resolve().parent
EXPORT_DIR = ROOT / "export"

# Attachments are downloaded during the export, never after: Discord CDN
# URLs are signed and expire in about 48h.
IMAGES_SUBDIR = "_images"
DOWNLOADED = []
FAILED = []
USED_NAMES = set()


def load_token():
    """Token from the environment, falling back to a .env next to the script."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        return token.strip()

    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "DISCORD_BOT_TOKEN":
                return value.strip().strip('"').strip("'")

    sys.exit(
        "Missing token.\n"
        f"Create {env_file} with the line:\n"
        "DISCORD_BOT_TOKEN=your_token_here"
    )


def api_get(path, token, params=None):
    """GET against the Discord API, honouring the rate limit."""
    url = f"{API}{path}"
    if params:
        pairs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{pairs}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBrain (local export, v1)",
    })

    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 429:
                body = json.loads(err.read().decode("utf-8") or "{}")
                wait = float(body.get("retry_after", 2))
                print(f"    rate limit, pause {wait:.1f}s")
                time.sleep(wait + 0.5)
                continue
            if err.code == 403:
                return None  # no permission on this channel, skip it
            if err.code >= 500 and attempt < 4:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    return None


def fetch_all_messages(channel_id, token, since=None):
    """Every message in a channel, oldest first."""
    messages = []
    before = None

    while True:
        batch = api_get(f"/channels/{channel_id}/messages", token,
                        {"limit": 100, "before": before})
        if not batch:
            break

        messages.extend(batch)
        before = batch[-1]["id"]

        if since:
            oldest = datetime.fromisoformat(batch[-1]["timestamp"])
            if oldest < since:
                break

        if len(batch) < 100:
            break
        time.sleep(0.4)  # be polite to the API

    messages.reverse()

    if since:
        messages = [m for m in messages
                    if datetime.fromisoformat(m["timestamp"]) >= since]
    return messages


def download_attachment(url, dest):
    """Download an attachment unless it is already there. True if the file ends up on disk.

    Discord CDN URLs are signed and expire in about 48h (the `ex=` query
    parameter). An export that only keeps links produces an archive that rots
    within two days, so downloading MUST happen during the export.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "DiscordBrain (local export, v1)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if not data:
                return False
            dest.write_bytes(data)
            time.sleep(0.15)          # be polite to the CDN
            return True
        except urllib.error.HTTPError as err:
            if err.code in (403, 404):
                return False          # expired link: lost for this pass
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return False


def render_message(msg, images_dir=None, rel_prefix=""):
    """One Discord message -> one readable Markdown block."""
    author = msg.get("author", {}).get("global_name") \
        or msg.get("author", {}).get("username", "unknown")
    stamp = datetime.fromisoformat(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")

    lines = [f"### {stamp} — {author}", ""]

    if msg.get("content"):
        lines.append(msg["content"])
        lines.append("")

    for embed in msg.get("embeds", []):
        if embed.get("title"):
            lines.append(f"**{embed['title']}**")
        if embed.get("description"):
            lines.append(embed["description"])
        for field in embed.get("fields", []):
            lines.append(f"- **{field.get('name', '')}** : {field.get('value', '')}")
        lines.append("")

    for att in msg.get("attachments", []):
        filename = att.get("filename") or "file"
        url = att.get("url")
        target = None

        if images_dir is not None and url:
            # The attachment id is unique; the message id is not enough since
            # one message can carry several files. The extension is kept apart
            # because safe_name() would turn "image.png" into "image-png", and
            # a file with no extension stops being recognised as an image.
            stem, _, ext = filename.rpartition(".")
            local = f"{att.get('id') or msg['id']}_{safe_name(stem or filename)}"
            if ext:
                local += f".{safe_name(ext)}"
            if download_attachment(url, images_dir / local):
                target = f"{rel_prefix}{local}"
                DOWNLOADED.append(local)
            else:
                FAILED.append(f"{msg['id']} {filename}")

        # Local path when we have it, otherwise the signed URL (which expires).
        lines.append(f"[file: {filename}]({target or url})")
        lines.append("")

    return "\n".join(lines)


def safe_name(name):
    keep = "-_ "
    cleaned = "".join(c if c.isalnum() or c in keep else "-" for c in name)
    return cleaned.strip().replace(" ", "-").lower() or "channel"


def cmd_list(token):
    guilds = api_get("/users/@me/guilds", token) or []
    if not guilds:
        print("No servers. The bot has not been invited anywhere.")
        return

    for guild in guilds:
        print(f"\n{guild['name']}   (guild_id: {guild['id']})")
        channels = api_get(f"/guilds/{guild['id']}/channels", token) or []
        categories = {c["id"]: c["name"] for c in channels if c["type"] == 4}

        for chan in channels:
            if chan["type"] not in (0, 5):  # texte + annonces
                continue
            cat = categories.get(chan.get("parent_id"), "")
            prefix = f"[{cat}] " if cat else ""
            print(f"   #{prefix}{chan['name']}   ({chan['id']})")


def cmd_export(token, guild_id, since=None):
    guilds = api_get("/users/@me/guilds", token) or []
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if not guild:
        # Exit 0 on purpose. A server the bot was removed from must not fail the
        # whole pipeline: one revoked invite should never cost you every later
        # step of a nightly run.
        print(f"Server {guild_id} not found — the bot is no longer invited there. "
              f"Step skipped; data already exported stays on disk.")
        return

    out_dir = EXPORT_DIR / safe_name(guild["name"])
    out_dir.mkdir(parents=True, exist_ok=True)

    channels = api_get(f"/guilds/{guild_id}/channels", token) or []
    categories = {c["id"]: c["name"] for c in channels if c["type"] == 4}
    text_channels = [c for c in channels if c["type"] in (0, 5)]

    print(f"\n{guild['name']} — {len(text_channels)} channels texte\n")
    total = 0

    for chan in text_channels:
        print(f"  #{chan['name']} ...", end=" ", flush=True)
        messages = fetch_all_messages(chan["id"], token, since)

        if not messages:
            print("empty or inaccessible")
            continue

        cat = categories.get(chan.get("parent_id"), "")
        header = [
            f"# #{chan['name']}",
            "",
            f"- Server: {guild['name']}",
            f"- Category: {cat or '(none)'}",
            f"- Messages: {len(messages)}",
            f"- Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
        ]
        name = safe_name(f"{cat}-{chan['name']}" if cat else chan["name"])
        # Two channels can share a name. Without this guard the second one
        # overwrites the first and a whole channel is lost silently.
        if name in USED_NAMES:
            name = f"{name}-{chan['id']}"
        USED_NAMES.add(name)

        images_dir = out_dir / IMAGES_SUBDIR / name
        body = "\n".join(
            render_message(m, images_dir, f"{IMAGES_SUBDIR}/{name}/")
            for m in messages)

        (out_dir / f"{name}.md").write_text(
            "\n".join(header) + body, encoding="utf-8")

        total += len(messages)
        print(f"{len(messages)} messages")

    print(f"\nDone. {total} messages in {out_dir}")
    if DOWNLOADED or FAILED:
        mb = sum(f.stat().st_size for f in (out_dir / IMAGES_SUBDIR).rglob("*")
                 if f.is_file()) / (1024 * 1024)
        print(f"Attachments: {len(DOWNLOADED)} local · {len(FAILED)} "
              f"not retrieved (expired link) · {mb:.0f} MB total")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    token = load_token()
    command = sys.argv[1]

    if command == "list":
        cmd_list(token)
    elif command == "export":
        if len(sys.argv) < 3:
            sys.exit("Usage: python discord_export.py export <guild_id>")
        since = None
        if "--since" in sys.argv:
            raw = sys.argv[sys.argv.index("--since") + 1]
            since = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        cmd_export(token, sys.argv[2], since)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
