#!/usr/bin/env python3
"""DEMO: after 7/7 fill, at +20 pts move ALL working stops to entry. No trail. No new entries."""
from __future__ import annotations

import json, os, time, uuid
from pathlib import Path
import requests

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
DEC = ROOT / "logs/decision.jsonl"
OUT = ROOT / "logs/be20.jsonl"
DEMO = "https://demo.tradovateapi.com/v1"
BE_TRIGGER = 20.0
POLL = 3.0
TICK = 0.25
WORKING = {"working", "pending", "accepted", "queued", "held", "suspended", ""}
DEAD = {"filled", "cancelled", "canceled", "rejected", "expired", "completed"}


def envload():
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


def log(**kw):
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def qtr(x):
    return round(round(float(x) / TICK) * TICK, 2)


def tail_lines(path: Path, n: int = 40) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 65536), os.SEEK_SET)
        chunk = f.read().decode("utf-8", "replace")
    return [ln for ln in chunk.splitlines() if ln.strip()][-n:]


def mid_from_tape():
    mid = None
    for line in tail_lines(DEC, 60):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        for key in ("mid", "price", "last", "close"):
            v = rec.get(key)
            if v is None:
                continue
            try:
                mid = float(v)
            except (TypeError, ValueError):
                continue
    return mid


def demo_base():
    if os.environ.get("TRADOVATE_ENV", "demo").lower() != "demo":
        raise SystemExit("not demo")
    base = os.environ.get("TRADOVATE_BASE", DEMO).rstrip("/")
    if "live.tradovateapi.com" in base:
        raise SystemExit("live url forbidden")
    return base


def auth(base):
    name = os.environ.get("TRADOVATE_NAME") or ""
    password = os.environ.get("TRADOVATE_PASSWORD") or ""
    if not name or not password:
        raise SystemExit("missing creds")
    body = {
        "name": name, "password": password,
        "appId": os.environ.get("TRADOVATE_APP_ID", "MNQHybrid"),
        "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "0.1"),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", str(uuid.uuid4())),
    }
    if os.environ.get("TRADOVATE_CID"):
        try:
            body["cid"] = int(os.environ["TRADOVATE_CID"])
        except ValueError:
            body["cid"] = os.environ["TRADOVATE_CID"]
    if os.environ.get("TRADOVATE_SEC"):
        body["sec"] = os.environ["TRADOVATE_SEC"]
    data = requests.post(base + "/auth/accesstokenrequest", json=body, timeout=20).json()
    token = data.get("accessToken")
    if not token:
        log(event="auth_fail", err=data.get("errorText"))
        raise SystemExit(4)
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if not account_id:
        acc = requests.get(base + "/account/list", headers=headers, timeout=20).json()
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
            raise SystemExit(5)
        account_id = pick.get("id")
    return headers, int(account_id), os.environ.get("TRADOVATE_ACCOUNT_SPEC") or name


def jget(base, path, headers):
    r = requests.get(base + path, headers=headers, timeout=20)
    try:
        return r.json()
    except Exception:
        return []


