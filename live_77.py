#!/usr/bin/env python3
"""7/7 fade/bounce ONLY. Dual off. No BRT.

Book: 3 MNQ SIM, stop 20, TP 40, BE at +20 (manage_be20).
Session 04:00–16:00 CDT M–F. Holidays skipped.

Arm: 1m traded the 6-pt shelf / cluster AND MNQ 5m volume not expanding.
Trigger: NEXT closed 1m HL (bounce) or LH (fade), close still on our side.
Tape lean at fire.
Arrival is STICKY for the visit (from above = bounce, from below = fade).
Through then back = BRT reclaim → skip.
Dead c2 → this visit is done until price leaves 10 pts.
Walking ONH is not a rail.
"""
from __future__ import annotations

import json, os, time, subprocess, sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI, DEC, OUT = ROOT / "logs/tv_poi.jsonl", ROOT / "logs/decision.jsonl", ROOT / "logs/seven.jsonl"
LOCK = ROOT / "logs/submit.lock"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))
FIRE = True

TICK = 0.25
WATCH = 10.0
ARM_PTS = 6.0
FAIL_PTS = 6.0
CLUSTER = 8.0
STOP_PTS = 20.0
TP_PTS = 40.0
BE_PTS = 20.0
QTY = 3
PACE_AFTER = 180.0
TZ = ZoneInfo("America/Chicago")
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
SKIP_LIVE = ("ONH",)  # walking overnight high is not a rail
BOOK = dict(qty=QTY, stop=STOP_PTS, tp=TP_PTS, be=BE_PTS, peel=False, runner=False)


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


def session():
    dt = datetime.now(TZ)
    if dt.weekday() >= 5:
        return False, "weekend"
    if dt.date() in HOLIDAYS:
        return False, "holiday"
    mins = dt.hour * 60 + dt.minute
    if mins < 4 * 60:
        return False, "before_4am"
    if mins >= 16 * 60:
        return False, "after_close"
    return True, "open"


def locked():
    if not LOCK.exists():
        return False
    try:
        o = json.loads(LOCK.read_text() or "{}")
        ts = float(o.get("ts") or LOCK.stat().st_mtime)
        if time.time() - ts > 16 * 3600:
            return False
        return bool(o.get("side"))
    except Exception:
        return True


def emit(**kw):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def qtr(x):
    return round(round(float(x) / TICK) * TICK, 2)


