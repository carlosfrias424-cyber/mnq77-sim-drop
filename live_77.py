#!/usr/bin/env python3
"""7/7 fade/bounce ONLY. Dual off. No BRT.

Book: 3 MNQ SIM. Stop = 2 pts beyond rail. TP = 40 if R<=20 else 2R. BE +20.
Session 02:00–16:00 America/Chicago (London cash) M–F. Holidays skipped.

Location is NEVER mid.
  Watch: fade if 1m HIGH tags a rail; bounce if 1m LOW tags a rail.
  Spike high → pick the rail at the HIGH (not nearest mid).
Arm: closed 1m high (fade) / low (bounce) tags 6-pt shelf.
Trigger: next closed 1m HL / LH, close still on our side, CVD agree.
Volume is logged, never a veto.
Lock: only while Tradovate net != 0. Flat → fire other rails.
Same sweep: no revenge until price leaves 10 pts.
Rails: pinged since today's 02:00 CT stay in the book. Score only while
1m high/low is within WATCH (10 pts). Leave 10 pts → drop until a NEW ping.
No 120s clock. Dual history book stays off. No BRT skip.
PDL/PDH/OPEN are valid if pinged today. Yesterday's JSONL rows cannot arm.
"""
from __future__ import annotations

import json, os, time, subprocess, sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI, DEC, OUT = ROOT / "logs/tv_poi.jsonl", ROOT / "logs/decision.jsonl", ROOT / "logs/seven.jsonl"
LOCK = ROOT / "logs/submit.lock"
BE20 = ROOT / "logs/be20.jsonl"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))
FIRE = True

TICK = 0.25
WATCH = 10.0
ARM_PTS = 6.0
FAIL_PTS = 6.0
CLUSTER = 8.0
AIR = 2.0
TP_DEFAULT = 40.0
BE_PTS = 20.0
R_SPLIT = 20.0
QTY = 3
PACE_AFTER = 180.0
SESSION_START = 2 * 60
SESSION_END = 16 * 60
TZ = ZoneInfo("America/Chicago")
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
ON_FREEZE = 8 * 60 + 30  # 08:30 CT — ONH/ONL freeze; walking before, rails after
SKIP_LIVE = ()
NOTE = "no_brt_skip"


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
    if mins < SESSION_START:
        return False, "before_2am"
    if mins >= SESSION_END:
        return False, "after_close"
    return True, "open"


def session_open_ts() -> float:
    """02:00 CT today. Rails pinged before this are yesterday — ignore."""
    dt = datetime.now(TZ)
    start = dt.replace(hour=SESSION_START // 60, minute=SESSION_START % 60,
                       second=0, microsecond=0)
    return start.timestamp()


def on_walking(tag: str, t: float) -> bool:
    """ONH/ONL walk until 08:30 CT. After that, only the pre-8:30 print is a rail."""
    u = (tag or "").upper()
    if not (u.startswith("ONH") or u.startswith("ONL")):
        return False
    now = datetime.now(TZ)
    mins = now.hour * 60 + now.minute
    if mins < ON_FREEZE:
        return True
    if not t:
        return False
    try:
        dt = datetime.fromtimestamp(float(t), TZ)
        if dt.date() == now.date() and (dt.hour * 60 + dt.minute) >= ON_FREEZE:
            return True
    except Exception:
        return False
    return False


def last_net():
    if not BE20.exists():
        return None
    try:
        with BE20.open("rb") as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(0, n - 32768), os.SEEK_SET)
            chunk = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    net = None
    for ln in chunk.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("event") in ("flat", "tick", "be_move"):
            if "net" in o and o.get("net") is not None:
                try:
                    net = int(o["net"])
                except Exception:
                    pass
    return net


def locked():
    net = last_net()
    if net == 0:
        if LOCK.exists():
            try:
                LOCK.unlink()
            except Exception:
                pass
        return False
    if net not in (0, None) and abs(int(net)) > 0:
        return True
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


def extreme_dist(px: float, lo: float, hi: float) -> float:
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


