#!/usr/bin/env python3
"""Paper replay with Databento 5m delta (same tape_5m as live_77).
Pulls GLBX.MDP3 trades for the session, caches them, scores loc_side + hold + 5m lean.
Does NOT submit. Does NOT touch live_77.
"""
from __future__ import annotations
import json, os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/replay_db.jsonl"
CACHE = ROOT / "logs/replay_db_trades.jsonl"
TZ = ZoneInfo("America/Chicago")
WATCH, STOP, TP = 10.0, 20.0, 40.0
SKIP_TAGS = ("ONH", "ONL", "EMA")


def envload():
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


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


def bar_open(ts, minutes):
    dt = datetime.fromtimestamp(ts, TZ)
    m = (dt.minute // minutes) * minutes
    return dt.replace(minute=m, second=0, microsecond=0).timestamp()


def px_of(rec):
    raw = getattr(rec, "price", None)
    if raw is None:
        return None
    try:
        x = float(raw)
        return x / 1e9 if abs(x) > 1e7 else x
    except Exception:
        return None


def sz_of(rec):
    for k in ("size", "quantity", "qty"):
        v = getattr(rec, k, None)
        if v is not None:
            try:
                return max(0.0, float(v))
            except Exception:
                return 0.0
    return 0.0


def side_delta(rec, sz):
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("A", "B"):
        return sz if s == "A" else -sz
    if s in ("BUY", "BID"):
        return sz
    if s in ("SELL", "ASK"):
        return -sz
    return 0.0


def rec_ts(rec):
    for k in ("ts_event", "ts_recv", "timestamp"):
        v = getattr(rec, k, None)
        if v is None:
            continue
        try:
            x = int(v)
            if x > 1e16:
                return x / 1e9
            if x > 1e12:
                return x / 1e6
            return float(x)
        except Exception:
            continue
    return None


@dataclass
class Candle:
    t0: float
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    delta: float = 0.0


@dataclass
class Rail:
    kind: str
    px: float
    recv: float

    @property
    def key(self):
        return f"{self.kind}@{self.px:.2f}"


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


def tape_5m(d_last, d_prev, d_live, bounce):
    met = dict(d_last=d_last, d_prev=d_prev, d_live=d_live, src="databento_5m")
    if d_last is None:
        return False, {**met, "why": "need_closed_5m"}
    cur = d_live if d_live is not None else d_last
    slope = None if d_prev is None else (d_last - d_prev)
    met["slope"] = slope
    if bounce:
        ok = cur > 0 and (slope is None or slope >= 0)
    else:
        ok = cur < 0 and (slope is None or slope <= 0)
    if not ok:
        met["why"] = "tape_against_5m"
    return ok, met


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
        k = (kind, px)
        recv = dt.timestamp()
        if k not in best or recv >= best[k].recv:
            best[k] = Rail(kind, px, recv)
    return list(best.values())


def pull_trades(t0, t1):
    if CACHE.exists() and CACHE.stat().st_size > 1000:
        print("cache hit", CACHE, CACHE.stat().st_size)
        out = []
        for ln in CACHE.open():
            if not ln.strip():
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        print("cached prints", len(out))
        return out
    key = os.environ.get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_KEY") or ""
    if not key:
        raise SystemExit("DATABENTO_API_KEY missing")
    import databento as db
    start = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    end = datetime.fromtimestamp(t1, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    print("databento historical trades", start, "->", end, "UTC")
    client = db.Historical(key)
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        schema="trades",
        symbols=["MNQ.c.0"],
        stype_in="continuous",
        start=start,
        end=end,
    )
    n = 0
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w") as f:
        for rec in data:
            px = px_of(rec)
            ts = rec_ts(rec)
            if px is None or ts is None:
                continue
            sz = sz_of(rec)
            dlt = side_delta(rec, sz)
            f.write(json.dumps({"ts": ts, "px": px, "sz": sz, "dlt": dlt}) + "\n")
            n += 1
            if n % 50000 == 0:
                print("  wrote", n)
    print("wrote prints", n, "->", CACHE)
    out = []
    for ln in CACHE.open():
        if ln.strip():
            out.append(json.loads(ln))
    return out


def build_1m(prints, sess0, sess1):
    m1 = m5 = None
    closed_5 = deque(maxlen=40)
    rows = []

    def roll(minutes, ts, px, vol, dlt):
        nonlocal m1, m5
        t0 = bar_open(ts, minutes)
        cur = m1 if minutes == 1 else m5
        closed = None
        if cur is None or cur.t0 != t0:
            if cur is not None and minutes == 5:
                closed_5.append(cur)
            if cur is not None and minutes == 1:
                closed = cur
            cur = Candle(t0, px, px, px, px, 0.0, 0.0)
            if minutes == 1:
                m1 = cur
            else:
                m5 = cur
        cur.h = max(cur.h, px)
        cur.l = min(cur.l, px)
        cur.c = px
        cur.v += vol
        cur.delta += dlt
        return closed

    for o in prints:
        ts, px, sz, dlt = o["ts"], o["px"], o["sz"], o["dlt"]
        closed = roll(1, ts, px, sz, dlt)
        roll(5, ts, px, sz, dlt)
        if closed is None:
            continue
        if closed.t0 < sess0 or closed.t0 >= sess1:
            continue
        last = closed_5[-1] if closed_5 else None
        prev = closed_5[-2] if len(closed_5) >= 2 else None
        live = m5
        rows.append(dict(
            t0=closed.t0, o=closed.o, h=closed.h, l=closed.l, c=closed.c, v=closed.v,
            d1=closed.delta,
            d_last=last.delta if last else None,
            d_prev=prev.delta if prev else None,
            d_live=live.delta if live else None,
        ))
    print("1m closes in session", len(rows))
    return rows


def pick_rail(rails, lo, hi):
    tagged = [r for r in rails if lo - WATCH <= r.px <= hi + WATCH]
    if not tagged:
        return None
    tagged.sort(key=lambda r: -r.recv)
    return tagged[0]


def path_mae_mfe(rows, i0, side, entry):
    hit, mae, mfe, t_hit = "OPEN", 0.0, 0.0, None
    for j in range(i0, len(rows)):
        b = rows[j]
        if side == "Buy":
            mae = min(mae, b["l"] - entry)
            mfe = max(mfe, b["h"] - entry)
        else:
            mae = min(mae, entry - b["h"])
            mfe = max(mfe, entry - b["l"])
        if mae <= -STOP:
            return "SL20", mae, mfe, b["t0"]
        if mfe >= TP:
            return "TP40", mae, mfe, b["t0"]
    return hit, mae, mfe, t_hit


def main():
    envload()
    now = datetime.now(TZ)
    day = now.date()
    if now.hour < 4:
        day = (now - timedelta(days=1)).date()
    day0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=TZ)
    day1 = datetime(day.year, day.month, day.day, 16, 0, tzinfo=TZ)
    print("REPLAY DB", day0, "->", day1, "tape=databento_5m  loc_side_l2_no_hl  paper 3/20/40")
    rails = load_rails(day0, day1)
    print("rails", len(rails), ":", ", ".join(f"{r.kind}@{r.px:.2f}" for r in sorted(rails, key=lambda x: -x.px)[:18]))
    pad0 = (day0 - timedelta(minutes=15)).timestamp()
    prints = pull_trades(pad0, day1.timestamp())
    rows = build_1m(prints, day0.timestamp(), day1.timestamp())
    if not rows:
        print("NO 1m BARS")
        return
    OUT.write_text("")
    key = ""
    visit_dead = spent = False
    lock_until = None
    fires = []
    reasons = defaultdict(int)
    for i, b in enumerate(rows):
        t0 = b["t0"]
        if lock_until is not None and t0 < lock_until:
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
        lean, tmet = tape_5m(b["d_last"], b["d_prev"], b["d_live"], bounce)
        side = "Buy" if bounce else "Sell"
        if not hold:
            visit_dead = True
            reasons["body_gave_rail"] += 1
            continue
        if not lean:
            reasons["tape_against"] += 1
            rec = dict(event="skip", reason="tape_against_5m",
                       t=datetime.fromtimestamp(t0, TZ).strftime("%H:%M"),
                       poi=rail.key, side=side, tape=tmet, c=round(b["c"], 2))
            OUT.open("a").write(json.dumps(rec, default=str) + "\n")
            continue
        entry = b["c"]
        hitp, mae, mfe, t_hit = path_mae_mfe(rows, i + 1, side, entry)
        rec = dict(
            event="paper_fire", t=datetime.fromtimestamp(t0, TZ).strftime("%H:%M"),
            side=side, setup="bounce_long" if bounce else "fade_short",
            poi=rail.key, loc="over" if bounce else "under",
            entry=round(entry, 2), h=round(b["h"], 2), l=round(b["l"], 2),
            c=round(b["c"], 2), tape=tmet, hit=hitp,
            mae=round(mae, 2), mfe=round(mfe, 2),
            t_hit=datetime.fromtimestamp(t_hit, TZ).strftime("%H:%M") if t_hit else None,
        )
        fires.append(rec)
        OUT.open("a").write(json.dumps(rec, default=str) + "\n")
        spent = True
        visit_dead = True
        lock_until = t_hit if t_hit else day1.timestamp()
        reasons["fire"] += 1

    print("\n=== PAPER FIRES (Databento 5m delta) ===")
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
        print(f"\nn={len(fires)}  TP={w}  SL={l}  OPEN={o}  pts={pts:+.0f}  3-lot ${pts * 6:.0f}")
    print("\n=== skip counts ===")
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {k:20} {v}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
