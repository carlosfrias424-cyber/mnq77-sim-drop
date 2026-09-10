#!/usr/bin/env python3
"""DEMO market entry. No stop, no TP. Qty from MNQ_QTY (default 5)."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import requests

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
OUT = ROOT / "logs/bullbot.jsonl"
DEMO = "https://demo.tradovateapi.com/v1"
QTY = 5


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


def log(**kw) -> None:
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def main() -> int:
    envload()
    if os.environ.get("TRADOVATE_ENV", "demo").lower() != "demo":
        log(event="refused", reason="not demo")
        return 2
    base = os.environ.get("TRADOVATE_BASE", DEMO).rstrip("/")
    if "live.tradovateapi.com" in base:
        log(event="refused", reason="live url forbidden")
        return 2
    side = os.environ.get("MNQ_SIDE", "")
    if side not in ("Buy", "Sell"):
        log(event="blocked", reason="MNQ_SIDE must be Buy or Sell", side=side)
        return 3
    try:
        qty = int(float(os.environ.get("MNQ_QTY") or QTY))
    except ValueError:
        qty = QTY
    if qty < 1:
        qty = QTY

    name = os.environ.get("TRADOVATE_NAME") or ""
    password = os.environ.get("TRADOVATE_PASSWORD") or ""
    if not name or not password:
        log(event="blocked", reason="missing creds")
        return 3
    auth_body = {
        "name": name,
        "password": password,
        "appId": os.environ.get("TRADOVATE_APP_ID", "MNQHybrid"),
        "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "0.1"),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", str(uuid.uuid4())),
    }
    if os.environ.get("TRADOVATE_CID"):
        try:
            auth_body["cid"] = int(os.environ["TRADOVATE_CID"])
        except ValueError:
            auth_body["cid"] = os.environ["TRADOVATE_CID"]
    if os.environ.get("TRADOVATE_SEC"):
        auth_body["sec"] = os.environ["TRADOVATE_SEC"]
    r = requests.post(base + "/auth/accesstokenrequest", json=auth_body, timeout=20)
    auth = r.json()
    token = auth.get("accessToken")
    if not token:
        log(event="auth_fail", status=r.status_code, err=auth.get("errorText"))
        return 4
    h = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if not account_id:
        acc = requests.get(base + "/account/list", headers=h, timeout=20).json()
        pick = None
        if isinstance(acc, list):
            for a in acc:
                if "DEMO" in str(a.get("name") or "").upper():
                    pick = a
                    break
            if pick is None and acc:
                pick = acc[0]
        if not pick:
            log(event="blocked", reason="no account")
            return 5
        account_id = pick.get("id")
    account_id = int(account_id)
    spec = os.environ.get("TRADOVATE_ACCOUNT_SPEC") or name
    symbol = os.environ.get("TRADOVATE_SYMBOL", "MNQU6")

    pos_raw = requests.get(base + "/position/list", headers=h, timeout=20).json()
    positions = pos_raw if isinstance(pos_raw, list) else []
    net = 0
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            net += int(float(p.get("netPos") or 0))
        except (TypeError, ValueError):
            pass
    want = qty if side == "Buy" else -qty
    if net != 0 and (net > 0) == (want > 0):
        log(event="skip_same_side", net=net, side=side, qty=qty)
        return 0
    if net != 0:
        action = "Sell" if net > 0 else "Buy"
        body = {
            "accountSpec": spec,
            "accountId": account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": abs(net),
            "orderType": "Market",
            "isAutomated": True,
        }
        pr = requests.post(base + "/order/placeorder", headers=h, json=body, timeout=20)
        try:
            result = pr.json()
        except Exception:
            result = {"text": pr.text[:300]}
        log(event="reverse_flat", status=pr.status_code, net=net, result=result)

    body = {
        "accountSpec": spec,
        "accountId": account_id,
        "action": side,
        "symbol": symbol,
        "orderQty": qty,
        "orderType": "Market",
        "isAutomated": True,
    }
    pr = requests.post(base + "/order/placeorder", headers=h, json=body, timeout=20)
    try:
        result = pr.json()
    except Exception:
        result = {"text": pr.text[:400]}
    log(
        event="bullbot_entry",
        status=pr.status_code,
        side=side,
        qty=qty,
        symbol=symbol,
        stop=None,
        tp=None,
        result=result,
    )
    return 0 if pr.status_code < 300 else 7


if __name__ == "__main__":
    raise SystemExit(main())
