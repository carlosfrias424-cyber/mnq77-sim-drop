#!/usr/bin/env python3
"""Score the 8 circled 7/7s from the 1m chart, current rules, then 40 TP / rail+2 / BE+20."""
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
    ("09:22 194 reclaim", 9, 22, 29194.0, "Buy"),
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


def load_bars(day):
    bars = {}
    d5 = 0.0
    p = ROOT / "decision.jsonl"
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
        if o.get("delta_5s") is not None:
            try:
                d5 = float(o["delta_5s"])
            except Exception:
                pass
        t0 = dt.replace(second=0, microsecond=0)
        b = bars.get(t0)
        if b is None:
            bars[t0] = [px, px, px, px, d5]
        else:
            b[1] = max(b[1], px)
            b[2] = min(b[2], px)
            b[3] = px
            b[4] = d5
    return [(t, *bars[t]) for t in sorted(bars)]


def path_pnl(closed, i, side, entry, sl, tp):
    be = False
    mfe = mae = 0.0
    for t, _o, h, l, c, _d in closed[i:]:
        if side == "Buy":
            mfe = max(mfe, h - entry)
            mae = max(mae, entry - l)
            if (not be) and h >= entry + BE:
                be = True
                sl = entry
            if l <= sl:
                return ("BE" if be else "SL"), sl, mfe, mae, t
            if h >= entry + tp:
                return "TP", entry + tp, mfe, mae, t
        else:
            mfe = max(mfe, entry - l)
            mae = max(mae, h - entry)
            if (not be) and l <= entry - BE:
                be = True
                sl = entry
            if h >= sl:
                return ("BE" if be else "SL"), sl, mfe, mae, t
            if l <= entry - tp:
                return "TP", entry - tp, mfe, mae, t
    t, _o, h, l, c, _d = closed[-1]
    px = c
    return "OPEN", px, mfe, mae, t


def main():
    now = datetime.now(TZ)
    day = now.replace(hour=2, minute=0, second=0, microsecond=0)
    closed = load_bars(day)
    by = {t: i for i, (t, *_) in enumerate(closed)}
    print(f"1m bars {len(closed)}")
    print(f"{'circle':<18} {'c1':<5} {'c2':<6} {'hold':<4} {'hl':<4} {'tape':<4} {'fire':<4} {'hit':<4} {'pts':>6} {'$':>6} why")
    net = 0.0
    nfire = 0
    for label, hh, mm, px, want in CIRCLES:
        t1 = day.replace(hour=hh, minute=mm)
        # C1 = that minute, C2 = next
        if t1 not in by:
            # nearest
            cand = min(by, key=lambda t: abs((t - t1).total_seconds())) if by else None
            if cand is None or abs((cand - t1).total_seconds()) > 120:
                print(f"{label:<18} NO 1m BAR")
                continue
            t1 = cand
        i = by[t1]
        if i + 1 >= len(closed):
            print(f"{label:<18} no C2")
            continue
        _t, o1, h1, l1, c1, d1 = closed[i]
        t2, o2, h2, l2, c2, d2 = closed[i + 1]
        bounce = want == "Buy"
        zlo, zhi = px - ARM, px + ARM
        hit = (zlo <= l1 <= zhi) or (zlo <= h1 <= zhi) or (l1 <= px <= h1)
        close_ok = (c1 >= px) if bounce else (c1 <= px)
        tape1 = (d1 > 0) if bounce else (d1 < 0)
        hold = (c2 >= px) if bounce else (c2 <= px)
        hl = (l2 > l1) if bounce else (h2 < h1)
        recut = (l2 <= l1) if bounce else (h2 >= h1)
        tape2 = (d2 > 0) if bounce else (d2 < 0)
        why = []
        if not hit:
            why.append("c1_miss_rail")
        if not close_ok:
            why.append("c1_close_wrong_side")
        if not tape1:
            why.append("c1_tape")
        if recut:
            why.append("c2_recut")
        if not hl:
            why.append("c2_no_hl")
        if not hold:
            why.append("c2_no_hold")
        if not tape2:
            why.append("c2_tape")
        fire = not why
        hitn = pts = dol = ""
        if fire:
            nfire += 1
            stop = qtr(zlo - AIR) if bounce else qtr(zhi + AIR)
            r = qtr((c2 - stop) if bounce else (stop - c2))
            tp = TP if r <= 20 else qtr(2 * r)
            side = "Buy" if bounce else "Sell"
            res, exit_px, mfe, mae, tdone = path_pnl(closed, i + 2, side, c2, stop, tp)
            p = (exit_px - c2) if side == "Buy" else (c2 - exit_px)
            net += p
            hitn, pts, dol = res, f"{p:.1f}", f"{p*2*QTY:.0f}"
            why = [f"entry {c2:.2f} stop {stop:.2f} MFE {mfe:.1f} MAE {mae:.1f}"]
        print(
            f"{label:<18} {str(hit):<5} {t2:%H:%M} {str(hold):<4} {str(hl):<4} "
            f"{str(tape2):<4} {str(fire):<4} {hitn:<4} {pts:>6} {dol:>6} {','.join(why) or 'ok'}"
        )
    print(f"\nwould fire {nfire}/8   NET {net:.1f} pts   ${net*2*QTY:.0f}   (only the fires, 3 MNQ)")


if __name__ == "__main__":
    main()
