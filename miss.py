#!/usr/bin/env python3
"""Running log of missed trades. Manual tag + optional pull of 7/7 skips.

  python miss.py 08:20 Sell H4H 29680 fade_the_high
  python miss.py --from-seven          # today's skips/passes from seven.jsonl
  python miss.py --show
"""
from __future__ import annotations
import json, sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
OUT = ROOT / "logs/missed.jsonl"
SEVEN = ROOT / "logs/seven.jsonl"
TZ = ZoneInfo("America/Chicago")


def now():
    return datetime.now(TZ)


def append(**kw):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": int(now().timestamp() * 1000), "day": now().strftime("%Y-%m-%d"), **kw}
    OUT.open("a").write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


def show():
    if not OUT.exists():
        print("empty")
        return
    for ln in OUT.open():
        if ln.strip():
            print(ln.rstrip())


def from_seven():
    day = now().date()
    if not SEVEN.exists():
        print("no seven.jsonl")
        return
    n = 0
    for ln in SEVEN.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        ev = o.get("event")
        if ev not in ("paper_fire", "struct40_submit", "struct40_fail"):
            continue
        t = o.get("ts")
        try:
            t = int(t)
            if t > 1e12:
                t //= 1000
            dt = datetime.fromtimestamp(t, TZ)
        except Exception:
            continue
        if dt.date() != day:
            continue
        skip = o.get("skip")
        if ev == "struct40_submit" and not skip:
            continue
        snap = o.get("snap") or {}
        append(
            src="seven",
            when=dt.strftime("%H:%M:%S"),
            side=o.get("side") or snap.get("side"),
            poi=o.get("poi"),
            mid=o.get("mid"),
            event=ev,
            skip=skip,
            reason=o.get("reason") or snap.get("reason"),
            hold=o.get("hold"), hl=o.get("hl_lh"), lean=o.get("tape_lean"),
        )
        n += 1
    print("pulled", n)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--show", "show"):
        show()
        return
    if args[0] == "--from-seven":
        from_seven()
        return
    # miss.py HH:MM Side Rail Px note...
    when = args[0]
    side = args[1] if len(args) > 1 else ""
    rail = args[2] if len(args) > 2 else ""
    px = args[3] if len(args) > 3 else ""
    note = " ".join(args[4:]) if len(args) > 4 else ""
    try:
        px_f = float(px)
    except Exception:
        px_f = px
    append(src="manual", when=when, side=side, rail=rail, px=px_f, note=note)


if __name__ == "__main__":
    main()
