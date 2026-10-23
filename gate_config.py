#!/usr/bin/env python3
"""
KubChuah Gate — config hash (shared by app.py and the Manifest check)
CDO Consulting LLC / ATI Division

One function, two users:
- app.py calls config_hash(os.environ) and shows it on /health.
- You run this file on the Manifest values BEFORE a deploy to get the
  expected hash:

      python gate_config.py manifest.env

  manifest.env is plain KEY=VALUE lines (the non-secret Manifest values).
  Lines starting with # are ignored. Secrets are never part of the hash,
  so do not put them in the file.

If /health config_hash == the hash printed here, Railway is running the
signed values. If not, do not trade.

The hash covers only the values that change the gate's decisions. It does
NOT cover: secrets (SECRET_PATH, TELEGRAM_*, PickMyTrade token/account),
PMT_WEBHOOK_URL (it changes at go-live; /health shows its host instead),
LOG_PATH, STATE_PATH.
"""
import hashlib
import json
import sys

# Optional settings and the value the gate uses when they are not set.
# app.py reads its defaults from here, so the two can never disagree.
DEFAULTS = {
    "TRAIL_LOCK_BALANCE": "",
    "LADDER_BASIS": "day",
    "MAX_CONTRACTS": "0",
    "ID_FIELD": "order_id",
    "EOD_ANCHOR_ID": "EODAnchor",
    "ENTRY_RESUME_ET": "18:00",
    "ENTRY_CUTOFF_TZ": "America/New_York",
}

NUMBER_KEYS = ("START_BALANCE", "DD_AMOUNT", "HAIRCUT_PCT",
               "DAILY_CAP_AMOUNT", "TRAIL_LOCK_BALANCE")

HASH_KEYS = (
    "START_BALANCE", "DD_AMOUNT", "DD_MODE", "TRAIL_LOCK_BALANCE",
    "HAIRCUT_PCT", "DAILY_CAP_AMOUNT", "LADDER_CONFIG", "LADDER_BASIS",
    "MAX_CONTRACTS", "QTY_FIELD", "ID_FIELD", "ENTRY_ORDER_IDS",
    "EOD_ANCHOR_ID", "ENTRY_CUTOFF_ET", "ENTRY_RESUME_ET", "ENTRY_CUTOFF_TZ",
)


def _norm(key, raw):
    """Write each value one fixed way, so "2000" and "2000.0" give the
    same hash, and spaces do not matter."""
    raw = ("" if raw is None else str(raw)).strip()
    if raw == "":
        return ""
    if key in NUMBER_KEYS:
        return repr(float(raw))
    if key == "MAX_CONTRACTS":
        return str(int(raw))
    if key in ("DD_MODE", "LADDER_BASIS"):
        return raw.lower()
    if key == "LADDER_CONFIG":
        tiers = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                lo, cap = part.split(":")
                tiers.append(f"{float(lo)!r}:{int(cap)}")
        return ",".join(tiers)
    if key == "ENTRY_ORDER_IDS":
        return ",".join(s.strip() for s in raw.split(","))  # case kept on purpose
    return raw


def canonical(env):
    """The exact values that go into the hash, as a sorted dict."""
    out = {}
    for key in HASH_KEYS:
        raw = env.get(key)
        if raw is None or str(raw).strip() == "":
            raw = DEFAULTS.get(key, "")
        out[key] = _norm(key, raw)
    return dict(sorted(out.items()))


def config_hash(env):
    body = json.dumps(canonical(env), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def read_env_file(path):
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python gate_config.py manifest.env")
    env = read_env_file(sys.argv[1])
    for k, v in canonical(env).items():
        print(f"{k:20} = {v}")
    print()
    print("config_hash =", config_hash(env))
