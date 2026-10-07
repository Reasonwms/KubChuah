#!/usr/bin/env python3
"""
KubChuah Gate — v2 (Legends Trading / EOD trailing drawdown)
CDO Consulting LLC / ATI Division

Sits between TradingView and PickMyTrade.
Gates ENTRIES only. Exits always forward untouched.
Fail-closed: if a check errors, the entry is blocked.

CHANGES FROM v1 (Apex / intraday trailing):
- Drawdown floor now trails on END-OF-DAY balance only, not on every
  intraday tick. A dedicated daily "EOD anchor" alert from TradingView
  (order_id = EOD_ANCHOR_ID, default "EODAnchor") reports equity once a
  day; that value becomes the new day's starting balance and — if it's
  a new high — raises the trailing floor. Nothing else moves the floor.
- Added a KubChuah-side daily loss cap (DAILY_CAP_AMOUNT) since Legends
  Trading itself has no daily loss limit. This blocks entries once
  today's loss (measured from the day's anchor) reaches the cap, even
  if the overall EOD floor hasn't been touched.
- Position ladder is now keyed off TODAY's P&L since the day's anchor
  (not lifetime P&L since START_BALANCE), and uses Legends Trading's
  own size steps: 5 / 10 / 20 micros.
- Added an entry-time cutoff (ENTRY_CUTOFF_ET, default 16:45 America/
  New_York) to block new entries ahead of Legends Trading's no-
  overnight-positions rule. This does NOT close open positions — it
  only stops new ones from opening late. Flattening before the session
  ends is still on the trader (or the firm's own auto-liquidation).
"""
import os, csv, json, pathlib, threading, datetime
from zoneinfo import ZoneInfo
import requests
from flask import Flask, request, jsonify

# ---------------------------------------------------------------- config
SECRET_PATH = os.environ["SECRET_PATH"]  # random string in the URL
PMT_URL = os.environ["PMT_WEBHOOK_URL"]  # PickMyTrade endpoint
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

START_BALANCE = float(os.environ.get("START_BALANCE", 50000))
DD_AMOUNT = float(os.environ["DD_AMOUNT"])             # EOD trailing drawdown, e.g. 2000
HAIRCUT_PCT = float(os.environ.get("HAIRCUT_PCT", 20))  # % of buffer held back
DAILY_CAP_AMOUNT = float(os.environ["DAILY_CAP_AMOUNT"])  # KubChuah's own daily loss cap, e.g. 600
QTY_FIELD = os.environ["QTY_FIELD"]
ENTRY_IDS = [s.strip() for s in os.environ["ENTRY_ORDER_IDS"].split(",")]
ID_FIELD = os.environ.get("ID_FIELD", "order_id")
EOD_ANCHOR_ID = os.environ.get("EOD_ANCHOR_ID", "EODAnchor")

# Entry cutoff: "HH:MM" in the given timezone. No new entries at/after this.
# Legends Trading requires positions closed by ~4:59pm ET. Default here is
# 4:50pm ET, 9 minutes of margin ahead of that close, not sitting on top of it.
ENTRY_CUTOFF_ET = os.environ.get("ENTRY_CUTOFF_ET", "18:00")
ENTRY_CUTOFF_TZ = os.environ.get("ENTRY_CUTOFF_TZ", "America/New_York")
ENTRY_RESUME_ET = os.environ.get("ENTRY_RESUME_ET", "18:00")
LOG = pathlib.Path(os.environ.get("LOG_PATH", "decisions.csv"))

# position ladder: (min_day_pnl, max_day_pnl, cap) — keyed off TODAY's P&L
# since the day's anchor, per Legends Trading's size steps.
LADDER = [(0, 999.99, 5), (1000, 1999.99, 10), (2000, float("inf"), 20)]

FIELDNAMES = [
    "ts", "order_id", "qty_in", "qty_out", "equity",
    "eod_hwm", "day_anchor", "floor", "day_pnl", "buffer_pct",
    "decision", "reason",
]

app = Flask(__name__)
_lock = threading.Lock()

# ---------------------------------------------------------------- state
def rebuild_state():
    """EOD high-water mark and today's anchor are rebuilt from the decision
    log on startup. The log IS the state — no separate database to lose."""
    eod_hwm = START_BALANCE
    day_anchor = START_BALANCE
    if LOG.exists():
        with LOG.open() as fh:
            for row in csv.DictReader(fh):
                if row.get("decision") == "ANCHOR":
                    try:
                        day_anchor = float(row["day_anchor"])
                        eod_hwm = max(eod_hwm, day_anchor)
                    except (ValueError, KeyError):
                        continue
    return eod_hwm, day_anchor

EOD_HWM, DAY_ANCHOR = rebuild_state()

def log_row(row):
    new = not LOG.exists()
    with _lock, LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if new:
            w.writeheader()
        w.writerow(row)

# ---------------------------------------------------------------- checks
def contract_cap(day_pnl):
    for lo, hi, cap in LADDER:
        if lo <= day_pnl <= hi:
            return cap
    return 5  # below $0 for the day (or any unmatched value): smallest tier