def candle_hl(*candles, mid: float):
    lo = hi = None
    for c in candles:
        if c is None:
            continue
        h = getattr(c, "h", None) or getattr(c, "high", None)
        l = getattr(c, "l", None) or getattr(c, "low", None)
        if h is not None:
            hi = float(h) if hi is None else max(hi, float(h))
        if l is not None:
            lo = float(l) if lo is None else min(lo, float(l))
    if lo is None:
        lo = mid
    if hi is None:
        hi = mid
    return lo, hi


def rail_stop_tp(bounce: bool, pack: "Pack", mid: float):
    if bounce:
        stop_px = qtr(pack.zone_lo - AIR)
        r = max(TICK, mid - stop_px)
    else:
        stop_px = qtr(pack.zone_hi + AIR)
        r = max(TICK, stop_px - mid)
    r = qtr(r)
    tp = TP_DEFAULT if r <= R_SPLIT else qtr(2.0 * r)
    return stop_px, r, tp


def book_now(r=None, tp=None):
    return dict(qty=QTY, stop="rail+2", max_r=None, tp="40_or_2R",
                be=BE_PTS, peel=False, runner=False,
                last_r=r, last_tp=tp)


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

    def hit(self, lo, hi, bounce: bool) -> bool:
        if bounce:
            return self.zone_lo <= lo <= self.zone_hi
        return self.zone_lo <= hi <= self.zone_hi

    def close_through(self, close: float, bounce: bool) -> bool:
        if bounce:
            return close < self.zone_lo - FAIL_PTS
        return close > self.zone_hi + FAIL_PTS


def visit_key(pack: Pack) -> str:
    midp = 0.5 * (pack.lo + pack.hi)
    b = round(midp / CLUSTER) * CLUSTER
    return f"{b:.2f}"


def vk_px(px: float) -> str:
    b = round(float(px) / CLUSTER) * CLUSTER
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
    now = time.time()
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
        recv = o.get("recv_ts") or o.get("ts") or o.get("time") or o.get("t") or 0
        t = o.get("ts") or o.get("time") or o.get("t") or recv or 0
        try:
            recv = float(recv)
            if recv > 1e12:
                recv /= 1000.0
        except Exception:
            recv = 0.0
        try:
            t = float(t)
            if t > 1e12:
                t /= 1000.0
        except Exception:
            t = 0.0
        if recv <= 0 or recv < session_open_ts():
            continue
        if on_walking(tag, t):
            continue
        r = Rail(name, px, kind, t)
        if any(tag.startswith(x) or tag == x for x in ("EMA", "ONH", "ONL", "OPEN", "PDH", "PDL", "PWH", "PWL")):
            k = tag
        else:
            k = (r.kind, r.px)
        if k not in best or recv >= getattr(best[k], "_recv", 0):
            r._recv = recv
            best[k] = r
    return list(best.values())


def is_h4(r: Rail) -> bool:
    u = (r.kind + " " + r.name).upper()
    return "H4" in u or r.kind == "240"


def packs_in_watch(rails: list[Rail], lo: float, hi: float, skip_keys=()) -> list[Pack]:
    """Every merged cluster the 1m bar tags. Dead/spent omitted. No single 'nearest'."""
    skip_keys = set(skip_keys)
    tagged = [r for r in rails if extreme_dist(r.px, lo, hi) <= WATCH]
    if not tagged:
        return []
    seen: set[str] = set()
    packs: list[Pack] = []
    for seed in tagged:
        group = [r for r in rails if abs(r.px - seed.px) <= CLUSTER]
        plo = min(r.px for r in group)
        phi = max(r.px for r in group)
        h4 = [r for r in group if is_h4(r)]
        chosen = min(h4 or group, key=lambda r: (extreme_dist(r.px, lo, hi), -r.ts))
        pk = Pack(chosen, plo, phi)
        vk = visit_key(pk)
        if vk in skip_keys or vk in seen:
            continue
        seen.add(vk)
        packs.append(pk)
    packs.sort(key=lambda p: extreme_dist(p.chosen.px, lo, hi))
    return packs


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
        self.spent_fill = False

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


