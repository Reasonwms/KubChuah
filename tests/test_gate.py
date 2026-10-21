"""
Local tests for the KubChuah gate (app.py). No network: Telegram and the
PickMyTrade forward are replaced with stubs that record what they were sent.

Run from the repo root:
    python -m unittest discover -s tests -v
"""
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_ENV = {
    "SECRET_PATH": "testpath",
    "PMT_WEBHOOK_URL": "https://example.invalid/pmt",
    "TELEGRAM_TOKEN": "x",
    "TELEGRAM_CHAT_ID": "1",
    "START_BALANCE": "50000",
    "DD_AMOUNT": "2000",
    "HAIRCUT_PCT": "0",
    "DAILY_CAP_AMOUNT": "600",
    "QTY_FIELD": "quantity",
    "ENTRY_ORDER_IDS": "Long,Short",
    # empty cutoff window, so tests pass at any time of day
    "ENTRY_CUTOFF_ET": "00:00",
    "ENTRY_RESUME_ET": "00:00",
}

ALL_KEYS = set(BASE_ENV) | {
    "DD_MODE", "TRAIL_LOCK_BALANCE", "LADDER_CONFIG", "LADDER_BASIS",
    "LOG_PATH", "ID_FIELD", "EOD_ANCHOR_ID", "ENTRY_CUTOFF_TZ",
}


class SyncThread:
    """Runs the forward() call inline so tests can see it right away."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class GateTest(unittest.TestCase):
    def load(self, log_path=None, **env):
        for k in ALL_KEYS:
            os.environ.pop(k, None)
        os.environ.update(BASE_ENV)
        if log_path is None:
            log_path = pathlib.Path(self.tmp.name) / "decisions.csv"
        os.environ["LOG_PATH"] = str(log_path)
        os.environ.update({k: str(v) for k, v in env.items()})
        sys.modules.pop("app", None)
        mod = importlib.import_module("app")
        self.sent, self.messages = [], []
        mod.forward = lambda payload: self.sent.append(dict(payload))
        mod.telegram = lambda text: self.messages.append(text)
        mod.threading.Thread = SyncThread
        self.mod, self.client = mod, mod.app.test_client()
        return mod

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._real_thread = __import__("threading").Thread

    def tearDown(self):
        __import__("threading").Thread = self._real_thread
        self.tmp.cleanup()

    def post(self, order_id, equity, qty=1):
        body = {"order_id": order_id, "equity": equity, "quantity": qty,
                "action": "buy"}
        r = self.client.post("/testpath", data=json.dumps(body))
        return r.get_json()

    def health(self):
        return self.client.get("/health").get_json()

    # ------------------------------------------------------------ DD_MODE
    def test_default_mode_is_eod_trail(self):
        self.load()
        self.assertEqual(self.health()["dd_mode"], "eod_trail")

    def test_bad_dd_mode_stops_startup(self):
        with self.assertRaises(SystemExit):
            self.load(DD_MODE="trailing")

    def test_eod_trail_ignores_intraday_highs(self):
        self.load(DD_MODE="eod_trail")
        self.post("Long", 51500)             # intraday high
        self.assertEqual(self.health()["floor"], 48000)
        self.post("EODAnchor", 51000)        # only anchors move the floor
        self.assertEqual(self.health()["floor"], 49000)

    def test_intraday_trail_follows_peak_from_any_alert(self):
        self.load(DD_MODE="intraday_trail")
        self.post("Long", 50800)             # entry
        self.post("LongExit", 51300)         # exit also counts
        self.assertEqual(self.health()["floor"], 49300)
        # equity 49300 is at the floor -> blocked
        self.assertEqual(self.post("Long", 49300)["decision"], "BLOCK")

    def test_static_floor_never_moves(self):
        self.load(DD_MODE="static")
        self.post("EODAnchor", 53000)
        self.post("Long", 53500)
        self.assertEqual(self.health()["floor"], 48000)

    def test_trail_lock_caps_floor(self):
        self.load(DD_MODE="intraday_trail", TRAIL_LOCK_BALANCE="50100")
        self.post("Long", 52500)             # would put floor at 50500
        self.assertEqual(self.health()["floor"], 50100)

    def test_trail_lock_ignored_for_static(self):
        self.load(DD_MODE="static", TRAIL_LOCK_BALANCE="40000")
        self.assertEqual(self.health()["floor"], 48000)

    def test_haircut_raises_block_level(self):
        self.load(DD_MODE="static", HAIRCUT_PCT="20",   # block at 48400
                  DAILY_CAP_AMOUNT="5000")              # keep daily cap out of the way
        self.assertEqual(self.post("Long", 48400)["decision"], "BLOCK")
        self.assertEqual(self.post("Long", 48401)["decision"], "ALLOW")

    def test_intraday_peak_survives_restart(self):
        log = pathlib.Path(self.tmp.name) / "decisions.csv"
        self.load(log_path=log, DD_MODE="intraday_trail")
        self.post("LongExit", 51700)
        self.load(log_path=log, DD_MODE="intraday_trail")   # "redeploy"
        self.assertEqual(self.health()["peak"], 51700)
        self.assertEqual(self.health()["floor"], 49700)

    def test_eod_anchor_survives_restart(self):
        log = pathlib.Path(self.tmp.name) / "decisions.csv"
        self.load(log_path=log)
        self.post("EODAnchor", 50900)
        self.load(log_path=log)
        h = self.health()
        self.assertEqual((h["day_anchor"], h["eod_hwm"]), (50900, 50900))

    # ------------------------------------------------------------ LADDER_CONFIG
    def test_default_ladder_matches_v2(self):
        mod = self.load()
        self.assertEqual(mod.LADDER, [(0, 5), (1000, 10), (2000, 20)])
        caps = [mod.contract_cap(p) for p in (-300, 0, 999.99, 1000, 1999.99, 2000, 9999)]
        self.assertEqual(caps, [5, 5, 5, 10, 10, 20, 20])

    def test_custom_ladder_resizes(self):
        self.load(LADDER_CONFIG="0:1,1500:2,2250:3")
        r = self.post("Long", 50500, qty=3)
        self.assertEqual(r["decision"], "RESIZE")
        self.assertEqual(self.sent[-1]["quantity"], 1)

    def test_ladder_basis_total(self):
        self.load(LADDER_CONFIG="0:1,1500:2,2250:3", LADDER_BASIS="total")
        self.post("EODAnchor", 51800)        # day P&L resets, total does not
        self.post("Long", 51900, qty=3)
        self.assertEqual(self.sent[-1]["quantity"], 2)

    def test_bad_ladders_stop_startup(self):
        for bad in ("", "0:5,abc", "0:5,1000", "1000:10,0:5", "0:0", "0:5,0:10"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.load(LADDER_CONFIG=bad)

    def test_bad_ladder_basis_stops_startup(self):
        with self.assertRaises(SystemExit):
            self.load(LADDER_BASIS="week")

    # ------------------------------------------------------------ unchanged rules
    def test_daily_cap_blocks(self):
        self.load()
        self.assertEqual(self.post("Long", 49400)["decision"], "BLOCK")

    def test_exit_forwarded_untouched(self):
        self.load()
        self.post("LongExit", 30000, qty=50)
        self.assertEqual(self.sent[-1]["quantity"], 50)

    def test_entry_missing_equity_fails_closed(self):
        self.load()
        r = self.client.post("/testpath", data=json.dumps(
            {"order_id": "Long", "quantity": 1}))
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
