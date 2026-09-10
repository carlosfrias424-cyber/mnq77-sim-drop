#!/usr/bin/env python3
"""Score the 8 circled trades using LIVE 7/7 1m bars (Databento high/low from seven.jsonl),
NOT mids. BRT is not a setup — 09:22 reclaim is listed only because it was circled."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
ARM, AIR, TP, BE, QTY = 6.0, 2.0, 40.0, 20.0, 3

CIRCLES = [
    ("08:00 Buy 047", 8, 0, 29047.0, "Buy"),
    ("08:22 Buy 047", 8, 22, 29047.0, "Buy"),
    ("08:45 Sell 194", 8, 45, 29194.0, "Sell"),
    ("08:55 Buy OPEN", 8, 55, 29089.5, "Buy"),
    ("09:22 reclaim 194", 9, 22, 29194.0, "Buy"),
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


def qtr(x):
    return round(round(float(x) / 0.25) * 0.25, 2)


def load_seven_1m(day):
    """Last seven.jsonl row per minute: real 1m hi/lo from Databento, plus delta."""
    by = {}
    p = ROOT / "seven.jsonl"
    if not p.exists():
        return by
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt or dt < day:
            continue
        t0 = dt.replace(second=0, microsecond=0)
        bar = o.get("bar") or o.get("snap", {}).get("bar")
        hi = lo = None
        if isinstance(bar, (list, tuple)) and len(bar) >= 2:
            try:
                lo, hi = float(bar[0]), float(bar[1])
            except Exception:
                pass
        mid = o.get("mid")
        d5 = o.get("delta_5s")
        if d5 is None:
            d5 = (o.get("vol") or {}).get("delta_5s")
        snap = o.get("snap") or {}
        if d5 is None:
            d5 = snap.get("delta_5s")
        lean = o.get("tape_lean")
        if lean is None:
            lean = snap.get("tape_lean")
        tag = o.get("tag") or snap.get("tag")
        by[t0] = dict(t=t0, hi=hi, lo=lo, mid=mid, d5=d5, lean=lean, tag=tag, raw=o)
    return by


def load_mids(day):
    bars = {}
    p = ROOT / "decision.jsonl"
    if not p.exists():
        return bars
    last = None
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt or dt < day:
            continue
        try:
            px = float(o.get("last") or o.get("px") or o.get("mid"))
        except Exception:
            continue
        d5 = o.get("delta_5s")
        t0 = dt.replace(second=0, microsecond=0)
        b = bars.get(t0)
        if b is None:
            bars[t0] = [px, px, px, px, d5]
        else:
            b[1] = max(b[1], px)
            b[2] = min(b[2], px)
            b[3] = px
            if d5 is not None:
                b[4] = d5
        last = (t0, px)
    return bars


def path_pnl(mids, start, side, entry, sl, tp):
    times = sorted(t for t in mids if t >= start)
    be = False
    mfe = mae = 0.0
    for t in times:
        _o, h, l, c, _d = mids[t]
        if side == "Buy":
            mfe = max(mfe, h - entry)
            mae = max(mae, entry - l)
            if (not be) and h >= entry + BE:
                be, sl = True, entry
            if l <= sl:
                return ("BE" if be else "SL"), sl, mfe, mae
            if h >= entry + tp:
                return "TP", entry + tp, mfe, mae
        else:
            mfe = max(mfe, entry - l)
            mae = max(mae, h - entry)
            if (not be) and l <= entry - BE:
                be, sl = True, entry
            if h >= sl:
                return ("BE" if be else "SL"), sl, mfe, mae
            if l <= entry - tp:
                return "TP", entry - tp, mfe, mae
    return "OPEN", entry, mfe, mae


def main():
    now = datetime.now(TZ)
    day = now.replace(hour=2, minute=0, second=0, microsecond=0)
    s1 = load_seven_1m(day)
    mids = load_mids(day)
    print(f"seven 1m minutes {len(s1)}  (hi/lo from live bot, not mid)")
    print(f"{'circle':<18} {'tag':<6} {'c1':<5} {'hold':<4} {'hl':<4} {'tape':<4} {'fire':<4} {'hit':<4} {'pts':>6} why")
    net = 0.0
    nfire = 0
    times = sorted(s1)
    for label, hh, mm, px, want in CIRCLES:
        t1 = day.replace(hour=hh, minute=mm)
        if t1 not in s1:
            near = min(times, key=lambda t: abs((t - t1).total_seconds())) if times else None
            if near is None or abs((near - t1).total_seconds()) > 90:
                print(f"{label:<18} NO LIVE 1m")
                continue
            t1 = near
        i = times.index(t1)
        if i + 1 >= len(times):
            print(f"{label:<18} no C2")
            continue
        a, b = s1[times[i]], s1[times[i + 1]]
        hi1, lo1 = a.get("hi"), a.get("lo")
        hi2, lo2 = b.get("hi"), b.get("lo")
        if None in (hi1, lo1, hi2, lo2):
            print(f"{label:<18} no hi/lo in seven row  mid={a.get('mid')}")
            continue
        bounce = want == "Buy"
        # live rule: bounce tags LOW, fade tags HIGH
        tag_px = lo1 if bounce else hi1
        hit = abs(tag_px - px) <= ARM or (lo1 <= px <= hi1)
        c1 = a.get("mid")
        c2 = b.get("mid")
        close_ok = True
        if c1 is not None:
            close_ok = (c1 >= px) if bounce else (c1 <= px)
        hold = (c2 >= px) if (bounce and c2 is not None) else ((c2 <= px) if c2 is not None else False)
        hl = (lo2 > lo1) if bounce else (hi2 < hi1)
        recut = (lo2 <= lo1) if bounce else (hi2 >= hi1)
        tape = a.get("lean")
        if tape is None and a.get("d5") is not None:
            tape = (a["d5"] > 0) if bounce else (a["d5"] < 0)
        tape2 = b.get("lean")
        if tape2 is None and b.get("d5") is not None:
            tape2 = (b["d5"] > 0) if bounce else (b["d5"] < 0)
        why = []
        if not hit:
            why.append(f"c1_miss tag={tag_px:.2f}")
        if not close_ok:
            why.append("c1_close_thru")
        if tape is False:
            why.append("c1_tape")
        if recut:
            why.append("c2_recut")
        if not hl:
            why.append("c2_no_hl")
        if not hold:
            why.append("c2_no_hold")
        if tape2 is False:
            why.append("c2_tape")
        fire = not why
        hitn = pts = ""
        if fire:
            nfire += 1
            entry = float(c2)
            stop = qtr((px - ARM - AIR) if bounce else (px + ARM + AIR))
            r = qtr((entry - stop) if bounce else (stop - entry))
            tp = TP if r <= 20 else qtr(2 * r)
            side = "Buy" if bounce else "Sell"
            res, exit_px, mfe, mae = path_pnl(mids, times[i + 1], side, entry, stop, tp)
            p = (exit_px - entry) if side == "Buy" else (entry - exit_px)
            net += p
            hitn, pts = res, f"{p:.1f}"
            why = [f"entry {entry:.2f} MFE {mfe:.1f} MAE {mae:.1f}"]
        print(
            f"{label:<18} {str(a.get('tag') or ('low' if bounce else 'high')):<6} "
            f"{str(hit):<5} {str(hold):<4} {str(hl):<4} {str(tape2):<4} {str(fire):<4} "
            f"{hitn:<4} {pts:>6} {','.join(why) or 'ok'}"
        )
    print(f"\nwould fire {nfire}/8   NET {net:.1f} pts   ${net*2*QTY:.0f}")
    print("tag=low/high is LIVE 1m wick, not mid. reclaim row is a skip — BRT is off.")


if __name__ == "__main__":
    main()
