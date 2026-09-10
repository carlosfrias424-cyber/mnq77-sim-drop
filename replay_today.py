#!/usr/bin/env python3
"""Replay today with CURRENT 7/7: no BRT, no 2-min, watch 10, arm 6,
C2 HL/LH + hold + tape, stop rail+2, TP 40 or 2R, BE +20. 3 MNQ.
Re-arms a rail when TV pings it again after price left watch.
Skips walking ONH/ONL."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH, ARM, FAIL, CLUSTER, AIR = 10.0, 6.0, 6.0, 8.0, 2.0
TP, BE, QTY = 40.0, 20.0, 3
SKIP = ("ONH", "ONL")


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


def dist(px, lo, hi):
    if lo <= px <= hi:
        return 0.0
    return px - hi if px > hi else lo - px


def vk(px):
    return f"{round(float(px) / CLUSTER) * CLUSTER:.2f}"


def lean(bounce, d):
    return (d > 0) if bounce else (d < 0)


def load_bars(day):
    bars = {}
    d5 = 0.0
    p = ROOT / "decision.jsonl"
    if not p.exists():
        return []
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


def load_pings(day):
    """Every webhook after 2am. Walking ONH/ONL dropped."""
    out = []
    p = ROOT / "tv_poi.jsonl"
    if not p.exists():
        return out
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
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        name = str(o.get("poi_name") or o.get("type") or "H1")
        tag = name.upper()
        if any(tag.startswith(s) for s in SKIP):
            continue
        out.append((dt, name, px))
    return out


def main():
    now = datetime.now(TZ)
    day = now.replace(hour=2, minute=0, second=0, microsecond=0)
    closed = load_bars(day)
    pings = load_pings(day)
    uniq = {}
    for dt, name, px in pings:
        uniq[(name, px)] = dt
    print(f"day {day:%Y-%m-%d %H:%M %Z}  1m {len(closed)}  pings {len(pings)}  rails {len(uniq)}")
    if uniq:
        last = max(uniq.items(), key=lambda kv: kv[1])
        print("last unique", last[1].strftime("%H:%M"), last[0][0], last[0][1])

    book = None
    dead = set()
    left = {}
    last_ping = {}
    trades = []
    pi = 0

    for i in range(1, len(closed)):
        t0, _o, h, l, c, d = closed[i]
        _t1, _o1, h1, l1, c1, d1 = closed[i - 1]
        ts = t0.timestamp()
        while pi < len(pings) and pings[pi][0] <= t0:
            pdt, name, px = pings[pi]
            pi += 1
            k = f"{name}@{px:.2f}"
            last_ping[k] = (pdt.timestamp(), name, px)

        sticky = {}
        for k, (pts, name, px) in last_ping.items():
            if dist(px, l, h) > WATCH:
                left[k] = ts
                dead.discard(vk(px))
                continue
            if pts <= left.get(k, 0):
                continue
            sticky[k] = (name, px)
        for k in list(left):
            if k in sticky:
                pass
            else:
                # still gone
                pass

        if book:
            side, entry, sl, tp = book["side"], book["entry"], book["sl"], book["tp"]
            be_on = book["be"]
            orig = book["orig_sl"]
            if side == "Buy":
                mfe, mae = h - entry, entry - l
                if (not be_on) and h >= entry + BE:
                    book["be"] = True
                    book["sl"] = entry
                    sl = entry
                    be_on = True
                hit = exit_px = None
                if l <= sl:
                    hit, exit_px = ("BE" if be_on else "SL"), sl
                elif h >= entry + tp:
                    hit, exit_px = "TP", entry + tp
            else:
                mfe, mae = entry - l, h - entry
                if (not be_on) and l <= entry - BE:
                    book["be"] = True
                    book["sl"] = entry
                    sl = entry
                    be_on = True
                hit = exit_px = None
                if h >= sl:
                    hit, exit_px = ("BE" if be_on else "SL"), sl
                elif l <= entry - tp:
                    hit, exit_px = "TP", entry - tp
            book["mfe"] = max(book["mfe"], mfe)
            book["mae"] = max(book["mae"], mae)
            if hit:
                trades.append({**book, "exit": exit_px, "hit": hit, "t1": t0, "sl": orig})
                book = None
            continue

        cands = []
        for k, (name, px) in sticky.items():
            if vk(px) in dead:
                continue
            dd = dist(px, l, h)
            if dd <= WATCH:
                cands.append((dd, name, px))
        if not cands:
            continue
        cands.sort()
        name, px = cands[0][1], cands[0][2]
        bounce = abs(l1 - px) <= abs(h1 - px)
        zlo, zhi = px - ARM, px + ARM
        hit = (zlo <= l1 <= zhi) if bounce else (zlo <= h1 <= zhi)
        if not hit:
            continue
        if bounce and c1 < px:
            continue
        if (not bounce) and c1 > px:
            continue
        if not lean(bounce, d1):
            continue
        hold = (c >= px) if bounce else (c <= px)
        hl = (l > l1) if bounce else (h < h1)
        recut = (l <= l1) if bounce else (h >= h1)
        through = (c < zlo - FAIL) if bounce else (c > zhi + FAIL)
        if recut or through or not hl or not hold or not lean(bounce, d):
            dead.add(vk(px))
            continue
        stop = qtr(zlo - AIR) if bounce else qtr(zhi + AIR)
        r = qtr((c - stop) if bounce else (stop - c))
        tp = TP if r <= 20 else qtr(2 * r)
        side = "Buy" if bounce else "Sell"
        book = dict(
            t=t0, side=side, entry=c, sl=stop, orig_sl=stop, r=r, tp=tp,
            poi=f"{name}@{px}", be=False, mfe=0.0, mae=0.0,
        )
        dead.add(vk(px))

    if book:
        t0, _o, h, l, c, d = closed[-1]
        trades.append({**book, "exit": c, "hit": "OPEN", "t1": t0, "sl": book["orig_sl"]})

    print(f"{'when':<8} {'side':<4} {'entry':>8} {'stop':>8} {'tp':>6} {'hit':<4} {'pts':>7} {'$':>7}  poi")
    net = 0.0
    for tr in trades:
        p = (tr["exit"] - tr["entry"]) if tr["side"] == "Buy" else (tr["entry"] - tr["exit"])
        net += p
        dol = p * 2 * QTY
        print(
            f"{tr['t']:%H:%M}    {tr['side']:<4} {tr['entry']:8.2f} {tr['sl']:8.2f} "
            f"{tr['tp']:6.1f} {tr['hit']:<4} {p:7.1f} {dol:7.0f}  {tr['poi']}"
        )
    print(f"\ntrades {len(trades)}  NET {net:.1f} pts   ${net * 2 * QTY:.0f}   (3 MNQ, $2/pt, BE+20)")


if __name__ == "__main__":
    main()
