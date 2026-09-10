#!/usr/bin/env python3
"""Today 7/7 PNL + which fires body_gave would kill. Run on the box."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
DAY0 = datetime(2026, 9, 10, 2, 0, tzinfo=CDT)
DAY1 = datetime(2026, 9, 10, 16, 0, tzinfo=CDT)
BE = 20.0
TP_DEF = 40.0
AIR = 2.0
QTY = 3
DOLLAR = 2.0 * QTY  # $ / pt


def dt_of(o):
    t = o.get("ts") or o.get("recv_ts")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CDT)
        except Exception:
            return None
    try:
        t = int(float(t))
    except Exception:
        return None
    if t < 1e12:
        t *= 1000
    if t < 1e11:
        return None
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(CDT)


def load(name):
    p = ROOT / name
    out = []
    if not p.exists():
        print("MISSING", name)
        return out
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt is None or dt < DAY0 or dt > DAY1:
            continue
        o["_dt"] = dt
        out.append(o)
    return out


def poi_px(o):
    for k in ("px", "poi"):
        v = o.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and "@" in v:
            try:
                return float(v.split("@")[-1].split(":")[0])
            except Exception:
                pass
    snap = o.get("snap") or {}
    return None


def side_of(o):
    s = o.get("side") or (o.get("snap") or {}).get("side")
    if s in ("Buy", "Sell"):
        return s
    bounce = o.get("bounce")
    if bounce is True:
        return "Buy"
    if bounce is False:
        return "Sell"
    pic = str((o.get("snap") or {}).get("picture") or o.get("picture") or "")
    if "bounce" in pic:
        return "Buy"
    if "fade" in pic:
        return "Sell"
    tag = o.get("tag")
    if tag == "low":
        return "Buy"
    if tag == "high":
        return "Sell"
    return None


def walk(mids, t0, side, entry, stop_px, tp_pts):
    """BE at +20, then stop=entry. First touch of stop/BE/TP."""
    mae = mfe = 0.0
    be_on = False
    hit = "OPEN"
    exit_px = None
    t_hit = None
    for dt, mid in mids:
        if dt <= t0:
            continue
        if side == "Buy":
            pnl = mid - entry
            stop_now = entry if be_on else stop_px
            sl = mid <= stop_now
            tp = mid >= entry + tp_pts
        else:
            pnl = entry - mid
            stop_now = entry if be_on else stop_px
            sl = mid >= stop_now
            tp = mid <= entry - tp_pts
        mae = min(mae, pnl)
        mfe = max(mfe, pnl)
        if (not be_on) and mfe >= BE:
            be_on = True
            continue
        if tp:
            hit, exit_px, t_hit = "TP", entry + tp_pts if side == "Buy" else entry - tp_pts, dt
            break
        if sl:
            hit = "BE" if be_on else "SL"
            exit_px, t_hit = stop_now, dt
            break
    pts = None
    if hit == "TP":
        pts = tp_pts
    elif hit == "BE":
        pts = 0.0
    elif hit == "SL":
        pts = (exit_px - entry) if side == "Buy" else (entry - exit_px)
    return hit, mae, mfe, be_on, pts, t_hit


seven = load("seven.jsonl")
dec = load("decision.jsonl")
mids = []
for o in dec:
    try:
        mids.append((o["_dt"], float(o.get("mid"))))
    except Exception:
        pass
mids.sort()
print("decision mids", len(mids), "seven rows", len(seven))

fires = [o for o in seven if o.get("event") in ("struct40_submit", "paper_fire") or o.get("submit")]
gave = [o for o in seven if o.get("reason") == "body_gave_rail" or o.get("event") == "body_gave_rail"]
print("\n=== body_gave_rail (new rule kills) ===", len(gave))
for o in gave[-12:]:
    print(o["_dt"].strftime("%H:%M:%S"), o.get("poi"), "c", (o.get("c1") or {}).get("c"), "mid", o.get("mid"))

print("\n=== fires / paper goes ===")
hdr = f"{'when':<8} {'src':<8} {'side':<4} {'entry':>8} {'poi':<28} {'R':>5} {'tp':>5} {'hit':<5} {'MAE':>6} {'MFE':>6} {'pts':>7} {'$':>8} note"
print(hdr)
tot = 0.0
n_sl = n_tp = n_be = n_open = 0
rows = []
for o in fires:
    dt = o["_dt"]
    side = side_of(o)
    entry = o.get("mid")
    try:
        entry = float(entry)
    except Exception:
        continue
    if side is None:
        px = poi_px(o)
        side = "Buy" if px is None or entry >= (px or entry) else "Sell"
    px = poi_px(o) or entry
    bounce = side == "Buy"
    stop_px = o.get("stop_px")
    stop_pts = o.get("stop_pts")
    tp_pts = o.get("tp_pts") or TP_DEF
    try:
        stop_px = float(stop_px) if stop_px is not None else (px - AIR if bounce else px + AIR)
        stop_pts = float(stop_pts) if stop_pts is not None else abs(entry - stop_px)
        tp_pts = float(tp_pts)
    except Exception:
        stop_px = px - AIR if bounce else px + AIR
        stop_pts = abs(entry - stop_px)
        tp_pts = TP_DEF
    skip = o.get("skip")
    ev = o.get("event")
    src = "fill" if ev == "struct40_submit" or o.get("submit") else "paper"
    # body-through in prior 20m on this rail?
    killed = False
    for g in gave:
        if abs((g["_dt"] - dt).total_seconds()) < 1800 and str(g.get("poi") or "")[:20] == str(o.get("poi") or "")[:20] and g["_dt"] <= dt:
            killed = True
            break
    # also: any 1m close through rail in 15m before entry (from seven scores)
    hit, mae, mfe, be_on, pts, t_hit = walk(mids, dt, side, entry, stop_px, tp_pts)
    if skip:
        note = f"skip={skip}"
        pts = 0.0
        hit = "SKIP"
    elif killed and dt >= datetime(2026, 9, 10, 13, 37, tzinfo=CDT):
        note = "would_body_gave"
    else:
        note = "live"
        if src == "paper" and not o.get("submit"):
            note = "paper_only"
    if pts is None:
        pts = 0.0
        n_open += 1
    elif hit == "SL":
        n_sl += 1
    elif hit == "TP":
        n_tp += 1
    elif hit == "BE":
        n_be += 1
    dollar = pts * DOLLAR if hit not in ("SKIP", "OPEN") else 0.0
    if hit not in ("SKIP",) and note != "would_body_gave":
        tot += dollar
    elif note == "would_body_gave":
        dollar = 0.0
    poi = str(o.get("poi") or "")[:28]
    print(f"{dt:%H:%M:%S} {src:<8} {side:<4} {entry:8.2f} {poi:<28} {stop_pts:5.1f} {tp_pts:5.1f} {hit:<5} {mae:6.1f} {mfe:6.1f} {pts:7.1f} {dollar:8.0f} {note}")
    rows.append(hit)

print("\n=== live-book day (fills that actually went, body_gave after 13:37 zeroed) ===")
print(f"TP {n_tp}  BE {n_be}  SL {n_sl}  OPEN {n_open}   PNL ${tot:.0f}   ({tot/DOLLAR:.1f} pts x 3 MNQ)")
print("rule: stop rail+2, BE +20, TP 40 or 2R, $2/pt")
print("\n=== skip / kill reasons today (top) ===")
from collections import Counter
c = Counter(o.get("reason") for o in seven if o.get("event") == "score" and o.get("reason"))
for k, n in c.most_common(12):
    print(f"  {n:5} {k}")
