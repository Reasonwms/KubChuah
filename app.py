#!/usr/bin/env python3
"""
KubChuah Gate — v3.1 (DD_MODE + LADDER_CONFIG + state file + config hash)
CDO Consulting LLC / ATI Division

Sits between TradingView and PickMyTrade.
Gates ENTRIES only. Exits always forward untouched.
Fail-closed: if a check errors, the entry is blocked.

CHANGES IN v3.1 (2026-10-22) — built for KubChuah Rev 7.1, Steps 5-7:
1. Merged the three v2.1 fixes from main (they were on main but not in v3):
   midnight-crossing entry cutoff, optional MAX_CONTRACTS, and the guard
   for alerts that are valid JSON but not an object.
   WHY: without this, merging v3 would silently undo those fixes.
2. Persistent state file (STATE_PATH, e.g. /data/state.json). EOD high-
   water mark, intraday peak, day anchor AND anchor time are saved after
   every change (write to a temp file, then swap, so a crash mid-write
   cannot leave half a file). On startup the gate reads BOTH the state
   file and the decision log and keeps the safer value: the higher HWM
   and peak, and the newer anchor. A broken state file does not stop the
   service (that would also stop exits); it falls back to the log and
   sends a Telegram warning.
   WHY: Rev 7 Test 9 — the floor must be the same after a restart.
3. No silent defaults for the values that move money. START_BALANCE,
   HAIRCUT_PCT, DD_MODE, LADDER_CONFIG, ENTRY_CUTOFF_ET, LOG_PATH and
   STATE_PATH must be set, or the service stops at startup with the name
   of the missing variable.
   WHY: v3 fell back to old Apex/Legends numbers (50000, 20%, 5/10/20).
   Rev 7 says every value comes from the signed Manifest.
   RISK: set these in Railway BEFORE pushing this code (pre-push checklist),
   or the deploy crashes the same way it did on Sep 30.
4. /health now also shows dd_amount, start_balance, anchor_time,
   max_contracts, config_hash, pmt_host, state_source and version.
   config_hash comes from gate_config.py; run
   "python gate_config.py manifest.env" on the Manifest values to get the
   expected hash before a deploy. pmt_host shows only the host name of
   PMT_WEBHOOK_URL (e.g. httpbin.org), never the full URL.
   WHY: Rev 7 Step 7c checks /health against the Manifest row by row.

CHANGES IN v3 (2026-10-21):
- DD_MODE picks how the drawdown floor moves: "eod_trail", "intraday_trail"
  (floor trails the highest equity seen in any alert), or "static"
  (floor = START_BALANCE - DD_AMOUNT). Unknown value stops startup.
- TRAIL_LOCK_BALANCE (optional) caps how high a trailing floor can go.
- LADDER_CONFIG replaces the hard-coded ladder: "threshold:cap,..." in
  ascending order. P&L below the first threshold gets the first cap.
- LADDER_BASIS: "day" (P&L since today's anchor) or "total"
  (equity - START_BALANCE).
- Exits are logged as PASS rows with their equity.

CHANGES IN v2.1 (2026-10-21):
- Entry cutoff window may cross midnight (cutoff > resume).
- MAX_CONTRACTS (optional, 0 = off) lowers the ladder cap.
- Valid JSON that is not an object is dropped with a 200, nothing forwarded.

CHANGES IN v2 (EOD trailing):
- Daily "EOD anchor" alert (order_id = EOD_ANCHOR_ID) sets the day's
  starting balance and, if higher, the trailing high.
- KubChuah's own daily loss cap (DAILY_CAP_AMOUNT).
- Entry cutoff (ENTRY_CUTOFF_ET) blocks new entries late in the session.
"""
import os, csv, json, pathlib, threading, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import requests
from flask import Flask, request, jsonify

from gate_config import DEFAULTS, config_hash

VERSION = "3.1"


def require(name):
    """Read a variable that has no safe default. Missing = stop at startup."""
    val = os.environ.get(name, "").strip()
    if val == "":
        raise SystemExit(f"KubChuah: required variable {name} is not set "
                         f"(Rev 7: it must come from the signed Manifest)")
    return val


def optional(name):
    return os.environ.get(name, "").strip() or DEFAULTS[name]


# ---------------------------------------------------------------- config
SECRET_PATH = os.environ["SECRET_PATH"]  # random string in the URL
PMT_URL = os.environ["PMT_WEBHOOK_URL"]  # PickMyTrade endpoint (httpbin while testing)
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

