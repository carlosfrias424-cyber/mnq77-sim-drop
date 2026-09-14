#!/usr/bin/env python3
"""Paper replay: loc_side_l2_no_hl vs today's decision.jsonl + tv_poi.
Does NOT submit. Does NOT touch live_77. One-position lock simulated 20/40.
"""
from __future__ import annotations
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
DEC = ROOT / "logs/decision.jsonl"
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/replay_today.jsonl"
TZ = ZoneInfo("America/Chicago")
WATCH, STOP, TP = 10.0, 20.0, 40.0
SKIP_TAGS = ("ONH", "ONL", "EMA")


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


def bar_t0(dt, minutes):
    m = (dt.minute // minutes) * minutes
    return dt.replace(minute=m, second=0, microsecond=0)


def norm_kind(name):
    u = (name or "H1").upper()
    if u.startswith("H4"):
        return "H4"
    if u.startswith("H1"):
        return "H1"
    return name or "H1"


def loc_bounce(px, rail):
    if px < rail:
        return False
    if px > rail:
        return True
    return None


@dataclass
class Rail:
    kind: str
    px: float
    recv: float

    @property
    def key(self):
        return f"{self.kind}@{self.px:.2f}"


def load_rails(day0, day1):
    best = {}
    if not POI.exists():
        print("MISSING tv_poi.jsonl")
        return []
    for ln in POI.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt is None or dt < day0 - timedelta(hours=6) or dt > day1:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        raw = str(o.get("poi_name") or o.get("type") or "H1")
        tag = raw.upper()
        if any(tag.startswith(x) or tag == x for x in SKIP_TAGS):
            continue
        kind = norm_kind(raw)
        recv = dt.timestamp()
        k = (kind, px)
        if k not in best or recv >= best[k].recv:
            best[k] = Rail(kind, px, recv)
    return list(best.values())


def load_m1(day0, day1):
    bars = {}
    n = 0
    if not DEC.exists():
        print("MISSING decision.jsonl")
        return []
    for ln in DEC.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("mid") is None:
            continue
        dt = dt_of(o)
        if dt is None or dt < day0 or dt >= day1:
            continue
        try:
            mid = float(o.get("mid"))
        except Exception:
            continue
        hi = o.get("high") if o.get("high") is not None else o.get("ask")
        lo = o.get("low") if o.get("low") is not None else o.get("bid")
        try:
            hi = float(hi) if hi is not None else mid
            lo = float(lo) if lo is not None else mid
        except Exception:
            hi = lo = mid
        d5 = o.get("delta_5s")
        try:
            d5 = float(d5) if d5 is not None else None
        except Exception:
            d5 = None
        t0 = bar_t0(dt, 1)
        b = bars.get(t0)
        if b is None:
            bars[t0] = dict(t0=t0, o=mid, h=max(hi, mid), l=min(lo, mid), c=mid,
                            d5=d5, dl=bool(o.get("d_long")), ds=bool(o.get("d_short")), n=1)
        else:
            b["h"] = max(b["h"], hi, mid)
            b["l"] = min(b["l"], lo, mid)
            b["c"] = mid
            if d5 is not None:
                b["d5"] = d5
            b["dl"] = bool(o.get("d_long"))
            b["ds"] = bool(o.get("d_short"))
            b["n"] += 1
        n += 1
    out = [bars[k] for k in sorted(bars)]
    print(f"decision rows in window ~{n}  1m bars {len(out)}")
    return out


def tape_ok(bounce, b):
    d5, dl, ds = b.get("d5"), b.get("dl"), b.get("ds")
    if bounce:
        if dl:
            return True, dict(why="d_long", d5=d5)
        if d5 is not None:
            return d5 > 0, dict(why="d5", d5=d5)
        return False, dict(why="no_tape", d5=d5)
    if ds:
        return True, dict(why="d_short", d5=d5)
    if d5 is not None:
        return d5 < 0, dict(why="d5", d5=d5)
    return False, dict(why="no_tape", d5=d5)


def pick_rail(rails, lo, hi):
    tagged = [r for r in rails if lo - WATCH <= r.px <= hi + WATCH]
    if not tagged:
        return None
    tagged.sort(key=lambda r: -r.recv)
    return tagged[0]


def path_mae_mfe(mids, i0, side, entry):
    hit, mae, mfe, t_hit = "OPEN", 0.0, 0.0, None
    sign = 1.0 if side == "Buy" else -1.0
    for j in range(i0, len(mids)):
        pnl = sign * (mids[j]["c"] - entry)
        mae = min(mae, pnl)
        mfe = max(mfe, pnl)
        if pnl <= -STOP:
            return "SL20", mae, mfe, mids[j]["t0"]
        if pnl >= TP:
            return "TP40", mae, mfe, mids[j]["t0"]
    return hit, mae, mfe, t_hit


def main():
    now = datetime.now(TZ)
    day = now.date()
    if now.hour < 4:
        day = (now - timedelta(days=1)).date()
    day0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=TZ)
    day1 = datetime(day.year, day.month, day.day, 16, 0, tzinfo=TZ)
    print(f"REPLAY {day0} -> {day1}  rules=loc_side_l2_no_hl  paper  3/20/40")
    rails = load_rails(day0, day1)
    print("rails", len(rails), ":", ", ".join(f"{r.kind}@{r.px:.2f}" for r in sorted(rails, key=lambda x: -x.px)[:18]))
    m1 = load_m1(day0, day1)
    if not m1:
        print("NO 1m BARS")
        return
    key = ""
    visit_dead = False
    spent = False
    lock_until = None
    fires = []
    reasons = defaultdict(int)
    for i, b in enumerate(m1):
        if lock_until is not None and b["t0"] < lock_until:
            reasons["locked"] += 1
            continue
        lock_until = None
        rail = pick_rail(rails, b["l"], b["h"])
        if rail is None:
            key, visit_dead, spent = "", False, False
            reasons["no_rail_in_watch"] += 1
            continue
        if rail.key != key:
            key, visit_dead, spent = rail.key, False, False
        if spent:
            reasons["same_sweep_spent"] += 1
            continue
        if visit_dead:
            reasons["visit_dead"] += 1
            continue
        bounce = loc_bounce(b["o"], rail.px)
        if bounce is None:
            bounce = loc_bounce(b["c"], rail.px)
        if bounce is None:
            reasons["at_rail"] += 1
            continue
        hit = (abs(b["l"] - rail.px) <= WATCH) if bounce else (abs(b["h"] - rail.px) <= WATCH)
        if not hit:
            reasons["idle_no_hit"] += 1
            continue
        hold = (b["c"] >= rail.px) if bounce else (b["c"] <= rail.px)
        lean, tmet = tape_ok(bounce, b)
        side = "Buy" if bounce else "Sell"
        if not hold:
            visit_dead = True
            reasons["body_gave_rail"] += 1
            continue
        if not lean:
            reasons["tape_against"] += 1
            continue
        entry = b["c"]
        hitp, mae, mfe, t_hit = path_mae_mfe(m1, i + 1, side, entry)
        rec = dict(
            event="paper_fire", t=b["t0"].strftime("%H:%M"), side=side,
            setup="bounce_long" if bounce else "fade_short",
            poi=rail.key, loc="over" if bounce else "under",
            entry=round(entry, 2), h=round(b["h"], 2), l=round(b["l"], 2),
            c=round(b["c"], 2), tape=tmet, hit=hitp,
            mae=round(mae, 2), mfe=round(mfe, 2),
            t_hit=t_hit.strftime("%H:%M") if t_hit else None,
        )
        fires.append(rec)
        OUT.open("a").write(json.dumps(rec, default=str) + "\n")
        spent = True
        visit_dead = True
        lock_until = t_hit if t_hit else day1
        reasons["fire"] += 1
    print("\n=== PAPER FIRES (new rules) ===")
    if not fires:
        print("none")
    else:
        print(f"{'when':<6} {'side':<4} {'entry':>8} {'poi':<22} {'hit':<5} {'MAE':>6} {'MFE':>6} loc")
        for f in fires:
            print(f"{f['t']:<6} {f['side']:<4} {f['entry']:8.2f} {f['poi']:<22} {f['hit']:<5} {f['mae']:6.1f} {f['mfe']:6.1f} {f['loc']}")
        w = sum(1 for f in fires if f["hit"] == "TP40")
        l = sum(1 for f in fires if f["hit"] == "SL20")
        o = len(fires) - w - l
        pts = w * 40 - l * 20
        print(f"\nn={len(fires)}  TP={w}  SL={l}  OPEN={o}  pts={pts:+.0f}  (3-lot ${pts*2:.0f})")
    print("\n=== skip counts ===")
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {k:20} {v}")
    print("wrote", OUT)


if __name__ == "__main__":
    OUT.write_text("")
    main()
