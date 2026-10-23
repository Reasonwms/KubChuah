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
    # v3.1: these have no defaults any more
    "DD_MODE": "eod_trail",
    "LADDER_CONFIG": "0:5,1000:10,2000:20",
}

ALL_KEYS = set(BASE_ENV) | {
    "DD_MODE", "TRAIL_LOCK_BALANCE", "LADDER_CONFIG", "LADDER_BASIS",
    "LOG_PATH", "ID_FIELD", "EOD_ANCHOR_ID", "ENTRY_CUTOFF_TZ",
    "STATE_PATH", "MAX_CONTRACTS",
}


class SyncThread:
    """Runs the forward() call inline so tests can see it right away."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class GateTest(unittest.TestCase):
    def load(self, log_path=None, state_path=None, **env):
        for k in ALL_KEYS:
            os.environ.pop(k, None)
        os.environ.update(BASE_ENV)
        if log_path is None:
            log_path = pathlib.Path(self.tmp.name) / "decisions.csv"
        os.environ["LOG_PATH"] = str(log_path)
        if state_path is None:
            state_path = pathlib.Path(self.tmp.name) / "state.json"
        os.environ["STATE_PATH"] = str(state_path)
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
    def test_eod_trail_mode_reported(self):
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

    # ------------------------------------------------------------ v3.1: required variables
    def test_missing_required_vars_stop_startup(self):
        for name in ("START_BALANCE", "HAIRCUT_PCT", "DD_MODE", "LADDER_CONFIG",
                     "ENTRY_CUTOFF_ET", "DD_AMOUNT", "DAILY_CAP_AMOUNT"):
            with self.subTest(var=name), self.assertRaises(SystemExit) as cm:
                self.load(**{name: ""})
            self.assertIn(name, str(cm.exception))

    def test_missing_state_path_stops_startup(self):
        for k in ALL_KEYS:
            os.environ.pop(k, None)
        os.environ.update(BASE_ENV)
        os.environ["LOG_PATH"] = str(pathlib.Path(self.tmp.name) / "d.csv")
        sys.modules.pop("app", None)
        with self.assertRaises(SystemExit):
            importlib.import_module("app")

    # ------------------------------------------------------------ v3.1: state file (Test 9)
    def test_state_file_written_on_anchor(self):
        st = pathlib.Path(self.tmp.name) / "state.json"
        self.load(state_path=st)
        self.post("EODAnchor", 50900)
        d = json.loads(st.read_text())
        self.assertEqual((d["day_anchor"], d["eod_hwm"]), (50900, 50900))
        self.assertTrue(d["anchor_time"])

    def test_restart_keeps_hwm_anchor_and_anchor_time(self):
        log = pathlib.Path(self.tmp.name) / "decisions.csv"
        self.load(log_path=log)
        self.post("EODAnchor", 51200)
        before = self.health()
        self.load(log_path=log)                          # "restart"
        after = self.health()
        for k in ("eod_hwm", "day_anchor", "anchor_time", "floor"):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(after["state_source"], "state file + log")

    def test_restart_with_state_file_only(self):
        """Log lost, state file kept: floor must not reset."""
        st = pathlib.Path(self.tmp.name) / "state.json"
        log1 = pathlib.Path(self.tmp.name) / "a.csv"
        self.load(log_path=log1, state_path=st)
        self.post("EODAnchor", 51500)
        log2 = pathlib.Path(self.tmp.name) / "empty.csv"
        self.load(log_path=log2, state_path=st)
        h = self.health()
        self.assertEqual((h["eod_hwm"], h["floor"]), (51500, 49500))

    def test_broken_state_file_falls_back_to_log(self):
        st = pathlib.Path(self.tmp.name) / "state.json"
        log = pathlib.Path(self.tmp.name) / "decisions.csv"
        self.load(log_path=log, state_path=st)
        self.post("EODAnchor", 50800)
        st.write_text("{not json")
        self.load(log_path=log, state_path=st)
        h = self.health()
        self.assertEqual(h["day_anchor"], 50800)
        self.assertEqual(h["state_source"], "log (state file broken)")

    def test_hwm_never_goes_down_on_restart(self):
        """Test 8 on restart: a lower state file can't lower the HWM."""
        st = pathlib.Path(self.tmp.name) / "state.json"
        log = pathlib.Path(self.tmp.name) / "decisions.csv"
        self.load(log_path=log, state_path=st)
        self.post("EODAnchor", 51000)
        self.post("EODAnchor", 50500)                    # losing day
        st.write_text(json.dumps(dict(eod_hwm=50000, day_anchor=50500,
                                      peak=50000, anchor_time="2000-01-01")))
        self.load(log_path=log, state_path=st)
        h = self.health()
        self.assertEqual(h["eod_hwm"], 51000)
        self.assertEqual(h["day_anchor"], 50500)

    # ------------------------------------------------------------ v3.1: /health + hash
    def test_health_has_rev7_fields(self):
        self.load(PMT_WEBHOOK_URL="https://httpbin.org/post")
        h = self.health()
        for k in ("ok", "eod_hwm", "day_anchor", "anchor_time", "dd_mode",
                  "dd_amount", "start_balance", "config_hash", "pmt_host"):
            self.assertIn(k, h)
        self.assertEqual(h["pmt_host"], "httpbin.org")
        self.assertEqual((h["dd_amount"], h["start_balance"]), (2000, 50000))

    def test_config_hash_matches_manifest_tool(self):
        import gate_config
        self.load()
        manifest = dict(BASE_ENV, DD_AMOUNT="2000.0", START_BALANCE=" 50000 ")
        self.assertEqual(self.health()["config_hash"], gate_config.config_hash(manifest))

    def test_config_hash_changes_with_a_rule(self):
        self.load()
        h1 = self.health()["config_hash"]
        self.load(DAILY_CAP_AMOUNT="300")
        self.assertNotEqual(h1, self.health()["config_hash"])

    def test_config_hash_ignores_secrets(self):
        self.load(TELEGRAM_TOKEN="a")
        h1 = self.health()["config_hash"]
        self.load(TELEGRAM_TOKEN="b", SECRET_PATH="testpath")
        self.assertEqual(h1, self.health()["config_hash"])

    # ------------------------------------------------------------ v2.1 fixes carried in
    def test_max_contracts_lowers_cap(self):
        self.load(MAX_CONTRACTS="1")
        self.post("Long", 50100, qty=3)
        self.assertEqual(self.sent[-1]["quantity"], 1)

    def test_overnight_cutoff_window(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        mod = self.load(ENTRY_CUTOFF_ET="15:30", ENTRY_RESUME_ET="09:30")
        tz = ZoneInfo("America/New_York")
        at = lambda h, m: dt.datetime(2026, 10, 22, h, m, tzinfo=tz)
        self.assertTrue(mod.past_entry_cutoff(at(16, 0)))
        self.assertTrue(mod.past_entry_cutoff(at(2, 0)))
        self.assertFalse(mod.past_entry_cutoff(at(10, 0)))

    def test_non_object_json_dropped(self):
        self.load()
        r = self.client.post("/testpath", data="29400")
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
