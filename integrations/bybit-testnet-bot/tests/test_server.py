"""Webhook server: secret, validation, journal of refused signals, kill, and one real HTTP round trip."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from cryptobot.server import handle_payload, make_handler
from helpers import BotCase

SECRET = "webhook-secret-123456"


class ServerTests(BotCase, unittest.TestCase):
    def alert(self, **kw):
        p = {"secret": SECRET, "id": "tv-1", "action": "buy", "symbol": "BYBIT:BTCUSDT.P", "sl_pct": 1, "tp_r": 2}
        p.update(kw)
        return p

    def test_bad_secret(self):
        code, _ = handle_payload(self.bot, "/webhook", self.alert(secret="wrong"), SECRET)
        self.assertEqual(code, 403)
        self.assertEqual(self.journal(), [], "spam with a wrong secret is not journaled")

    def test_signal_and_duplicate(self):
        code, body = handle_payload(self.bot, "/webhook", self.alert(), SECRET)
        self.assertEqual((code, body["status"]), (200, "filled"))
        code, body = handle_payload(self.bot, "/webhook", self.alert(), SECRET)
        self.assertEqual((code, body["status"]), (200, "duplicate"))

    def test_invalid_signal_is_journaled(self):
        code, body = handle_payload(self.bot, "/webhook", self.alert(risk=9), SECRET)
        self.assertEqual(code, 400)
        row = self.journal()[0]
        self.assertEqual(row["status"], "rejected")
        self.assertIn("risk above", row["reason"])
        self.assertNotIn(SECRET, row["raw"])

    def test_kill(self):
        handle_payload(self.bot, "/webhook", self.alert(), SECRET)
        code, body = handle_payload(self.bot, "/kill", {"secret": SECRET}, SECRET)
        self.assertEqual((code, body["closed"]), (200, 1))
        self.assertEqual(self.ex.positions(), [])

    def test_http_round_trip(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.bot, SECRET, set(), False))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def post(path, data):
                req = urllib.request.Request(url + path, data=data, headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    with e:
                        return e.code, json.loads(e.read())

            self.assertEqual(post("/webhook", json.dumps(self.alert()).encode())[1]["status"], "filled")
            self.assertEqual(post("/webhook", b"not json")[0], 400)
            self.assertEqual(post("/webhook", json.dumps(self.alert(secret="x")).encode())[0], 403)
            self.assertEqual(post("/webhook", b"x" * 5000)[0], 413)
            self.assertEqual(post("/other", b"{}")[0], 404)
            with urllib.request.urlopen(url + "/health", timeout=5) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
