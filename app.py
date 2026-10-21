#!/usr/bin/env python3
"""
KubChuah Gate — v3 (DD_MODE + LADDER_CONFIG)
CDO Consulting LLC / ATI Division

Sits between TradingView and PickMyTrade.
Gates ENTRIES only. Exits always forward untouched.
Fail-closed: if a check errors, the entry is blocked.

CHANGES FROM v2 (one firm, hard-coded rules):
- DD_MODE picks how the drawdown floor moves, so one codebase serves
  any firm: "eod_trail" (default, same as v2), "intraday_trail" (floor
  trails the highest equity the gate has seen in any alert — entries,
  exits and anchors), or "static" (floor = START_BALANCE - DD_AMOUNT,
  never moves). An unknown value stops the service at start-up.
- TRAIL_LOCK_BALANCE (optional) stops a trailing floor from rising past
  that balance, for firms that lock the trail. Blank = no lock.
- LADDER_CONFIG replaces the hard-coded ladder. Format
  "threshold:cap,threshold:cap,..." in ascending order, e.g.
  "0:5,1000:10,2000:20" (the default, same as v2). P&L below the first
  threshold gets the first cap. The last cap is the most the gate will
  ever send, so set it no higher than the firm's max contracts.
- LADDER_BASIS picks which P&L drives the ladder: "day" (default, P&L
  since today's anchor, same as v2) or "total" (equity - START_BALANCE).
- Exits are now logged as PASS rows with their equity, so the
  intraday_trail peak survives a restart (rebuilt from the log).
- /health now reports dd_mode, floor, peak, ladder and ladder_basis.

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
ENTRY_CUTOFF_ET = os.environ.get("ENTRY_CUTOFF_ET", "16:50")
ENTRY_CUTOFF_TZ = os.environ.get("ENTRY_CUTOFF_TZ", "America/New_York")
ENTRY_RESUME_ET = os.environ.get("ENTRY_RESUME_ET", "18:00")

LOG = pathlib.Path(os.environ.get("LOG_PATH", "decisions.csv"))

DD_MODES = ("eod_trail", "intraday_trail", "static")
DD_MODE = os.environ.get("DD_MODE", "eod_trail").strip().lower()
if DD_MODE not in DD_MODES:
    raise SystemExit(f"DD_MODE must be one of {DD_MODES}, got {DD_MODE!r}")

_lock_raw = os.environ.get("TRAIL_LOCK_BALANCE", "").strip()
TRAIL_LOCK_BALANCE = float(_lock_raw) if _lock_raw else None

LADDER_BASES = ("day", "total")
LADDER_BASIS = os.environ.get("LADDER_BASIS", "day").strip().lower()
if LADDER_BASIS not in LADDER_BASES:
    raise SystemExit(f"LADDER_BASIS must be one of {LADDER_BASES}, got {LADDER_BASIS!r}")

def parse_ladder(raw):
    """"0:5,1000:10,2000:20" -> [(0.0, 5), (1000.0, 10), (2000.0, 20)].
    Any mistake stops the service at start-up instead of trading on a
    ladder nobody signed off."""
    tiers = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            lo, cap = part.split(":")
            lo, cap = float(lo), int(cap)
        except ValueError:
            raise SystemExit(f"LADDER_CONFIG tier {part!r} is not threshold:cap")
        if cap < 1:
            raise SystemExit(f"LADDER_CONFIG cap must be 1 or more, got {part!r}")
        if tiers and lo <= tiers[-1][0]:
            raise SystemExit(f"LADDER_CONFIG thresholds must go up, got {raw!r}")
        tiers.append((lo, cap))
    if not tiers:
        raise SystemExit("LADDER_CONFIG is empty")
    return tiers

# position ladder: (min_pnl, cap). P&L is today's (since the anchor) or
# total (since START_BALANCE), per LADDER_BASIS.
LADDER = parse_ladder(os.environ.get("LADDER_CONFIG", "0:5,1000:10,2000:20"))

FIELDNAMES = [
    "ts", "order_id", "qty_in", "qty_out", "equity",
    "eod_hwm", "day_anchor", "floor", "day_pnl", "buffer_pct",
    "decision", "reason",
]

app = Flask(__name__)
_lock = threading.Lock()

# ---------------------------------------------------------------- state
def rebuild_state():
    """EOD high-water mark, intraday peak and today's anchor are rebuilt
    from the decision log on startup. The log IS the state — no separate
    database to lose."""
    eod_hwm = START_BALANCE
    day_anchor = START_BALANCE
    peak = START_BALANCE
    if LOG.exists():
        with LOG.open() as fh:
            for row in csv.DictReader(fh):
                if row.get("decision") == "ANCHOR":
                    try:
                        day_anchor = float(row["day_anchor"])
                        eod_hwm = max(eod_hwm, day_anchor)
                    except (ValueError, KeyError, TypeError):
                        pass
                try:
                    peak = max(peak, float(row["equity"]))
                except (ValueError, KeyError, TypeError):
                    continue
    return eod_hwm, day_anchor, peak

EOD_HWM, DAY_ANCHOR, PEAK = rebuild_state()

def log_row(row):
    new = not LOG.exists()
    with _lock, LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if new:
            w.writeheader()
        w.writerow(row)

# ---------------------------------------------------------------- checks
def contract_cap(pnl):
    cap = LADDER[0][1]  # below the first threshold: smallest tier
    for lo, tier_cap in LADDER:
        if pnl >= lo:
            cap = tier_cap
    return cap

def trail_high():
    """The balance the floor trails from, per DD_MODE."""
    if DD_MODE == "eod_trail":
        return EOD_HWM
    if DD_MODE == "intraday_trail":
        return PEAK
    return START_BALANCE  # static

def hard_floor():
    floor = trail_high() - DD_AMOUNT
    if DD_MODE != "static" and TRAIL_LOCK_BALANCE is not None:
        floor = min(floor, TRAIL_LOCK_BALANCE)
    return floor

def see_equity(equity):
    """Every equity value the gate sees can raise the intraday peak."""
    global PEAK
    PEAK = max(PEAK, equity)

def past_entry_cutoff():
    now_et = datetime.datetime.now(ZoneInfo(ENTRY_CUTOFF_TZ))
    cutoff_h, cutoff_m = (int(x) for x in ENTRY_CUTOFF_ET.split(":"))
    resume_h, resume_m = (int(x) for x in ENTRY_RESUME_ET.split(":"))
    cutoff = now_et.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
    resume = now_et.replace(hour=resume_h, minute=resume_m, second=0, microsecond=0)
    return cutoff <= now_et < resume

def evaluate(equity, qty_in):
    """Returns (decision, qty_out, reason, floor, day_pnl, buffer_pct)."""
    day_pnl = equity - DAY_ANCHOR
    ladder_pnl = day_pnl if LADDER_BASIS == "day" else equity - START_BALANCE

    floor = hard_floor()
    eff_floor = floor + DD_AMOUNT * HAIRCUT_PCT / 100
    buffer_pct = (equity - floor) / DD_AMOUNT * 100

    if past_entry_cutoff():
        return ("BLOCK", 0,
                f"past entry cutoff ({ENTRY_CUTOFF_ET} {ENTRY_CUTOFF_TZ}) — "
                f"no new entries this session",
                floor, day_pnl, buffer_pct)

    if equity <= eff_floor:
        return ("BLOCK", 0,
                f"equity ${equity:,.2f} at/below haircut floor ${eff_floor:,.2f} "
                f"(hard floor ${floor:,.2f}, {DD_MODE}, buffer {buffer_pct:.1f}%)",
                floor, day_pnl, buffer_pct)

    if day_pnl <= -DAILY_CAP_AMOUNT:
        return ("BLOCK", 0,
                f"daily loss cap reached: today's P&L ${day_pnl:,.2f} "
                f"<= -${DAILY_CAP_AMOUNT:,.2f} (KubChuah's own limit)",
                floor, day_pnl, buffer_pct)

    cap = contract_cap(ladder_pnl)
    if qty_in > cap:
        return ("RESIZE", cap,
                f"{LADDER_BASIS} P&L ${ladder_pnl:,.2f} caps size at {cap}; "
                f"requested {qty_in}. Buffer {buffer_pct:.1f}%",
                floor, day_pnl, buffer_pct)

    return ("ALLOW", qty_in,
            f"buffer {buffer_pct:.1f}% remaining, day P&L ${day_pnl:,.2f}, "
            f"size {qty_in} within cap {cap}",
            floor, day_pnl, buffer_pct)

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
        see_equity(anchor_equity)
        log_row(dict(ts=datetime.datetime.utcnow().isoformat(),
                      order_id=order_id, qty_in="", qty_out="", equity=anchor_equity,
                      eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                      day_pnl="", buffer_pct="",
                      decision="ANCHOR", reason="EOD anchor updated"))
        telegram(f"KubChuah: EOD anchor set to ${anchor_equity:,.2f} "
                 f"(trailing high ${EOD_HWM:,.2f})")
        return jsonify(ok=True, anchor=anchor_equity, eod_hwm=EOD_HWM), 200

    # --- exits and anything unrecognised: forward untouched, no checks.
    # The equity is still recorded so the intraday peak survives a restart.
    if order_id not in ENTRY_IDS:
        threading.Thread(target=forward, args=(payload,), daemon=True).start()
        try:
            exit_equity = float(payload["equity"])
            see_equity(exit_equity)
        except Exception:
            exit_equity = ""
        log_row(dict(ts=datetime.datetime.utcnow().isoformat(),
                      order_id=order_id, qty_in=payload.get(QTY_FIELD, ""),
                      qty_out=payload.get(QTY_FIELD, ""), equity=exit_equity,
                      eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                      day_pnl="", buffer_pct="",
                      decision="PASS", reason="not an entry ID — forwarded unchecked"))
        return jsonify(ok=True, gated=False), 200

    # --- entries: gate
    try:
        equity = float(payload["equity"])
        qty_in = int(float(payload[QTY_FIELD]))
        see_equity(equity)
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
    return jsonify(ok=True, dd_mode=DD_MODE, floor=round(hard_floor(), 2),
                   eod_hwm=EOD_HWM, peak=PEAK, day_anchor=DAY_ANCHOR,
                   trail_lock=TRAIL_LOCK_BALANCE, ladder=LADDER,
                   ladder_basis=LADDER_BASIS), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