def bar_open(ts: float, minutes: int) -> float:
    dt = datetime.fromtimestamp(ts, TZ)
    m = (dt.minute // minutes) * minutes
    return dt.replace(minute=m, second=0, microsecond=0).timestamp()


@dataclass
class Rail:
    name: str
    px: float
    kind: str
    ts: float = 0.0

    @property
    def key(self):
        return f"{self.kind}@{self.px:.2f}"


@dataclass
class Pack:
    chosen: Rail
    lo: float
    hi: float

    @property
    def key(self):
        return f"{self.chosen.key}:{self.lo:.2f}-{self.hi:.2f}"

    @property
    def zone_lo(self):
        return min(self.lo, self.chosen.px - ARM_PTS)

    @property
    def zone_hi(self):
        return max(self.hi, self.chosen.px + ARM_PTS)

    def hit(self, lo, hi) -> bool:
        return hi >= self.zone_lo and lo <= self.zone_hi

    def close_through(self, close: float, bounce: bool) -> bool:
        if bounce:
            return close < self.zone_lo - FAIL_PTS
        return close > self.zone_hi + FAIL_PTS

    def dist(self, mid: float) -> float:
        if mid < self.zone_lo:
            return self.zone_lo - mid
        if mid > self.zone_hi:
            return mid - self.zone_hi
        return 0.0


def visit_key(pack: Pack) -> str:
    midp = 0.5 * (pack.lo + pack.hi)
    b = round(midp / CLUSTER) * CLUSTER
    return f"{b:.2f}"


@dataclass
class Candle:
    t0: float
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    n: int = 0


class MinuteBars:
    def __init__(self):
        self.m1: Candle | None = None
        self.m5: Candle | None = None
        self.closed_1: deque[Candle] = deque(maxlen=80)
        self.closed_5: deque[Candle] = deque(maxlen=40)

    def push(self, ts: float, px: float, vol: float):
        self._roll(1, ts, px, vol)
        self._roll(5, ts, px, vol)

    def _roll(self, minutes: int, ts: float, px: float, vol: float):
        t0 = bar_open(ts, minutes)
        cur = self.m1 if minutes == 1 else self.m5
        if cur is None or cur.t0 != t0:
            if cur is not None:
                (self.closed_1 if minutes == 1 else self.closed_5).append(cur)
            cur = Candle(t0, px, px, px, px, 0.0, 0)
            if minutes == 1:
                self.m1 = cur
            else:
                self.m5 = cur
        cur.h = max(cur.h, px)
        cur.l = min(cur.l, px)
        cur.c = px
        cur.v += max(0.0, vol)
        cur.n += 1

    def last_closed_1(self) -> Candle | None:
        return self.closed_1[-1] if self.closed_1 else None


def load_pois():
    if not POI.exists():
        return []
    best = {}
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        kind = str(o.get("type") or o.get("tf") or o.get("poi_name") or "H1")
        name = str(o.get("poi_name") or kind)
        tag = (name or kind).upper()
        if any(tag.startswith(x) or tag == x for x in SKIP_LIVE):
            continue
        t = o.get("ts") or o.get("time") or o.get("t") or 0
        try:
            t = float(t)
            if t > 1e12:
                t /= 1000.0
        except Exception:
            t = 0.0
        r = Rail(name, px, kind, t)
        if any(tag.startswith(x) or tag == x for x in ("EMA", "ONL", "OPEN", "PDH", "PDL", "PWH", "PWL")):
            k = tag
        else:
            k = (r.kind, r.px)
        if k not in best or r.ts >= best[k].ts:
            best[k] = r
    return list(best.values())


def is_h4(r: Rail) -> bool:
    u = (r.kind + " " + r.name).upper()
    return "H4" in u or r.kind == "240"


def nearest_pack(mid: float, rails: list[Rail]) -> Pack | None:
    live = [r for r in rails if abs(mid - r.px) <= WATCH]
    if not live:
        return None
    live.sort(key=lambda r: abs(mid - r.px))
    seed = live[0]
    pack = [r for r in live if abs(r.px - seed.px) <= CLUSTER]
    lo = min(r.px for r in pack)
    hi = max(r.px for r in pack)
    h4 = [r for r in pack if is_h4(r)]
    pool = h4 or pack
    pool.sort(key=lambda r: (abs(mid - r.px), -r.ts))
    return Pack(pool[0], lo, hi)


@dataclass
class Machine:
    key: str = ""
    phase: str = "IDLE"
    side: str | None = None
    picture: str = ""
    bounce: bool = True
    c1: Candle | None = None
    last_mid: float | None = None
    arrived: str | None = None
    spent_fill: bool = False
    visit_dead: bool = False

    def reset_attempt(self):
        self.phase = "IDLE"
        self.side = None
        self.picture = ""
        self.c1 = None

    def clear_visit(self):
        self.reset_attempt()
        self.visit_dead = False
        self.arrived = None

    def out(self, reason, go=False):
        return dict(
            phase=self.phase, setup=self.picture, side=self.side,
            picture=self.picture, go=bool(FIRE and go), paper=go,
            fire_enabled=FIRE, reason=reason, bounce=self.bounce,
            visit_dead=self.visit_dead, arrived=self.arrived,
        )


def tape_lean(row: dict, bounce: bool) -> bool:
    d5 = float(row.get("delta_5s") or 0)
    if bounce:
        return d5 > 0 or bool(row.get("d_long") or row.get("delta_lean_long"))
    return d5 < 0 or bool(row.get("d_short") or row.get("delta_lean_short"))


def is_brt_reclaim(pack: Pack, c: Candle) -> bool:
    """Poke through the shelf then close back = reclaim / BRT. Skip."""
    poked_dn = c.l < pack.zone_lo - FAIL_PTS
    poked_up = c.h > pack.zone_hi + FAIL_PTS
    back_up = c.c >= pack.zone_lo
    back_dn = c.c <= pack.zone_hi
    return (poked_dn and back_up) or (poked_up and back_dn)


def send_book(side: str, name: str, px: float, mid: float, stop_px: float, stop_pts: float):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side, "MNQ_QTY": str(QTY), "TRADOVATE_ENV": "demo",
        "MNQ_POI_NAME": str(name), "MNQ_POI_PX": str(px),
        "MNQ_MID": str(mid), "MNQ_ENTRY": str(round(mid, 2)),
        "MNQ_STOP_PX": str(qtr(stop_px)), "MNQ_STOP_PTS": str(STOP_PTS),
        "MNQ_T40": str(TP_PTS),
    })
    r = subprocess.run([str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        LOCK.write_text(json.dumps({
            "side": side, "poi": name, "px": px, "qty": QTY,
            "entry": round(mid, 2), "stop_px": stop_px, "stop_pts": STOP_PTS,
            "tp": TP_PTS, "be": BE_PTS, "ts": time.time(),
        }))
    return r.returncode, (r.stdout or "")[-400:]


def parse_ts(o) -> float:
    ts = o.get("ts")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return time.time()
    try:
        ts = float(ts)
        return ts / 1000.0 if ts > 1e12 else ts
    except Exception:
        return time.time()


def follow(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    last_hb = 0.0
    with path.open() as f:
        f.seek(0, 2)
        while True:
            ln = f.readline()
            if not ln:
                if time.time() - last_hb > 60:
                    ok, why = session()
                    emit(event="heartbeat", session=ok, why=why, fire=FIRE, locked=locked(), book=BOOK)
                    last_hb = time.time()
                time.sleep(0.15)
                continue
            yield ln


def main():
    envload()
    try:
        from mnq_vol import start_from_env
        dbvol = start_from_env()
    except Exception as e:
        dbvol = None
        emit(event="vol_err", err=str(e)[:200])
    emit(event="seven_start", fire=FIRE, book=BOOK, note="fade_bounce_20_40_be20",
         vol_src="databento_trades" if dbvol else "missing")
    bars = MinuteBars()
    machines: dict[str, Machine] = {}
    rails: list[Rail] = []
    last_poi = 0.0
    last_1m_t0 = None
    n = 0
    vol_src = "databento_trades" if dbvol else "missing"

    for ln in follow(DEC):
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("event") not in (None, "decision", "tick", "dual"):
            if o.get("mid") is None:
                continue
        try:
            mid = float(o.get("mid"))
        except Exception:
            continue
        ts = parse_ts(o)
        px = float(o.get("last") or o.get("px") or mid)
        bars.push(ts, px, 0.0)
        now = ts
        n += 1
        if time.time() - last_poi > 5:
            rails = load_pois()
            last_poi = time.time()

        pack = nearest_pack(mid, rails)

        if dbvol is not None:
            closed = dbvol.last_closed_1()
            vol_ok, vmet = dbvol.vol_not_expanding(now, PACE_AFTER)
            vol_src = "databento_trades"
            vmet = dict(vmet or {})
            vmet["age_s"] = dbvol.age_s(now)
            vmet["prints"] = dbvol.prints()
            if dbvol.err:
                vmet["err"] = dbvol.err
            if not dbvol.fresh(now):
                vol_ok = False
                vmet["why"] = "databento_stale"
        else:
            closed = bars.last_closed_1()
            vol_ok, vmet = False, dict(why="databento_missing")
            vol_src = "missing"

        new_1m = closed is not None and closed.t0 != last_1m_t0
        if new_1m:
            last_1m_t0 = closed.t0

        # left 10 pts of a bucket → that visit can arm again later
        for k, mm in list(machines.items()):
            try:
                far = abs(mid - float(k)) > WATCH
            except Exception:
                far = pack is None
            if far and not mm.spent_fill:
                if mm.visit_dead or mm.phase != "IDLE":
                    mm.clear_visit()
                    if n % 20 == 0:
                        emit(event="score", mid=round(mid, 3), poi=k, reason="left_watch_reset")

        if pack is None:
            if n % 40 == 0:
                emit(event="score", mid=round(mid, 3), reason="no_rail_in_watch", vol=vmet, vol_src=vol_src)
            continue

        vk = visit_key(pack)
        m = machines.get(vk)
        if m is None:
            m = Machine(vk)
            machines[vk] = m
        # first cross this visit sticks
        if m.arrived is None and m.last_mid is not None:
            if m.last_mid > pack.chosen.px and mid <= pack.chosen.px:
                m.arrived = "down"
            elif m.last_mid < pack.chosen.px and mid >= pack.chosen.px:
                m.arrived = "up"
        m.last_mid = mid

        if m.spent_fill:
            if n % 20 == 0:
                emit(event="score", mid=round(mid, 3), poi=pack.key, reason="filled_lock")
            continue

        rec = dict(event="score", mid=round(mid, 3), poi=pack.key, vk=vk, px=pack.chosen.px,
                   zone=[pack.zone_lo, pack.zone_hi], vol=vmet, vol_src=vol_src,
                   phase=m.phase, submit=False, arrived=m.arrived, visit_dead=m.visit_dead)

        if m.visit_dead:
            rec["reason"] = "visit_spent"
            if n % 15 == 0:
                emit(**rec)
            continue

        if m.phase == "IDLE":
            if not new_1m:
                rec["reason"] = "idle_wait_1m"
                if n % 15 == 0:
                    emit(**rec)
                continue
            if not pack.hit(closed.l, closed.h):
                rec["reason"] = "idle_no_hit"
                if n % 15 == 0:
                    emit(**rec)
                continue
            if not vol_ok:
                rec["reason"] = "idle_vol_expanding"
                emit(**rec)
                continue
            if is_brt_reclaim(pack, closed):
                m.visit_dead = True
                rec["reason"] = "brt_reclaim"
                rec["snap"] = m.out("brt_reclaim")
                emit(**rec)
                continue
            bounce = (m.arrived or "down") == "down"
            if m.arrived is None:
                bounce = mid >= pack.chosen.px
            m.bounce = bounce
            m.picture = "bounce_long" if bounce else "fade_short"
            m.side = "Buy" if bounce else "Sell"
            m.c1 = closed
            m.phase = "WAIT_C2"
            rec.update(reason="armed_c1", snap=m.out("armed_c1"),
                       c1=dict(t0=closed.t0, h=closed.h, l=closed.l, c=closed.c))
            emit(**rec)
            continue

        if m.phase == "WAIT_C2":
            if not new_1m or m.c1 is None or closed.t0 == m.c1.t0:
                rec["reason"] = "wait_c2"
                if n % 10 == 0:
                    emit(**rec)
                continue
            c1, c2 = m.c1, closed
            bounce = m.bounce
            hold = (c2.c >= pack.zone_lo) if bounce else (c2.c <= pack.zone_hi)
            hl = c2.l > c1.l if bounce else c2.h < c1.h
            recut = (c2.l <= c1.l) if bounce else (c2.h >= c1.h)
            through = pack.close_through(c2.c, bounce)
            lean = tape_lean(o, bounce)
            stop_px = qtr(mid - STOP_PTS) if bounce else qtr(mid + STOP_PTS)
            rec.update(
                c1=dict(h=c1.h, l=c1.l, c=c1.c),
                c2=dict(h=c2.h, l=c2.l, c=c2.c),
                hold=hold, hl_lh=hl, recut=recut, through=through, tape_lean=lean,
                stop_pts=STOP_PTS, stop_px=stop_px, vol_ok=vol_ok,
            )
            why = None
            if is_brt_reclaim(pack, c2):
                why = "brt_reclaim"
            elif through:
                why = "c2_close_through"
            elif not vol_ok:
                why = "vol_expanding"
            elif recut:
                why = "c2_recut"
            elif not hl:
                why = "no_hl_lh"
            elif not hold:
                why = "close_gave_shelf"
            elif not lean:
                why = "tape_against"
            if why:
                m.reset_attempt()
                m.visit_dead = True
                rec.update(reason=why, snap=m.out(why))
                emit(**rec)
                continue

            ok, sess = session()
            rec["event"] = "paper_fire"
            rec["book"] = BOOK
            rec["snap"] = m.out("fire", True)
            if not FIRE:
                rec["skip"] = "fire_off"
            elif not ok:
                rec["skip"] = sess
            elif locked():
                rec["skip"] = "open_position"
            else:
                rc, out = send_book(m.side, pack.key, pack.chosen.px, mid, stop_px, STOP_PTS)
                rec["submit"] = rc == 0
                rec["event"] = "struct40_submit" if rc == 0 else "struct40_fail"
                rec["rc"] = rc
                rec["out"] = out
                if rc == 0:
                    m.spent_fill = True
                    m.phase = "FILLED"
            if rec.get("skip"):
                m.reset_attempt()
                m.visit_dead = True
            emit(**rec)
            continue

        rec["reason"] = m.phase.lower()
        if n % 20 == 0:
            emit(**rec)


if __name__ == "__main__":
    main()
