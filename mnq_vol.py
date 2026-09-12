#!/usr/bin/env python3
"""Databento Live MNQ trades → 1m/5m OHLC + 5m delta. No Dual. No decision.jsonl."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("mnq_vol")
TZ = ZoneInfo("America/Chicago")
STALE_S = float(os.environ.get("MNQ_VOL_STALE_S", "8"))
HEARTBEAT_S = int(os.environ.get("TAPE_HEARTBEAT_S", "10"))


def bar_open(ts: float, minutes: int) -> float:
    dt = datetime.fromtimestamp(ts, TZ)
    m = (dt.minute // minutes) * minutes
    return dt.replace(minute=m, second=0, microsecond=0).timestamp()


def px_of(rec) -> Optional[float]:
    raw = getattr(rec, "price", None)
    if raw is None:
        return None
    try:
        x = float(raw)
        return x / 1e9 if abs(x) > 1e7 else x
    except Exception:
        return None


def sz_of(rec) -> float:
    for k in ("size", "quantity", "qty"):
        v = getattr(rec, k, None)
        if v is not None:
            try:
                return max(0.0, float(v))
            except Exception:
                return 0.0
    return 0.0


def side_delta(rec, sz: float) -> float:
    """+sz = buy aggressor (lift ask), -sz = sell aggressor (hit bid)."""
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("A", "B"):
        return sz if s == "A" else -sz
    if s in ("BUY", "BID", "B"):
        return sz
    if s in ("SELL", "ASK"):
        return -sz
    return 0.0


def rec_ts(rec) -> float:
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
    return time.time()


@dataclass
class Candle:
    t0: float
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    n: int = 0
    delta: float = 0.0


class MnqVol:
    def __init__(self, key: str, dataset="GLBX.MDP3", symbols=("MNQ.c.0",), stale_s=STALE_S):
        self.key, self.dataset, self.symbols, self.stale_s = key, dataset, list(symbols), stale_s
        self._lock = threading.Lock()
        self._last_ts = self._last_px = None
        self._prints = 0
        self.err = None
        self.m1: Optional[Candle] = None
        self.m5: Optional[Candle] = None
        self.closed_1: deque[Candle] = deque(maxlen=80)
        self.closed_5: deque[Candle] = deque(maxlen=40)
        self._stop = threading.Event()
        self._thread = None
        self._client = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mnq-vol", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        try:
            if self._client is not None:
                self._client.stop()
        except Exception:
            pass

    def last(self):
        with self._lock:
            if self._last_px is None:
                return None
            return self._last_px, self._last_ts

    def last_px(self) -> Optional[float]:
        with self._lock:
            return self._last_px

    def last_closed_1(self) -> Optional[Candle]:
        with self._lock:
            return self.closed_1[-1] if self.closed_1 else None

    def last_closed_5(self) -> Optional[Candle]:
        with self._lock:
            return self.closed_5[-1] if self.closed_5 else None

    def prev_closed_5(self) -> Optional[Candle]:
        with self._lock:
            return self.closed_5[-2] if len(self.closed_5) >= 2 else None

    def age_s(self, now: float) -> float:
        with self._lock:
            if self._last_ts is None:
                return 1e9
            return now - self._last_ts

    def fresh(self, now: float) -> bool:
        return self.age_s(now) <= self.stale_s

    def prints(self) -> int:
        with self._lock:
            return self._prints

    def tape_5m(self, bounce: bool) -> tuple[bool, dict]:
        """Lean from Databento 5m delta (buy minus sell size), not Dual 5s."""
        with self._lock:
            last = self.closed_5[-1] if self.closed_5 else None
            prev = self.closed_5[-2] if len(self.closed_5) >= 2 else None
            live = self.m5
            d_last = last.delta if last else None
            d_prev = prev.delta if prev else None
            d_live = live.delta if live else None
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

    def _roll(self, minutes: int, ts: float, px: float, vol: float, dlt: float):
        t0 = bar_open(ts, minutes)
        cur = self.m1 if minutes == 1 else self.m5
        closed = self.closed_1 if minutes == 1 else self.closed_5
        if cur is None or cur.t0 != t0:
            if cur is not None:
                closed.append(cur)
            cur = Candle(t0, px, px, px, px, 0.0, 0, 0.0)
            if minutes == 1:
                self.m1 = cur
            else:
                self.m5 = cur
        cur.h = max(cur.h, px)
        cur.l = min(cur.l, px)
        cur.c = px
        cur.v += vol
        cur.n += 1
        cur.delta += dlt

    def _on_rec(self, rec):
        px = px_of(rec)
        if px is None:
            return
        ts = rec_ts(rec)
        sz = sz_of(rec)
        dlt = side_delta(rec, sz)
        with self._lock:
            self._last_px = px
            self._last_ts = ts
            self._prints += 1
            self._roll(1, ts, px, sz, dlt)
            self._roll(5, ts, px, sz, dlt)

    def _run(self):
        import databento as db
        from databento import ReconnectPolicy
        while not self._stop.is_set():
            try:
                self.err = None
                client = db.Live(key=self.key, reconnect_policy=ReconnectPolicy.RECONNECT)
                try:
                    client.heartbeat_interval_s = HEARTBEAT_S
                except Exception:
                    pass
                self._client = client
                client.subscribe(
                    dataset=self.dataset,
                    schema="trades",
                    symbols=self.symbols,
                    stype_in="continuous",
                )
                client.add_callback(self._on_rec)
                client.start()
                while not self._stop.is_set():
                    time.sleep(0.2)
                    if self.age_s(time.time()) > max(self.stale_s * 3, 30):
                        log.warning("tape stale — reconnect")
                        break
            except Exception as e:
                self.err = str(e)[:200]
                log.warning("mnq_vol error: %r", e)
            try:
                if self._client is not None:
                    self._client.stop()
            except Exception:
                pass
            self._client = None
            if not self._stop.wait(2.0):
                continue


def start_from_env() -> MnqVol:
    key = os.environ.get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_KEY") or ""
    if not key:
        raise RuntimeError("DATABENTO_API_KEY missing")
    return MnqVol(key).start()
