"""One signal id = at most one order: repeated alerts, restarts, lost answers from the exchange."""
import unittest

from cryptobot.engine import client_id
from cryptobot.exchange.base import NetworkError, RateLimitError
from cryptobot.signals import parse
from helpers import BotCase


class DuplicateTests(BotCase, unittest.TestCase):
    def test_same_id_twice(self):
        first = self.signal(action="buy", id="tv-1", sl_pct=1)
        second = self.signal(action="buy", id="tv-1", sl_pct=1)
        self.assertEqual(first["status"], "filled")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(self.ex.calls["place_order"], 1)
        self.assertEqual([r["status"] for r in self.journal()], ["filled", "duplicate"])

    def test_duplicate_after_restart(self):
        self.signal(action="buy", id="tv-2", sl_pct=1)
        self.restart()  # new process: nothing in memory, only the database file
        self.ex.set_price("BTCUSDT", 61000)
        r = self.signal(action="buy", id="tv-2", sl_pct=1)
        self.assertEqual(r["status"], "duplicate")
        self.assertEqual(self.ex.calls["place_order"], 1)

    def test_duplicate_even_when_first_was_rejected(self):
        """A refused signal is still 'used': TradingView resending it must not suddenly trade."""
        self.assertEqual(self.signal(action="buy", id="tv-3")["status"], "rejected")  # no stop
        self.assertEqual(self.signal(action="buy", id="tv-3", sl_pct=1)["status"], "duplicate")
        self.assertNotIn("place_order", self.ex.calls)

    def test_id_from_bar_time(self):
        p = {"action": "sell", "symbol": "BYBIT:ETHUSDT.P", "time": "2026-10-17T10:00:00Z", "sl_pct": 1}
        a, b = parse(dict(p)), parse(dict(p))
        self.assertEqual(a.id, b.id)
        self.assertNotEqual(a.id, parse(dict(p, time="2026-10-17T11:00:00Z")).id)
        self.assertEqual(self.bot.handle(a)["status"], "filled")
        self.assertEqual(self.bot.handle(b)["status"], "duplicate")

    def test_answer_lost_after_exchange_accepted(self):
        """Timeout after the exchange placed the order: the bot finds it by client id, sends nothing more."""
        self.ex.fail("place_order", NetworkError("read timeout"), after=True)
        r = self.signal(action="buy", id="tv-4", sl_pct=1)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(self.ex.calls["place_order"], 1)
        self.assertEqual(len(self.ex.positions()), 1)
        self.assertEqual(self.ex.positions()[0].qty, self.ex.get_order("BTCUSDT", client_id("tv-4", "E")).qty)

    def test_request_lost_before_exchange(self):
        """Timeout before the exchange saw the order: the bot sends it again, once."""
        self.ex.fail("place_order", NetworkError("connect timeout"))
        r = self.signal(action="buy", id="tv-5", sl_pct=1)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(self.ex.calls["place_order"], 2)
        self.assertEqual(len([o for o in self.ex.orders.values() if o.status == "filled"]), 1)

    def test_answer_lost_and_lookup_fails_too(self):
        """Worst case: the order went through, then even the lookup times out. The bot re-sends with
        the same client id, the exchange refuses it as a duplicate, the bot reads the existing order."""
        self.ex.fail("place_order", NetworkError("read timeout"), after=True)
        self.ex.fail("get_order", NetworkError("read timeout"))
        r = self.signal(action="buy", id="tv-6", sl_pct=1)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(self.ex.calls["place_order"], 2)
        self.assertEqual(len(self.ex.orders), 1)
        self.assertEqual(len(self.ex.positions()), 1)

    def test_rate_limited_order_is_retried(self):
        self.ex.fail("place_order", RateLimitError("retCode 10006"))
        self.assertEqual(self.signal(action="buy", id="tv-7", sl_pct=1)["status"], "filled")
        self.assertEqual(len(self.ex.orders), 1)

    def test_exchange_down_keeps_row_for_resync(self):
        for _ in range(3):
            self.ex.fail("place_order", NetworkError("connection refused"))
        r = self.signal(action="buy", id="tv-8", sl_pct=1)
        self.assertEqual(r["status"], "error")
        row = self.journal()[0]
        self.assertEqual(row["status"], "sending")  # result unknown: resync decides
        self.assertIn("resync will check", row["reason"])
        self.bot.resync()
        self.assertEqual(self.journal()[0]["status"], "error")
        self.assertIn("never reached", self.journal()[0]["reason"])
        self.assertEqual(self.ex.positions(), [])


if __name__ == "__main__":
    unittest.main()