def as_list(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("orders", "positions", "items", "d"):
            if isinstance(raw.get(k), list):
                return raw[k]
    return []


def status_of(o):
    return str(o.get("ordStatus") or o.get("status") or "").lower()


def is_working(o):
    st = status_of(o)
    if st in DEAD:
        return False
    return st in WORKING or bool(o.get("id"))


def is_stop(o):
    ot = str(o.get("orderType") or o.get("type") or "").lower()
    return ot in {"stop", "stoplimit", "trailingstop", "mit"}


def stop_price_of(o):
    for k in ("stopPrice", "price", "auxPrice"):
        if o.get(k) is not None:
            try:
                return float(o[k])
            except (TypeError, ValueError):
                pass
    return None


def join_versions(orders, versions):
    latest = {}
    for v in versions:
        oid = v.get("orderId") or v.get("id")
        if oid is None:
            continue
        prev = latest.get(oid)
        vid = int(v.get("id") or 0)
        if prev is None or vid >= int(prev.get("id") or 0):
            latest[oid] = v
    out = []
    for o in orders:
        oid = o.get("id") or o.get("orderId")
        v = latest.get(oid) or {}
        m = dict(o)
        for k in ("orderType", "stopPrice", "price", "orderQty", "timeInForce"):
            if v.get(k) is not None:
                m[k] = v[k]
        out.append(m)
    return out


def modify_stop(base, headers, order, new_px, account_spec, account_id):
    oid = order.get("id") or order.get("orderId")
    qty = int(order.get("orderQty") or order.get("qty") or 1)
    otype = order.get("orderType") or "Stop"
    if str(otype).lower() == "trailingstop":
        otype = "Stop"
    body = {
        "orderId": oid, "orderQty": qty, "orderType": otype,
        "stopPrice": new_px, "timeInForce": order.get("timeInForce") or "Day",
        "isAutomated": True, "accountSpec": account_spec, "accountId": account_id,
    }
    r = requests.post(base + "/order/modifyorder", headers=headers, json=body, timeout=20)
    try:
        result = r.json()
    except Exception:
        result = {"text": r.text[:300]}
    return {"status": r.status_code, "orderId": oid, "to": new_px, "qty": qty, "result": result}


def pick_position(positions, account_id):
    net, entry = 0, None
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            n = int(float(p.get("netPos") if p.get("netPos") is not None else p.get("net") or 0))
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        net = n
        for k in ("netPrice", "avgPrice", "averagePrice", "price"):
            if p.get(k) is not None:
                try:
                    entry = float(p[k])
                    break
                except (TypeError, ValueError):
                    pass
    return net, entry


def collect_stops(orders, account_id):
    stops = []
    for o in orders:
        if o.get("accountId") is not None and int(o.get("accountId") or 0) != account_id:
            continue
        if not is_stop(o) or not is_working(o):
            continue
        px = stop_price_of(o)
        if px is None:
            continue
        stops.append((o, px))
    return stops


def main():
    envload()
    base = demo_base()
    headers, account_id, account_spec = auth(base)
    log(event="boot", account_id=account_id, be_trigger=BE_TRIGGER, trail=False)
    last_be_px = None
    token_ts = time.time()
    while True:
        try:
            if time.time() - token_ts > 600:
                headers, account_id, account_spec = auth(base)
                token_ts = time.time()
            mid = mid_from_tape()
            positions = as_list(jget(base, "/position/list", headers))
            net, entry = pick_position(positions, account_id)
            if net == 0 or entry is None or mid is None:
                log(event="flat", net=net, mid=mid, entry=entry)
                last_be_px = None
                time.sleep(POLL)
                continue
            long = net > 0
            profit = (mid - entry) if long else (entry - mid)
            orders = join_versions(
                as_list(jget(base, "/order/list", headers)),
                as_list(jget(base, "/orderVersion/list", headers)),
            )
            stops = collect_stops(orders, account_id)
            stop_pxs = [px for _, px in stops]
            be_px = qtr(entry)
            if stop_pxs:
                if long:
                    at_be = all(px >= be_px - 0.01 for px in stop_pxs)
                else:
                    at_be = all(px <= be_px + 0.01 for px in stop_pxs)
            else:
                at_be = False
            log(event="tick", net=net, entry=entry, mid=mid, profit=round(profit, 2),
                stops=stop_pxs, be_done=at_be)
            if profit >= BE_TRIGGER and not at_be:
                for o, px in stops:
                    if long and px >= be_px - 0.01:
                        continue
                    if (not long) and px <= be_px + 0.01:
                        continue
                    if last_be_px == be_px:
                        continue
                    resp = modify_stop(base, headers, o, be_px, account_spec, account_id)
                    log(event="be_move", from_px=px, **resp)
                last_be_px = be_px
        except SystemExit:
            raise
        except Exception as e:
            log(event="error", err=repr(e))
        time.sleep(POLL)


if __name__ == "__main__":
    main()
