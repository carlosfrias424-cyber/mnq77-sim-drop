#!/usr/bin/env python3
"""Dump today's raw TV webhook names so we can see H4H vs H4L at each price."""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

POI = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs/tv_poi.jsonl")
TZ = ZoneInfo("America/Chicago")
FOCUS = (29273.5, 29202.5, 29160.25, 29095.5, 29042.5, 29001.75, 28947.75, 28906.0)


def dt_of(o):
    t = o.get("ts") or o.get("recv_ts")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            return None
    try:
        t = int(float(t))
    except Exception:
        return None
    if t < 1e11:
        return None
    if t < 1e12:
        t *= 1000
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(TZ)


def main():
    now = datetime.now(TZ)
    day0 = datetime(now.year, now.month, now.day, 0, 0, tzinfo=TZ)
    if now.hour < 4:
        day0 -= timedelta(days=1)
    day1 = day0 + timedelta(days=1)
    by = defaultdict(list)
    if not POI.exists():
        print("MISSING", POI)
        return
    n = 0
    for ln in POI.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt is None or dt < day0 or dt >= day1:
            continue
        n += 1
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        name = str(o.get("poi_name") or "")
        typ = str(o.get("type") or "")
        tf = str(o.get("tf") or "")
        by[(name, typ, px, tf)].append(dt)
    print("day", day0.date(), "alerts", n, "unique", len(by))
    print(f"\n{'name':<12} {'type':<12} {'tf':<6} {'px':>10} {'n':>4} first last")
    for (name, typ, px, tf), dts in sorted(by.items(), key=lambda kv: -kv[0][2]):
        dts.sort()
        print(f"{name:<12} {typ:<12} {tf:<6} {px:10.2f} {len(dts):4} {dts[0]:%H:%M} {dts[-1]:%H:%M}")
    print("\n=== around focus rails (±2 pts) ===")
    for want in FOCUS:
        hits = [(k, v) for k, v in by.items() if abs(k[2] - want) <= 2]
        print(f"\n-- {want} --")
        if not hits:
            print("  no webhook")
            continue
        for (name, typ, px, tf), dts in sorted(hits, key=lambda kv: kv[0][2]):
            print(f"  {name}/{typ} tf={tf} px={px} n={len(dts)} {dts[0]:%H:%M}-{dts[-1]:%H:%M}")


if __name__ == "__main__":
    main()
