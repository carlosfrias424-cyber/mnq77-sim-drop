#!/usr/bin/env python3
"""Flatten MNQ on Tradovate DEMO only. Used by bullbot exits."""
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
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if not account_id:
        accounts = requests.get(base + "/account/list", headers=headers, timeout=20).json()
        pick = None
        if isinstance(accounts, list):
            for a in accounts:
                if "DEMO" in str(a.get("name") or "").upper():
                    pick = a
                    break
            if pick is None and accounts:
                pick = accounts[0]
        if not pick:
            log(event="blocked", reason="no account")
            return 5
        account_id = pick.get("id")
    account_id = int(account_id)
    account_spec = os.environ.get("TRADOVATE_ACCOUNT_SPEC") or name
    symbol = os.environ.get("TRADOVATE_SYMBOL", "MNQU6")
    pos_raw = requests.get(base + "/position/list", headers=headers, timeout=20).json()
    positions = pos_raw if isinstance(pos_raw, list) else []
    n = 0
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        net = float(p.get("netPos") or 0)
        if net == 0:
            continue
        action = "Sell" if net > 0 else "Buy"
        qty = int(abs(net))
        body = {
            "accountSpec": account_spec,
            "accountId": account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": qty,
            "orderType": "Market",
            "isAutomated": True,
        }
        pr = requests.post(base + "/order/placeorder", headers=headers, json=body, timeout=20)
        try:
            result = pr.json()
        except Exception:
            result = {"text": pr.text[:300]}
        log(event="flatten", status=pr.status_code, action=action, qty=qty, symbol=symbol, result=result)
        n += 1
    if n == 0:
        log(event="flat_already", positions=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
