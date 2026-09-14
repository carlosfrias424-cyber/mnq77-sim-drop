#!/usr/bin/env python3
"""7/7 fade/bounce. Dual UNPLUGGED. PAPER ONLY.

Side from LOCATION, not rail name, not wick-tag:
  price under rail → fade short
  price over  rail → bounce long
  break + hold the other side → other trade (automatic)

L2 filter = Databento 5m delta lean. No HL/LH extra candle.
Fire on the closed 1m that tagged, if hold + tape.

Book: 3 MNQ, stop 20, TP 40, BE +20. Session 04:00–16:00 CT M–F.
FIRE=False: paper_fire only.
"""
from __future__ import annotations

import json, os, time, subprocess, sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/seven.jsonl"
LOCK = ROOT / "logs/submit.lock"
BE20 = ROOT / "logs/be20.jsonl"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))

FIRE = False
TICK, WATCH = 0.25, 10.0
STOP_PTS, TP_PTS, BE_PTS, QTY = 20.0, 40.0, 20.0, 3
SESSION_START, SESSION_END = 4 * 60, 16 * 60
TZ = ZoneInfo("America/Chicago")
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
SKIP_TAGS = ("ONH", "ONL", "EMA")
NOTE = "loc_side_l2_no_hl"
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
    if mins < SESSION_START:
        return False, "before_4am"
    if mins >= SESSION_END:
        return False, "after_close"
    return True, "open"


def session_open_ts() -> float:
    dt = datetime.now(TZ)
    start = dt.replace(hour=4, minute=0, second=0, microsecond=0)
    return start.timestamp()


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
        if o.get("event") in ("flat", "tick", "be_move") and o.get("net") is not None:
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


def extreme_dist(px: float, lo: float, hi: float) -> float:
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


def norm_kind(name: str, kind: str) -> str:
    """H4H/H4L/H4 → H4. H1H/H1L/H1 → H1. Session names stay."""
    u = (name or kind or "H1").upper()
    if u.startswith("H4"):
        return "H4"
    if u.startswith("H1"):
        return "H1"
    return name or kind or "H1"


def loc_bounce(close_px: float, rail_px: float) -> bool | None:
    """Under rail = fade (False). Over rail = bounce (True). On rail = None."""
    if close_px < rail_px:
        return False
    if close_px > rail_px:
        return True
    return None


@dataclass
class Rail:
    name: str
    px: float
    kind: str
    ts: float = 0.0
    recv: float = 0.0

    @property
    def key(self):
        return f"{self.kind}@{self.px:.2f}"


def load_pois() -> list[Rail]:
    if not POI.exists():
        return []
    best = {}
    cut = session_open_ts()
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
        raw = str(o.get("poi_name") or o.get("type") or o.get("tf") or "H1")
        tag = raw.upper()
        if any(tag.startswith(x) or tag == x for x in SKIP_TAGS):
            continue
        kind = norm_kind(raw, raw)
        recv = o.get("recv_ts") or o.get("ts") or 0
        t = o.get("ts") or recv or 0
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
        if recv <= 0 or recv < cut:
            continue
        r = Rail(kind, px, kind, t, recv)
        k = (r.kind, r.px)
        if k not in best or recv >= best[k].recv:
            best[k] = r
    return list(best.values())


def alert_rail(rails: list[Rail], lo: float, hi: float) -> Rail | None:
    tagged = [r for r in rails if extreme_dist(r.px, lo, hi) <= WATCH]
    if not tagged:
        return None
    tagged.sort(key=lambda r: -r.recv)
    return tagged[0]


@dataclass
class Machine:
    key: str = ""
    phase: str = "IDLE"
    side: str | None = None
    picture: str = ""
    bounce: bool = True
    visit_dead: bool = False
    spent_fill: bool = False

    def reset_attempt(self):
        self.phase = "IDLE"
        self.side = None
        self.picture = ""

    def clear_visit(self):
        self.reset_attempt()
        self.visit_dead = False
        self.spent_fill = False

    def out(self, reason, go=False):
        return dict(
            phase=self.phase, setup=self.picture, side=self.side,
            picture=self.picture, go=bool(FIRE and go), paper=go,
            fire_enabled=FIRE, reason=reason, bounce=self.bounce,
            visit_dead=self.visit_dead,
        )