START_BALANCE = float(require("START_BALANCE"))
DD_AMOUNT = float(require("DD_AMOUNT"))
HAIRCUT_PCT = float(require("HAIRCUT_PCT"))          # % of the drawdown held back
DAILY_CAP_AMOUNT = float(require("DAILY_CAP_AMOUNT"))  # KubChuah's own daily loss cap
QTY_FIELD = require("QTY_FIELD")
ENTRY_IDS = [s.strip() for s in require("ENTRY_ORDER_IDS").split(",")]
ID_FIELD = optional("ID_FIELD")
EOD_ANCHOR_ID = optional("EOD_ANCHOR_ID")

# Optional hard size limit on top of the ladder. 0 = off.
MAX_CONTRACTS = int(optional("MAX_CONTRACTS"))

# Entry cutoff: "HH:MM" in ENTRY_CUTOFF_TZ. No new entries from cutoff until
# resume. If cutoff > resume, the block crosses midnight.
ENTRY_CUTOFF_ET = require("ENTRY_CUTOFF_ET")
ENTRY_CUTOFF_TZ = optional("ENTRY_CUTOFF_TZ")
ENTRY_RESUME_ET = optional("ENTRY_RESUME_ET")

LOG = pathlib.Path(require("LOG_PATH"))
STATE = pathlib.Path(require("STATE_PATH"))

DD_MODES = ("eod_trail", "intraday_trail", "static")
DD_MODE = require("DD_MODE").lower()
if DD_MODE not in DD_MODES:
    raise SystemExit(f"DD_MODE must be one of {DD_MODES}, got {DD_MODE!r}")

_lock_raw = optional("TRAIL_LOCK_BALANCE")
TRAIL_LOCK_BALANCE = float(_lock_raw) if _lock_raw else None

LADDER_BASES = ("day", "total")
LADDER_BASIS = optional("LADDER_BASIS").lower()
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


LADDER = parse_ladder(require("LADDER_CONFIG"))
CONFIG_HASH = config_hash(os.environ)
PMT_HOST = urlparse(PMT_URL).hostname or "unknown"

FIELDNAMES = [
    "ts", "order_id", "qty_in", "qty_out", "equity",
    "eod_hwm", "day_anchor", "floor", "day_pnl", "buffer_pct",
    "decision", "reason",
]

app = Flask(__name__)
_lock = threading.Lock()
_state_lock = threading.Lock()


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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


# ---------------------------------------------------------------- state
def state_from_log():
    """Rebuild HWM, peak, anchor and anchor time from the decision log."""
    s = dict(eod_hwm=START_BALANCE, day_anchor=START_BALANCE,
             peak=START_BALANCE, anchor_time=None)
    if LOG.exists():
        with LOG.open() as fh:
            for row in csv.DictReader(fh):
                if row.get("decision") == "ANCHOR":
                    try:
                        s["day_anchor"] = float(row["day_anchor"])
                        s["anchor_time"] = row.get("ts") or None
                        s["eod_hwm"] = max(s["eod_hwm"], s["day_anchor"])
                    except (ValueError, KeyError, TypeError):
                        pass
                try:
                    s["peak"] = max(s["peak"], float(row["equity"]))
                except (ValueError, KeyError, TypeError):
                    continue
    return s


def state_from_file():
    """Returns (state dict or None, warning text or None)."""
    if not STATE.exists():
        return None, None
    try:
        d = json.loads(STATE.read_text())
        return dict(eod_hwm=float(d["eod_hwm"]), day_anchor=float(d["day_anchor"]),
                    peak=float(d["peak"]), anchor_time=d.get("anchor_time")), None
    except Exception as e:
        return None, f"state file {STATE} unreadable ({e}) — rebuilt from the log"


def load_state():
    """Keep the safer value from the two sources: higher HWM and peak (they
    only ever move up), and the newer anchor."""
    log_s = state_from_log()
    file_s, warning = state_from_file()
    if file_s is None:
        return log_s, ("log" if warning is None else "log (state file broken)"), warning
    merged = dict(eod_hwm=max(log_s["eod_hwm"], file_s["eod_hwm"]),
                  peak=max(log_s["peak"], file_s["peak"]))
    if (log_s["anchor_time"] or "") > (file_s["anchor_time"] or ""):
        merged.update(day_anchor=log_s["day_anchor"], anchor_time=log_s["anchor_time"])
    else:
        merged.update(day_anchor=file_s["day_anchor"], anchor_time=file_s["anchor_time"])
    return merged, "state file + log", None


_s, STATE_SOURCE, _warning = load_state()
EOD_HWM, DAY_ANCHOR, PEAK, ANCHOR_TIME = (
    _s["eod_hwm"], _s["day_anchor"], _s["peak"], _s["anchor_time"])
