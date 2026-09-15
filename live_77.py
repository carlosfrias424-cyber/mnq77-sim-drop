#!/usr/bin/env python3
"""7/7 bounce support / fade resistance. Dual UNPLUGGED. PAPER ONLY.

Pine name is the side:
  H4L H1L PDL PWL → bounce long (price must hold OVER)
  H4H H1H PDH PWH → fade short  (price must hold UNDER)
  H4 / H1 (old pine) → infer: mid above = support bounce, mid below = fade
  wrong side of that name = BRT → skip
  OPEN / ONH / ONL / EMA → not rails

L2 = Databento 5m delta. Fire on the closed 1m that tagged, if hold + tape.
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
SKIP_TAGS = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}  # old pine sent no high/low
NOTE = "sr_bounce_fade_h4infer"
BOOK = dict(qty=QTY, stop=STOP_PTS, tp=TP_PTS, be=BE_PTS, peel=False, runner=False)
RAIL_KEEP_S = 5 * 86400  # H4/H1 rails live across days; session rails still skipped
