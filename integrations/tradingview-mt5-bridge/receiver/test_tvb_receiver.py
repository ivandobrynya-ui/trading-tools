"""Tests for the webhook receiver. Run: python -m unittest test_tvb_receiver.py"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import tvb_receiver as r

SECRET = "test-secret-1234567890"


class ValidateTests(unittest.TestCase):
    def base(self, **kw):
        p = {"secret": SECRET, "action": "buy", "symbol": "OANDA:XAUUSD", "id": "XAUUSD-60-1700000000-buy", "sl_points": 500}
        p.update(kw)
        return p

    def test_valid_buy(self):
        s = r.validate(self.base(), SECRET, 1700000000.0)
        self.assertEqual(s["symbol"], "XAUUSD")
        self.assertEqual(s["action"], "buy")
        self.assertEqual(s["sl_points"], "500.0")

    def test_bad_secret(self):
        with self.assertRaises(PermissionError):
            r.validate(self.base(secret="nope"), SECRET, 0)

    def test_bad_action_and_symbol(self):
        with self.assertRaises(ValueError):
            r.validate(self.base(action="hedge"), SECRET, 0)
        with self.assertRaises(ValueError):
            r.validate(self.base(symbol="XAU USD;rm"), SECRET, 0)

    def test_numbers(self):
        for bad in ({"sl": "abc"}, {"sl": -1}, {"risk": 9}, {"sl": 1, "sl_points": 5}, {"tp": "nan"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                r.validate(self.base(**bad), SECRET, 0)

    def test_id_from_bar_time(self):
        p = self.base()
        del p["id"]
        p["time"] = "2026-10-16T13:00:00Z"
        a = r.validate(p, SECRET, 0)["id"]
        b = r.validate(dict(p), SECRET, 5)["id"]
        self.assertEqual(a, b)  # same bar -> same id -> duplicate
        p2 = dict(p, time="2026-10-16T14:00:00Z")
        self.assertNotEqual(a, r.validate(p2, SECRET, 0)["id"])
        del p["time"]
        with self.assertRaises(ValueError):
            r.validate(p, SECRET, 0)

    def test_close_all_needs_no_symbol(self):
        s = r.validate({"secret": SECRET, "action": "close_all", "id": "flat-1"}, SECRET, 0)
        self.assertEqual(s["action"], "close_all")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.b = r.Bridge(self.base, SECRET)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, sig_id="a1"):
        return {"secret": SECRET, "action": "sell", "symbol": "XAUUSD", "id": sig_id, "sl": 4200.5, "tp": 4150, "risk": 0.5}

    def test_queue_and_duplicates(self):
        code, body = self.b.handle_webhook(self.payload())
        self.assertEqual(body["status"], "queued")
        code, body = self.b.handle_webhook(self.payload())
        self.assertEqual(body["status"], "duplicate")
        files = list((self.base / "tvb_inbox").glob("*.sig"))
        self.assertEqual(len(files), 1)
        line = files[0].read_text(encoding="ascii").strip()
        kv = dict(part.split("=", 1) for part in line.split(";"))
        self.assertEqual(kv["action"], "sell")
        self.assertEqual(kv["sl"], "4200.5")
        self.assertFalse(list((self.base / "tvb_inbox").glob("*.tmp")))

    def test_duplicates_survive_restart(self):
        self.b.handle_webhook(self.payload("persist-1"))
        b2 = r.Bridge(self.base, SECRET)
        _, body = b2.handle_webhook(self.payload("persist-1"))
        self.assertEqual(body["status"], "duplicate")

    def test_kill(self):
        with self.assertRaises(PermissionError):
            self.b.handle_kill({"secret": "x"})
        self.b.handle_kill({"secret": SECRET})
        self.assertTrue((self.base / "tvb_KILL.flag").exists())


class HttpTests(unittest.TestCase):
    """End to end over HTTP, the way TradingView calls it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bridge = r.Bridge(Path(cls.tmp.name), SECRET)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), r.make_handler(cls.bridge, set(), False))
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def post(self, path, body, raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_http_flow(self):
        ok = {"secret": SECRET, "action": "buy", "symbol": "BINANCE:BTCUSDT", "id": "btc-1", "sl_points": 30000}
        self.assertEqual(self.post("/webhook", ok), (200, {"status": "queued", "id": "btc-1"}))
        self.assertEqual(self.post("/webhook", ok)[1]["status"], "duplicate")
        self.assertEqual(self.post("/webhook", dict(ok, secret="bad"))[0], 403)
        self.assertEqual(self.post("/webhook", None, raw=b"not json")[0], 400)
        self.assertEqual(self.post("/webhook", None, raw=b"x" * 5000)[0], 413)
        self.assertEqual(self.post("/nope", ok)[0], 404)
        with urllib.request.urlopen(self.url + "/health", timeout=5) as resp:
            self.assertEqual(json.loads(resp.read()), {"ok": True})


if __name__ == "__main__":
    unittest.main()
