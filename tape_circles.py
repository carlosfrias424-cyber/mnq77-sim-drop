#!/usr/bin/env python3
"""Tape around the 8 circled trades. 1m buckets from decision.jsonl + seven.jsonl."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
CIRCLES = [
    ("08:00 Buy 047", 8, 0, 29047.0, "Buy"),
    ("08:22 Buy 047", 8, 22, 29047.0, "Buy"),
    ("08:45 Sell 194", 8, 45, 29194.0, "Sell"),
    ("08:55 Buy OPEN", 8, 55, 29089.5, "Buy"),
    ("09:22 194", 9, 22, 29194.0, "Buy"),
    ("09:38 Sell 273", 9, 38, 29273.5, "Sell"),
    ("10:18 Buy 194", 10, 18, 29194.0, "Buy"),
    ("10:38 Sell 273", 10, 38, 29273.5, "Sell"),
]


def dt_of(o):
    t = o.get("recv_ts") or o.get("ts")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            return None
    try:
        t = float(t)
    except Exception:
        return None
    if t > 1e12:
        t /= 1000.0
    if t < 1e9:
        return None
    return datetime.fromtimestamp(t, timezone.utc).astimezone(TZ)


def main():
    now = datetime.now(TZ)
    day = now.replace(hour=2, minute=0, second=0, microsecond=0)
    tmin = day.replace(hour=7, minute=50)
    tmax = day.replace(hour=10, minute=50)

    dec = {}  # minute -> stats
    p = ROOT / "decision.jsonl"
    n = 0
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("event") not in (None, "decision", "dual"):
            if o.get("event") not in ("decision",):
                # keep decision rows; also rows with mid+delta
                if o.get("mid") is None and o.get("last") is None:
                    continue
        dt = dt_of(o)
        if not dt or dt < tmin or dt > tmax:
            continue
        t0 = dt.replace(second=0, microsecond=0)
        try:
            px = float(o.get("last") or o.get("px") or o.get("mid"))
        except Exception:
            continue
        b = dec.get(t0)
        if b is None:
            b = dict(n=0, o=px, h=px, l=px, c=px, d5=[], hb=0, ha=0, dl=0, ds=0)
            dec[t0] = b
        b["n"] += 1
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if o.get("delta_5s") is not None:
            try:
                b["d5"].append(float(o["delta_5s"]))
            except Exception:
                pass
        if o.get("hold_bid"):
            b["hb"] += 1
        if o.get("hold_ask"):
            b["ha"] += 1
        if o.get("d_long"):
            b["dl"] += 1
        if o.get("d_short"):
            b["ds"] += 1
        n += 1

    sev = {}
    sp = ROOT / "seven.jsonl"
    if sp.exists():
        for ln in sp.open():
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            dt = dt_of(o)
            if not dt or dt < tmin or dt > tmax:
                continue
            t0 = dt.replace(second=0, microsecond=0)
            sev[t0] = o

    print(f"decision minutes {len(dec)} rows {n}  seven minutes {len(sev)}")
    for label, hh, mm, px, side in CIRCLES:
        t0 = day.replace(hour=hh, minute=mm)
        print(f"\n==== {label} rail {px} {side} ====")
        print("min    lo      hi      last    d5avg  d5end  hb%  ha%  dL%  dS%  lean  seven")
        for k in range(-2, 4):
            t = t0 + timedelta(minutes=k)
            b = dec.get(t)
            s = sev.get(t) or {}
            mark = "<" if k == 0 else " "
            if not b:
                print(f"{t:%H:%M}{mark} NO DECISION  seven={s.get('reason')} poi={s.get('poi')}")
                continue
            nn = max(b["n"], 1)
            d5s = b["d5"]
            avg = sum(d5s) / len(d5s) if d5s else 0.0
            end = d5s[-1] if d5s else 0.0
            lean = "BUY" if end > 0 else ("SELL" if end < 0 else "flat")
            want = "BUY" if side == "Buy" else "SELL"
            ok = "WITH" if lean == want else "AGAINST"
            print(
                f"{t:%H:%M}{mark} {b['l']:7.2f} {b['h']:7.2f} {b['c']:7.2f} "
                f"{avg:6.0f} {end:6.0f} {100*b['hb']/nn:3.0f} {100*b['ha']/nn:3.0f} "
                f"{100*b['dl']/nn:3.0f} {100*b['ds']/nn:3.0f} {lean:4} {ok:7} "
                f"{s.get('reason') or '-'} tag={s.get('tag')} bar={s.get('bar')}"
            )


if __name__ == "__main__":
    main()
