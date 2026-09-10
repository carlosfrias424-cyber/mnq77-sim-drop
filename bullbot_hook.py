#!/usr/bin/env python3
"""Bullbot.ai webhook. SEPARATE from 7/7 rails.

POST /tv/signal  (also / and /tv/signal/)
  LONG/SHORT entry -> Tradovate DEMO 3 MNQ, stop 20, TP 40
  LONG/SHORT exit  -> flatten DEMO
  OK confirm       -> log only

Never writes tv_poi.jsonl. Never calls live_77.
Demo URL only.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
LOG = ROOT / "logs/bullbot.jsonl"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
FLAT = ROOT / "apps/tradovate/flatten_mkt.py"
HOST = os.environ.get("BULLBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("BULLBOT_PORT", "8788"))
FIRE = os.environ.get("BULLBOT_FIRE", "1").strip() not in ("0", "false", "False", "")


def envload() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def emit(**kw) -> dict:
    rec = {"ts": int(time.time() * 1000), "src": "bullbot.ai", **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)
    return rec


def classify(raw: str, obj) -> str:
    """Return Buy, Sell, flat, ok, or ignore."""
    parts = [raw]
    if isinstance(obj, dict):
        for k in ("action", "side", "event", "type", "msg", "message", "comment", "alert"):
            if obj.get(k) is not None:
                parts.append(str(obj.get(k)))
        parts.append(json.dumps(obj, default=str))
    blob = " ".join(parts).lower()
    blob = blob.replace("—", "-").replace("–", "-")

    if "ok" in blob and "confirm" in blob:
        return "ok"
    if "long exit" in blob or "exit long" in blob or "close long" in blob:
        return "flat"
    if "short exit" in blob or "exit short" in blob or "close short" in blob:
        return "flat"
    if "flatten" in blob or blob.strip() in ("exit", "close"):
        return "flat"
    if "long entry" in blob or "buy" in blob or "entry long" in blob:
        return "Buy"
    if "short entry" in blob or "sell" in blob or "entry short" in blob:
        return "Sell"
    # bare words last
    if " long" in blob or blob.strip() == "long":
        return "Buy"
    if " short" in blob or blob.strip() == "short":
        return "Sell"
    return "ignore"


def run_py(script: Path, extra_env: dict) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(extra_env)
    env.setdefault("TRADOVATE_ENV", "demo")
    r = subprocess.run(
        [str(PY), str(script)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = ((r.stdout or "") + (r.stderr or ""))[-800:]
    return r.returncode, out


def handle_signal(raw: str, obj) -> dict:
    kind = classify(raw, obj)
    rec = emit(event="signal", kind=kind, fire=FIRE, raw=raw[:500], parsed=obj if isinstance(obj, dict) else None)
    if kind == "ignore":
        rec["skip"] = "unparsed"
        emit(**{k: rec[k] for k in rec if k != "ts"}, event="skip")
        return rec
    if kind == "ok":
        rec["skip"] = "ok_confirm_log_only"
        return rec
    if not FIRE:
        rec["skip"] = "fire_off"
        return rec

    if kind == "flat":
        rc, out = run_py(FLAT, {})
        emit(event="flatten", rc=rc, out=out)
        rec["submit"] = "flatten"
        rec["rc"] = rc
        return rec

    # reverse: flatten first so we never add to the other side
    run_py(FLAT, {})
    rc, out = run_py(
        SUBMIT,
        {
            "MNQ_SIDE": kind,
            "MNQ_QTY": "3",
            "TRADOVATE_ENV": "demo",
            "MNQ_POI_NAME": "BULLBOT",
            "MNQ_STOP_PTS": "20",
            "MNQ_T40": "40",
        },
    )
    emit(event="entry", side=kind, rc=rc, out=out)
    rec["submit"] = "entry"
    rec["side"] = kind
    rec["rc"] = rc
    return rec


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("http", self.address_string(), fmt % args, flush=True)

    def _send(self, code: int, body: dict):
        b = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/health", "/tv/signal/health"):
            self._send(200, {"ok": True, "fire": FIRE, "service": "bullbot"})
            return
        self._send(200, {"ok": True, "post": "/tv/signal", "fire": FIRE})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in ("/", "/tv/signal", "/signal"):
            self._send(404, {"err": "use POST /tv/signal"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 50_000)).decode("utf-8", "replace") if n else ""
        obj = None
        s = raw.strip()
        if s:
            try:
                obj = json.loads(s)
            except Exception:
                obj = None
        rec = handle_signal(raw, obj)
        self._send(200, rec)


def main() -> None:
    envload()
    if os.environ.get("TRADOVATE_ENV", "demo").lower() != "demo":
        raise SystemExit("bullbot: not demo")
    emit(event="bullbot_start", fire=FIRE, host=HOST, port=PORT)
    httpd = ThreadingHTTPServer((HOST, PORT), H)
    print(f"bullbot on {HOST}:{PORT} fire={FIRE}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