ENTRY_RESUME_ET = os.environ.get("ENTRY_RESUME_ET", "18:00")    now_et = datetime.datetime.now(ZoneInfo(ENTRY_CUTOFF_TZ))
    cutoff_h, cutoff_m = (int(x) for x in ENTRY_CUTOFF_ET.split(":"))
    cutoff = now_et.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
    return now_et >= cutoff

def evaluate(equity, qty_in):
    """Returns (decision, qty_out, reason, floor, day_pnl, buffer_pct)."""
    day_pnl = equity - DAY_ANCHOR

    hard_floor = EOD_HWM - DD_AMOUNT
    eff_floor = EOD_HWM - DD_AMOUNT * (1 - HAIRCUT_PCT / 100)
    buffer_pct = (equity - hard_floor) / DD_AMOUNT * 100

    if past_entry_cutoff():
        return ("BLOCK", 0,
                f"past entry cutoff ({ENTRY_CUTOFF_ET} {ENTRY_CUTOFF_TZ}) — "
                f"no new entries this session",
                hard_floor, day_pnl, buffer_pct)

    if equity <= eff_floor:
        return ("BLOCK", 0,
                f"equity ${equity:,.2f} at/below EOD haircut floor ${eff_floor:,.2f} "
                f"(hard floor ${hard_floor:,.2f}, buffer {buffer_pct:.1f}%)",
                hard_floor, day_pnl, buffer_pct)

    if day_pnl <= -DAILY_CAP_AMOUNT:
        return ("BLOCK", 0,
                f"daily loss cap reached: today's P&L ${day_pnl:,.2f} "
                f"<= -${DAILY_CAP_AMOUNT:,.2f} (KubChuah's own limit, "
                f"not a Legends Trading rule)",
                hard_floor, day_pnl, buffer_pct)

    cap = contract_cap(day_pnl)
    if qty_in > cap:
        return ("RESIZE", cap,
                f"today's P&L ${day_pnl:,.2f} caps size at {cap}; "
                f"requested {qty_in}. Buffer {buffer_pct:.1f}%",
                hard_floor, day_pnl, buffer_pct)

    return ("ALLOW", qty_in,
            f"buffer {buffer_pct:.1f}% remaining, day P&L ${day_pnl:,.2f}, "
            f"size {qty_in} within cap {cap}",
            hard_floor, day_pnl, buffer_pct)

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
    global EOD_HWM, DAY_ANCHOR

    raw = request.get_data(as_text=True)
    try:
        payload = json.loads(raw)
    except Exception:
        print("unparseable payload:", raw[:400])
        return jsonify(ok=False), 200

    order_id = str(payload.get(ID_FIELD, ""))

    # --- daily EOD anchor report: capture it, log it, forward nothing
    if order_id == EOD_ANCHOR_ID:
        try:
            anchor_equity = float(payload["equity"])
        except Exception as e:
            telegram(f"KubChuah: EOD anchor payload unreadable — {e}")
            return jsonify(ok=False), 200

        DAY_ANCHOR = anchor_equity
        EOD_HWM = max(EOD_HWM, DAY_ANCHOR)
        log_row(dict(ts=datetime.datetime.utcnow().isoformat(),
                      order_id=order_id, qty_in="", qty_out="", equity=anchor_equity,
                      eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                      day_pnl="", buffer_pct="",
                      decision="ANCHOR", reason="EOD anchor updated"))
        telegram(f"KubChuah: EOD anchor set to ${anchor_equity:,.2f} "
                 f"(trailing high ${EOD_HWM:,.2f})")
        return jsonify(ok=True, anchor=anchor_equity, eod_hwm=EOD_HWM), 200

    # --- exits and anything unrecognised: forward untouched, no checks
    if order_id not in ENTRY_IDS:
        threading.Thread(target=forward, args=(payload,), daemon=True).start()
        return jsonify(ok=True, gated=False), 200

    # --- entries: gate
    try:
        equity = float(payload["equity"])
        qty_in = int(float(payload[QTY_FIELD]))
        decision, qty_out, reason, floor, day_pnl, buf = evaluate(equity, qty_in)
    except Exception as e:
        telegram(f"KubChuah: BLOCKED (fail-closed) — {e}")
        log_row(dict(ts=datetime.datetime.utcnow().isoformat(),
                      order_id=order_id, qty_in="", qty_out=0, equity="",
                      eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                      day_pnl="", buffer_pct="",
                      decision="BLOCK", reason=f"fail-closed: {e}"))
        return jsonify(ok=False), 200

    log_row(dict(ts=datetime.datetime.utcnow().isoformat(),
                  order_id=order_id, qty_in=qty_in, qty_out=qty_out,
                  equity=equity, eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR,
                  floor=round(floor, 2), day_pnl=round(day_pnl, 2),
                  buffer_pct=round(buf, 1),
                  decision=decision, reason=reason))

    telegram(f"KubChuah {decision}\n{order_id} | qty {qty_in} -> {qty_out}\n"
             f"equity ${equity:,.2f} | floor ${floor:,.2f} | day P&L ${day_pnl:,.2f} "
             f"| buffer {buf:.1f}%\n{reason}")

    if decision != "BLOCK":
        payload[QTY_FIELD] = qty_out
        threading.Thread(target=forward, args=(payload,), daemon=True).start()

    return jsonify(ok=True, decision=decision), 200

@app.get("/health")
def health():
    return jsonify(ok=True, eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
