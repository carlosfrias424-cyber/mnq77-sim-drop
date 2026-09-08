#!/usr/bin/env python3
"""Post-RTH adjustment log.

  python adjust.py --show
  python adjust.py P1 lock_clear_on_flat do
  python adjust.py --done P1
"""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
OUT = ROOT / "logs/adjustments.jsonl"
TZ = ZoneInfo("America/Chicago")

SEED = [
    dict(id="P1", pri=1, item="lock_clear_on_flat",
         why="submit.lock lives 16h after fill. 10:38 W → flat 11:45 → afternoon dead.",
         do="Delete lock when Tradovate net=0 (or BE/TP/SL flatten). One OPEN position, not one fire/day."),
    dict(id="P2", pri=1, item="spent_fill_scope",
         why="spent_fill on 29595 never cleared. Price sat on rail → filled_lock all afternoon.",
         do="Clear spent_fill when flat OR price leaves 10 pts. Keep it only to block revenge on the same sweep."),
    dict(id="P3", pri=1, item="stop_through_rail",
         why="06:16 Buy H4H 29540 filled 29558. Stop 29538 = 1 pt through the rail. Swept by a tick, then +150.",
         do="Skip bounce if entry-20 sits through zone_lo (fade: entry+20 through zone_hi). Don't fire a stretched HL."),
    dict(id="P4", pri=2, item="review_0820_fade",
         why="08:20 Sell H4H 29680 was THE fade; waterfall. Not fired. Miss log tagged.",
         do="Pull seven.jsonl 08:10–08:35. Score vol / LH / tape / visit. Patch only if a real hole."),
    dict(id="P5", pri=3, item="stop_plus_tick",
         why="06:16 MAE mid 18.50, wick took 20.00. Not decided.",
         do="Park. Don't widen to 21 until we see more than one tick-stop. P3 first."),
]


def load():
    rows = []
    if OUT.exists():
        for ln in OUT.open():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
    return rows


def write(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))


def seed():
    have = {r.get("id") for r in load()}
    rows = load()
    n = 0
    for s in SEED:
        if s["id"] in have:
            continue
        rec = dict(s, ts=int(datetime.now(TZ).timestamp() * 1000),
                   day=datetime.now(TZ).strftime("%Y-%m-%d"), status="open")
        rows.append(rec)
        n += 1
    write(rows)
    print("seeded", n, "total", len(rows))


def show():
    rows = load()
    if not rows:
        print("empty — run: python adjust.py --seed")
        return
    for r in rows:
        print(f"{r.get('id')}  {r.get('status','?'):<6} pri={r.get('pri')}  {r.get('item')}")
        print(f"     why: {r.get('why')}")
        print(f"     do:  {r.get('do')}")


def add(args):
    item = args[0]
    why = args[1] if len(args) > 1 else ""
    do = " ".join(args[2:]) if len(args) > 2 else ""
    rows = load()
    n = 1 + sum(1 for r in rows if str(r.get("id","")).startswith("P"))
    rec = dict(id=f"P{n}", pri=2, item=item, why=why, do=do,
               ts=int(datetime.now(TZ).timestamp()*1000),
               day=datetime.now(TZ).strftime("%Y-%m-%d"), status="open")
    rows.append(rec)
    write(rows)
    print(json.dumps(rec))


def done(pid):
    rows = load()
    ok = False
    for r in rows:
        if r.get("id") == pid:
            r["status"] = "done"
            r["done_ts"] = int(datetime.now(TZ).timestamp()*1000)
            ok = True
    write(rows)
    print("done" if ok else "not found", pid)


def main():
    args = sys.argv[1:] or ["--show"]
    if args[0] in ("--show", "show"):
        show()
    elif args[0] == "--seed":
        seed()
        show()
    elif args[0] == "--done" and len(args) > 1:
        done(args[1])
        show()
    else:
        add(args)


if __name__ == "__main__":
    main()