if _warning:
    print("KubChuah:", _warning)
    telegram(f"KubChuah WARNING: {_warning}")


def save_state():
    """Write to a temp file, then swap it in, so a crash mid-write can't
    leave half a file. A failed save never blocks a trade; it warns."""
    data = dict(eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, peak=PEAK,
                anchor_time=ANCHOR_TIME, saved_at=now_utc(), version=VERSION)
    try:
        with _state_lock:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_name(STATE.name + ".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, STATE)
    except Exception as e:
        print("state save failed:", e)
        telegram(f"KubChuah WARNING: state save failed — {e}")


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
    if MAX_CONTRACTS > 0:
        cap = min(cap, MAX_CONTRACTS)
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
    if equity > PEAK:
        PEAK = equity
        save_state()


def past_entry_cutoff(now_et=None):
    if now_et is None:
        now_et = datetime.datetime.now(ZoneInfo(ENTRY_CUTOFF_TZ))
    cutoff_h, cutoff_m = (int(x) for x in ENTRY_CUTOFF_ET.split(":"))
    resume_h, resume_m = (int(x) for x in ENTRY_RESUME_ET.split(":"))
    cutoff = now_et.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
    resume = now_et.replace(hour=resume_h, minute=resume_m, second=0, microsecond=0)
    if cutoff <= resume:
        return cutoff <= now_et < resume          # same-day block (e.g. 16:50 -> 18:00)
    return now_et >= cutoff or now_et < resume    # overnight block (e.g. 15:30 -> 09:30)


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


# ---------------------------------------------------------------- endpoint
@app.post(f"/{SECRET_PATH}")
def gate():
    global EOD_HWM, DAY_ANCHOR, ANCHOR_TIME

    raw = request.get_data(as_text=True)
    try:
        payload = json.loads(raw)
    except Exception:
        print("unparseable payload:", raw[:400])
        return jsonify(ok=False), 200

    # valid JSON that is not an object (e.g. a bare number) is dropped
    if not isinstance(payload, dict):
        print("non-object payload dropped:", raw[:400])
        return jsonify(ok=False), 200

    order_id = str(payload.get(ID_FIELD, ""))

    # --- daily EOD anchor report: capture it, log it, save it, forward nothing
    if order_id == EOD_ANCHOR_ID:
        try:
            anchor_equity = float(payload["equity"])
        except Exception as e:
            telegram(f"KubChuah: EOD anchor payload unreadable — {e}")
            return jsonify(ok=False), 200

        DAY_ANCHOR = anchor_equity
        ANCHOR_TIME = now_utc()
        EOD_HWM = max(EOD_HWM, DAY_ANCHOR)
        see_equity(anchor_equity)
        save_state()
        log_row(dict(ts=ANCHOR_TIME,
                     order_id=order_id, qty_in="", qty_out="", equity=anchor_equity,
                     eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                     day_pnl="", buffer_pct="",
                     decision="ANCHOR", reason="EOD anchor updated"))
        telegram(f"KubChuah: EOD anchor set to ${anchor_equity:,.2f} "
                 f"(trailing high ${EOD_HWM:,.2f})")
        return jsonify(ok=True, anchor=anchor_equity, eod_hwm=EOD_HWM), 200

    # --- exits and anything unrecognised: forward untouched, no checks.
    if order_id not in ENTRY_IDS:
        threading.Thread(target=forward, args=(payload,), daemon=True).start()
        try:
            exit_equity = float(payload["equity"])
            see_equity(exit_equity)
        except Exception:
            exit_equity = ""
        log_row(dict(ts=now_utc(),
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
        log_row(dict(ts=now_utc(),
                     order_id=order_id, qty_in="", qty_out=0, equity="",
                     eod_hwm=EOD_HWM, day_anchor=DAY_ANCHOR, floor="",
                     day_pnl="", buffer_pct="",
                     decision="BLOCK", reason=f"fail-closed: {e}"))
        return jsonify(ok=False), 200

    log_row(dict(ts=now_utc(),
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
    return jsonify(ok=True, version=VERSION, config_hash=CONFIG_HASH,
                   dd_mode=DD_MODE, dd_amount=DD_AMOUNT, start_balance=START_BALANCE,
                   floor=round(hard_floor(), 2), eod_hwm=EOD_HWM, peak=PEAK,
                   day_anchor=DAY_ANCHOR, anchor_time=ANCHOR_TIME,
                   trail_lock=TRAIL_LOCK_BALANCE, ladder=LADDER,
                   ladder_basis=LADDER_BASIS, max_contracts=MAX_CONTRACTS,
                   pmt_host=PMT_HOST, state_source=STATE_SOURCE), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
