#!/usr/bin/env python3
"""
KubChuah Gate — minimal entry guard
CDO Consulting LLC / ATI Division

Sits between TradingView and PickMyTrade.
Gates ENTRIES only. Exits always forward untouched.
Fail-closed: if a check errors, the entry is blocked.
"""
import os, csv, json, pathlib, threading, datetime
import requests
from flask import Flask, request, jsonify

# ---------------------------------------------------------------- config
SECRET_PATH   = os.environ["SECRET_PATH"]          # random string in the URL
PMT_URL       = os.environ["PMT_WEBHOOK_URL"]      # PickMyTrade endpoint
TG_TOKEN      = os.environ["TELEGRAM_TOKEN"]
TG_CHAT       = os.environ["TELEGRAM_CHAT_ID"]

START_BALANCE = float(os.environ.get("START_BALANCE", 50000))
DD_AMOUNT     = float(os.environ["DD_AMOUNT"])     # from Step 0.1
HAIRCUT_PCT   = float(os.environ.get("HAIRCUT_PCT", 20))  # % of buffer held back
QTY_FIELD     = os.environ["QTY_FIELD"]            # from Step 0.3
ENTRY_IDS     = [s.strip() for s in os.environ["ENTRY_ORDER_IDS"].split(",")]
ID_FIELD      = os.environ.get("ID_FIELD", "order_id")

LOG = pathlib.Path(os.environ.get("LOG_PATH", "decisions.csv"))

# contract ladder: (min_cum_pnl, max_cum_pnl, cap)
LADDER = [(0, 1499.99, 1), (1500, 2249.99, 2), (2250, float("inf"), 3)]

app = Flask(__name__)
_lock = threading.Lock()


# ---------------------------------------------------------------- state
def rebuild_hwm():
    """High-water mark rebuilt from the decision log on startup.
    The log IS the state — no separate database to lose."""
    hwm = START_BALANCE
    if LOG.exists():
        with LOG.open() as fh:
            for row in csv.DictReader(fh):
                try:
                    hwm = max(hwm, float(row["equity"]))
                except (ValueError, KeyError):
                    continue
    return hwm


HWM = rebuild_hwm()


def log_decision(row):
    new = not LOG.exists()
    with _lock, LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "ts", "order_id", "qty_in", "qty_out", "equity",
            "hwm", "floor", "buffer_pct", "decision", "reason"])
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------- checks
def contract_cap(cum_pnl):
    for lo, hi, cap in LADDER:
        if lo <= cum_pnl <= hi:
            return cap
    return 1


def evaluate(equity, qty_in):
    """Returns (decision, qty_out, reason, floor, buffer_pct)."""
    global HWM
    HWM = max(HWM, equity)

    hard_floor = HWM - DD_AMOUNT
    eff_floor  = HWM - DD_AMOUNT * (1 - HAIRCUT_PCT / 100)
    buffer_pct = (equity - hard_floor) / DD_AMOUNT * 100

    if equity <= eff_floor:
        return ("BLOCK", 0,
                f"equity ${equity:,.2f} at/below haircut floor ${eff_floor:,.2f} "
                f"(hard floor ${hard_floor:,.2f}, buffer {buffer_pct:.1f}%)",
                hard_floor, buffer_pct)

    cap = contract_cap(equity - START_BALANCE)
    if qty_in > cap:
        return ("RESIZE", cap,
                f"cum P&L ${equity - START_BALANCE:,.2f} caps size at {cap}; "
                f"requested {qty_in}. Buffer {buffer_pct:.1f}%",
                hard_floor, buffer_pct)

    return ("ALLOW", qty_in,
            f"buffer {buffer_pct:.1f}% remaining, size {qty_in} within cap {cap}",
            hard_floor, buffer_pct)


# ---------------------------------------------------------------- outbound
def telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text}, timeout=5)
    except Exception as e:
        print("telegram failed:", e)


def forward(payload):
    try:
        r = requests.post(PMT_URL, json=payload, timeout=10)
        print("pmt response", r.status_code, r.text[:300])
    except Exception as e:
        print("forward failed:", e)
        telegram(f"KubChuah: FORWARD FAILED — {e}")


# ---------------------------------------------------------------- endpoint
@app.post(f"/{SECRET_PATH}")
def gate():
    raw = request.get_data(as_text=True)
    try:
        payload = json.loads(raw)
    except Exception:
        print("unparseable payload:", raw[:400])
        return jsonify(ok=False), 200          # respond fast regardless

    order_id = str(payload.get(ID_FIELD, ""))

    # --- exits and anything unrecognised: forward untouched, no checks
    if order_id not in ENTRY_IDS:
        threading.Thread(target=forward, args=(payload,), daemon=True).start()
        return jsonify(ok=True, gated=False), 200

    # --- entries: gate
    try:
        equity = float(payload["equity"])
        qty_in = int(float(payload[QTY_FIELD]))
        decision, qty_out, reason, floor, buf = evaluate(equity, qty_in)
    except Exception as e:
        # fail-closed: anything unexpected blocks the entry
        telegram(f"KubChuah: BLOCKED (fail-closed) — {e}")
        log_decision(dict(ts=datetime.datetime.utcnow().isoformat(),
                          order_id=order_id, qty_in="", qty_out=0, equity="",
                          hwm=HWM, floor="", buffer_pct="",
                          decision="BLOCK", reason=f"fail-closed: {e}"))
        return jsonify(ok=False), 200

    log_decision(dict(ts=datetime.datetime.utcnow().isoformat(),
                      order_id=order_id, qty_in=qty_in, qty_out=qty_out,
                      equity=equity, hwm=HWM, floor=round(floor, 2),
                      buffer_pct=round(buf, 1),
                      decision=decision, reason=reason))

    telegram(f"KubChuah {decision}\n{order_id} | qty {qty_in} → {qty_out}\n"
             f"equity ${equity:,.2f} | floor ${floor:,.2f} | buffer {buf:.1f}%\n"
             f"{reason}")

    if decision != "BLOCK":
        payload[QTY_FIELD] = qty_out
        threading.Thread(target=forward, args=(payload,), daemon=True).start()

    return jsonify(ok=True, decision=decision), 200


@app.get("/health")
def health():
    return jsonify(ok=True, hwm=HWM), 200