def send_book(side: str, name: str, px: float, mid: float, stop_px: float, stop_pts: float, tp_pts: float):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side, "MNQ_QTY": str(QTY), "TRADOVATE_ENV": "demo",
        "MNQ_POI_NAME": str(name), "MNQ_POI_PX": str(px),
        "MNQ_MID": str(mid), "MNQ_ENTRY": str(round(mid, 2)),
        "MNQ_STOP_PX": str(qtr(stop_px)), "MNQ_STOP_PTS": str(stop_pts),
        "MNQ_T40": str(tp_pts),
    })
    r = subprocess.run([str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        LOCK.write_text(json.dumps({
            "side": side, "poi": name, "px": px, "qty": QTY,
            "entry": round(mid, 2), "stop_px": stop_px, "stop_pts": stop_pts,
            "tp": tp_pts, "be": BE_PTS, "ts": time.time(),
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
                    emit(event="heartbeat", session=ok, why=why, fire=FIRE,
                         locked=locked(), book=book_now(), note=NOTE)
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
    emit(event="seven_start", fire=FIRE, book=book_now(), note=NOTE,
         session_start="02:00", fresh_s=None, vol_src="databento_trades" if dbvol else "missing")
    bars = MinuteBars()
    machines: dict[str, Machine] = {}
    rails: list[Rail] = []
    today_rails: list[Rail] = []
    sticky: dict[str, Rail] = {}
    left_ts: dict[str, float] = {}
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
            today_rails = load_pois()
            last_poi = time.time()

        if dbvol is not None:
            closed = dbvol.last_closed_1()
            forming = getattr(dbvol, "m1", None) or getattr(dbvol, "cur_1", None) or bars.m1
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
            forming = bars.m1
            vol_ok, vmet = False, dict(why="databento_missing")
            vol_src = "missing"

        bar_lo, bar_hi = candle_hl(closed, forming, mid=mid)

        # Keep a pinged rail while 1m still tags it. Leave 10 pts → dead
        # until TV pings again (recv after we left).
        for r in today_rails:
            if extreme_dist(r.px, bar_lo, bar_hi) <= WATCH:
                recv = float(getattr(r, "_recv", 0) or 0)
                if recv > left_ts.get(r.key, 0.0):
                    sticky[r.key] = r
        for k in list(sticky):
            r = sticky[k]
            if extreme_dist(r.px, bar_lo, bar_hi) > WATCH:
                left_ts[k] = now
                sticky.pop(k, None)
        rails = list(sticky.values())
        live_vks = {vk_px(r.px) for r in rails}
        for k in list(machines.keys()):
            try:
                far = extreme_dist(float(k), bar_lo, bar_hi) > WATCH
            except Exception:
                far = k not in live_vks
            if far:
                mm = machines.pop(k)
                emit(event="machine_drop", vk=k, reason="left_watch",
                     phase=mm.phase, arrived=mm.arrived, visit_dead=mm.visit_dead)

        in_pos = locked()
        skip = {k for k, mm in machines.items() if mm.spent_fill or mm.visit_dead} if not in_pos else set()
        packs = packs_in_watch(rails, bar_lo, bar_hi, skip)

        new_1m = closed is not None and closed.t0 != last_1m_t0
        if new_1m:
            last_1m_t0 = closed.t0

        for k, mm in list(machines.items()):
            try:
                far = extreme_dist(float(k), bar_lo, bar_hi) > WATCH
            except Exception:
                far = not packs
            if far:
                if mm.visit_dead or mm.phase != "IDLE" or mm.spent_fill:
                    mm.clear_visit()
                    if n % 20 == 0:
                        emit(event="score", mid=round(mid, 3), poi=k, reason="left_watch_reset")

        if not packs:
            if n % 40 == 0:
                emit(event="score", mid=round(mid, 3), reason="no_rail_in_watch",
                     vol=vmet, vol_src=vol_src, bar=[bar_lo, bar_hi])
            continue

        for pack in packs:
            vk = visit_key(pack)
            m = machines.get(vk)
            if m is None:
                m = Machine(vk)
                machines[vk] = m
            if m.arrived is None and m.last_mid is not None:
                if m.last_mid > pack.chosen.px and mid <= pack.chosen.px:
                    m.arrived = "down"
                elif m.last_mid < pack.chosen.px and mid >= pack.chosen.px:
                    m.arrived = "up"
            m.last_mid = mid

            bounce = (m.arrived or "down") == "down"
            if m.arrived is None:
                # closer wick: low near rail = bounce, high near rail = fade
                bounce = abs(bar_lo - pack.chosen.px) <= abs(bar_hi - pack.chosen.px)

            if m.spent_fill:
                if n % 20 == 0:
                    emit(event="score", mid=round(mid, 3), poi=pack.key, reason="same_sweep_spent")
                continue

            rec = dict(event="score", mid=round(mid, 3), poi=pack.key, vk=vk, px=pack.chosen.px,
                       zone=[pack.zone_lo, pack.zone_hi], vol=vmet, vol_src=vol_src,
                       phase=m.phase, submit=False, arrived=m.arrived, visit_dead=m.visit_dead,
                       bar=[round(bar_lo, 3), round(bar_hi, 3)], tag="low" if bounce else "high",
                       n_rails=len(packs))

            if m.visit_dead:
                continue

            if m.phase == "IDLE":
                if not new_1m:
                    rec["reason"] = "idle_wait_1m"
                    if n % 15 == 0:
                        emit(**rec)
                    continue
                if closed is None or not pack.hit(closed.l, closed.h, bounce):
                    rec["reason"] = "idle_no_hit"
                    if closed:
                        rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
                    if n % 15 == 0:
                        emit(**rec)
                    continue
                lean0 = tape_lean(o, bounce)
                rec["tape_lean"] = lean0
                rec["vol_ok"] = vol_ok
                if bounce and closed.c < pack.chosen.px:
                    rec["reason"] = "idle_long_under_rail"
                    rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
                    emit(**rec)
                    continue
                if (not bounce) and closed.c > pack.chosen.px:
                    rec["reason"] = "idle_short_over_rail"
                    rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
                    emit(**rec)
                    continue
                if not lean0:
                    rec["reason"] = "idle_tape_against"
                    emit(**rec)
                    continue
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
                if not new_1m or m.c1 is None or closed is None or closed.t0 == m.c1.t0:
                    rec["reason"] = "wait_c2"
                    if n % 10 == 0:
                        emit(**rec)
                    continue
                c1, c2 = m.c1, closed
                bounce = m.bounce
                hold = (c2.c >= pack.chosen.px) if bounce else (c2.c <= pack.chosen.px)
                hl = c2.l > c1.l if bounce else c2.h < c1.h
                recut = (c2.l <= c1.l) if bounce else (c2.h >= c1.h)
                through = pack.close_through(c2.c, bounce)
                lean = tape_lean(o, bounce)
                stop_px, stop_pts, tp_pts = rail_stop_tp(bounce, pack, mid)
                rec.update(
                    c1=dict(h=c1.h, l=c1.l, c=c1.c),
                    c2=dict(h=c2.h, l=c2.l, c=c2.c),
                    hold=hold, hl_lh=hl, recut=recut, through=through, tape_lean=lean,
                    stop_pts=stop_pts, stop_px=stop_px, tp_pts=tp_pts, vol_ok=vol_ok,
                )
                why = None
                if through:
                    why = "c2_close_through"
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
                rec["book"] = book_now(stop_pts, tp_pts)
                rec["snap"] = m.out("fire", True)
                if not FIRE:
                    rec["skip"] = "fire_off"
                elif not ok:
                    rec["skip"] = sess
                elif in_pos:
                    rec["skip"] = "open_position"
                else:
                    rc, out = send_book(m.side, pack.key, pack.chosen.px, mid, stop_px, stop_pts, tp_pts)
                    rec["submit"] = rc == 0
                    rec["event"] = "struct40_submit" if rc == 0 else "struct40_fail"
                    rec["rc"] = rc
                    rec["out"] = out
                    if rc == 0:
                        m.spent_fill = True
                        m.phase = "FILLED"
                        in_pos = True
                if rec.get("skip"):
                    m.reset_attempt()
                    m.visit_dead = True
                emit(**rec)
                if rec.get("submit"):
                    break
                continue

            rec["reason"] = m.phase.lower()
            if n % 20 == 0:
                emit(**rec)


if __name__ == "__main__":
    main()
