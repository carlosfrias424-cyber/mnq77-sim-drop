#!/usr/bin/env python3
"""Read bullbot.ai Discord alerts and POST them to the local bullbot hook.

Alerts only. Does not talk to Tradovate itself.
MNQ / MNQU only. Same classify as bullbot_hook.py (SHORT entry, SHORT exit, …).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
import discord

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
LOG = ROOT / "logs/bullbot_discord.jsonl"
HOOK = os.environ.get("BULLBOT_HOOK", "http://127.0.0.1:8788/tv/signal")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0") or 0)
SEEN = set()


def envload() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def emit(**kw) -> None:
    rec = {"ts": int(time.time() * 1000), **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def blob_of(msg: discord.Message) -> str:
    parts = [msg.content or ""]
    for e in msg.embeds:
        if e.title:
            parts.append(e.title)
        if e.description:
            parts.append(e.description)
        for f in e.fields:
            parts.append(f"{f.name}: {f.value}")
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    return "\n".join(x for x in parts if x).strip()


def is_mnq(blob: str) -> bool:
    u = blob.upper()
    return "MNQ" in u or "MNQU" in u


def forward(blob: str, msg_id: int) -> None:
    if not blob or msg_id in SEEN:
        return
    SEEN.add(msg_id)
    if len(SEEN) > 500:
        SEEN.clear()
        SEEN.add(msg_id)
    if not is_mnq(blob):
        emit(event="skip_symbol", id=msg_id, raw=blob[:300])
        return
    try:
        r = requests.post(
            HOOK,
            json={"action": blob, "src": "discord", "id": msg_id},
            timeout=20,
        )
        emit(event="forward", id=msg_id, status=r.status_code, body=(r.text or "")[:400], raw=blob[:400])
    except Exception as e:
        emit(event="forward_err", id=msg_id, err=str(e)[:300], raw=blob[:400])


def main() -> None:
    envload()
    global TOKEN, CHANNEL_ID, HOOK
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0") or 0)
    HOOK = os.environ.get("BULLBOT_HOOK", "http://127.0.0.1:8788/tv/signal")
    if not TOKEN or not CHANNEL_ID:
        raise SystemExit("set DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID in .env")
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        emit(event="discord_ready", user=str(client.user), channel=CHANNEL_ID, hook=HOOK)

    @client.event
    async def on_message(msg: discord.Message):
        if msg.channel.id != CHANNEL_ID:
            return
        if msg.author.id == client.user.id:
            return
        forward(blob_of(msg), msg.id)

    client.run(TOKEN)


if __name__ == "__main__":
    main()