def send_book(side: str, name: str, px: float, last: float):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side, "MNQ_QTY": str(QTY), "TRADOVATE_ENV": "demo",
        "MNQ_POI_NAME": str(name), "MNQ_POI_PX": str(px),
        "MNQ_MID": str(last), "MNQ_ENTRY": str(round(last, 2)),
        "MNQ_STOP_PTS": str(STOP_PTS), "MNQ_T40": str(TP_PTS),
    })
    r = subprocess.run([str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        LOCK.write_text(json.dumps({
            "side": side, "poi": name, "px": px, "qty": QTY,
            "entry": round(last, 2), "stop_pts": STOP_PTS,
            "tp": TP_PTS, "be": BE_PTS, "ts": time.time(),
        }))
    return r.returncode, (r.stdout or "")[-400:]


def main():
    envload()
    try:
        from mnq_vol import start_from_env
        dbvol = start_from_env()
    except Exception as e:
        emit(event="fatal", err=str(e)[:300], note="databento required — dual unplugged")
        return
    emit(event="seven_start", fire=FIRE, book=BOOK, note=NOTE,
         session_start="04:00", vol_src="databento_trades", dual="UNPLUGGED")

    m = Machine()
    last_poi = 0.0
    last_1m_t0 = None
    last_hb = 0.0
    rails: list[Rail] = []
    n = 0

    while True:
        now = time.time()
        if now - last_hb > 60:
            ok, why = session()
            emit(event="heartbeat", session=ok, why=why, fire=FIRE,
                 locked=locked(), book=BOOK, note=NOTE, dual="UNPLUGGED")
            last_hb = now

        if now - last_poi > 5:
            rails = load_pois()
            last_poi = now

        last = dbvol.last()
        if last is None:
            time.sleep(0.25)
            continue
        last_px, last_ts = last
        n += 1

        if not dbvol.fresh(now):
            if n % 40 == 0:
                emit(event="score", reason="tape_stale", mid=round(last_px, 3),
                     age_s=round(dbvol.age_s(now), 2), prints=dbvol.prints())
            time.sleep(0.25)
            continue

        closed = dbvol.last_closed_1()
        forming = dbvol.m1
        bar_lo = min(x for x in (
            getattr(closed, "l", None), getattr(forming, "l", None), last_px) if x is not None)
        bar_hi = max(x for x in (
            getattr(closed, "h", None), getattr(forming, "h", None), last_px) if x is not None)

        rail = alert_rail(rails, bar_lo, bar_hi)
        rec = dict(
            event="score", mid=round(last_px, 3),
            bar=[round(bar_lo, 3), round(bar_hi, 3)],
            vol_src="databento_trades", prints=dbvol.prints(),
            submit=False, dual="UNPLUGGED",
        )

        if rail is None:
            if m.key:
                m.clear_visit()
                m.key = ""
            rec["reason"] = "no_rail_in_watch"
            if n % 40 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        if m.key != rail.key:
            m = Machine(key=rail.key)
        rec.update(poi=rail.key, px=rail.px, phase=m.phase)

        new_1m = closed is not None and closed.t0 != last_1m_t0
        if new_1m:
            last_1m_t0 = closed.t0

        in_pos = locked()
        if m.spent_fill:
            if n % 20 == 0:
                rec["reason"] = "same_sweep_spent"
                emit(**rec)
            time.sleep(0.25)
            continue
        if m.visit_dead:
            time.sleep(0.25)
            continue

        if not new_1m or closed is None:
            rec["reason"] = "idle_wait_1m"
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        bounce = loc_bounce(closed.o, rail.px)
        if bounce is None:
            bounce = loc_bounce(closed.c, rail.px)
        if bounce is None:
            rec["reason"] = "at_rail"
            rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
            emit(**rec)
            time.sleep(0.25)
            continue

        rec["tag"] = "low" if bounce else "high"
        rec["loc"] = "over" if bounce else "under"
        hit = (abs(closed.l - rail.px) <= WATCH) if bounce else (abs(closed.h - rail.px) <= WATCH)
        if not hit:
            rec["reason"] = "idle_no_hit"
            rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        hold = (closed.c >= rail.px) if bounce else (closed.c <= rail.px)
        lean, tmet = dbvol.tape_5m(bounce)
        rec.update(
            c1=dict(t0=closed.t0, h=closed.h, l=closed.l, c=closed.c),
            hold=hold, tape_lean=lean, tape=tmet,
            stop_pts=STOP_PTS, tp_pts=TP_PTS,
        )
        m.bounce = bounce
        m.picture = "bounce_long" if bounce else "fade_short"
        m.side = "Buy" if bounce else "Sell"

        if not hold:
            m.visit_dead = True
            rec.update(reason="body_gave_rail", snap=m.out("body_gave_rail"), visit_dead=True)
            emit(**rec)
            time.sleep(0.25)
            continue
        if not lean:
            rec.update(reason="tape_against", snap=m.out("tape_against"))
            emit(**rec)
            time.sleep(0.25)
            continue

        ok, sess = session()
        rec["event"] = "paper_fire"
        rec["book"] = BOOK
        rec["snap"] = m.out("fire", True)
        rec["reason"] = "fire"
        if not FIRE:
            rec["skip"] = "fire_off"
        elif not ok:
            rec["skip"] = sess
        elif in_pos:
            rec["skip"] = "open_position"
        else:
            rc, out = send_book(m.side, rail.key, rail.px, last_px)
            rec["submit"] = rc == 0
            rec["event"] = "struct40_submit" if rc == 0 else "struct40_fail"
            rec["rc"] = rc
            rec["out"] = out
            if rc == 0:
                m.spent_fill = True
                m.phase = "FILLED"
        if rec.get("skip"):
            m.spent_fill = True
            m.visit_dead = True
        emit(**rec)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
