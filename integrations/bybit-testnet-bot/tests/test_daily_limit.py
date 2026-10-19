"""Daily loss limit and kill switch: they block entries, flatten, survive restarts, and reset correctly."""
import unittest
from decimal import Decimal

from helpers import BotCase

D = Decimal


class DailyLimitTests(BotCase, unittest.TestCase):
    settings = {"daily_loss_pct": D("1.5"), "risk_pct": D(1)}

    def lose_one_trade(self, sig_id, symbol="BTCUSDT"):
        r = self.signal(action="buy", symbol=symbol, id=sig_id, sl_pct=1)
        self.assertEqual(r["status"], "filled", r)
        self.ex.set_price(symbol, D(r["sl"]) - 100)  # stop hit: about -1%
        self.ex.set_price(symbol, D(r["entry"]))     # back to the old price for the next trade

    def test_blocks_entries_after_limit(self):
        self.lose_one_trade("d1")
        self.assertEqual(self.signal(action="buy", id="d2", sl_pct=1)["status"], "filled")  # -1% < 1.5%
        self.ex.set_price("BTCUSDT", 59000)  # second stop: about -2% today
        r = self.signal(action="buy", id="d3", sl_pct=1)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("daily loss limit", r["reason"])
        self.assertEqual(self.ex.calls["place_order"], 2)  # d1 and d2 only, nothing for d3
        self.assertTrue(self.bot.status()["daily_limit_hit"])

    def test_unrealized_loss_counts_and_flattens(self):
        r = self.signal(action="buy", id="d4", sl_pct=3, risk=2)  # 0.107 BTC, stop at 58 200
        self.assertEqual(r["status"], "filled")
        self.ex.set_price("BTCUSDT", 58300)  # -1700 x 0.107 = about -1.8% floating, stop not hit
        self.assertEqual(len(self.ex.positions()), 1)
        self.bot.tick()  # the server's periodic check
        self.assertEqual(self.ex.positions(), [], "positions closed on the daily limit")
        kinds = [(r["action"], r["status"]) for r in self.journal()]
        self.assertIn(("daily_limit", "done"), kinds)
        self.assertEqual(self.signal(action="sell", id="d5", sl_pct=1)["status"], "rejected")

    def test_no_flatten_when_disabled(self):
        self.cfg.flatten_on_daily_limit = False
        self.signal(action="buy", id="d6", sl_pct=3, risk=2)
        self.ex.set_price("BTCUSDT", 58300)
        self.bot.tick()
        self.assertEqual(len(self.ex.positions()), 1)
        self.assertEqual(self.signal(action="buy", symbol="ETHUSDT", id="d7", sl_pct=1)["status"], "rejected")

    def test_restart_keeps_the_days_anchor(self):
        self.lose_one_trade("d8")  # anchor 10 000, now about 9 900
        self.restart()
        self.assertEqual(self.store.get("day_start_equity"), "10000")
        self.signal(action="buy", id="d9", sl_pct=1)
        self.ex.set_price("BTCUSDT", 59000)
        self.restart()
        # after a fresh start at -2% the bot must not take 9 800 as the new anchor
        r = self.signal(action="buy", id="d10", sl_pct=1)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("daily loss limit", r["reason"])

    def test_next_utc_day_resets(self):
        self.lose_one_trade("d11")
        self.lose_one_trade("d12")
        self.assertEqual(self.signal(action="buy", id="d13", sl_pct=1)["status"], "rejected")
        self.clock.advance(14 * 3600)  # 10:00 -> 00:00 UTC next day
        r = self.signal(action="buy", id="d14", sl_pct=1)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(self.store.get("day"), "2026-10-18")
        self.assertLess(D(self.store.get("day_start_equity")), D(10000))

    def test_closing_is_allowed_while_halted(self):
        self.cfg.flatten_on_daily_limit = False
        self.signal(action="buy", id="d15", sl_pct=3, risk=2)
        self.ex.set_price("BTCUSDT", 58300)
        self.bot.tick()
        r = self.signal(action="close", id="d16")
        self.assertEqual((r["status"], r["closed"]), ("done", True))
        self.assertEqual(self.ex.positions(), [])


class KillSwitchTests(BotCase, unittest.TestCase):
    def test_kill_flattens_and_blocks(self):
        self.signal(action="buy", id="k1", sl_pct=1)
        self.signal(action="sell", symbol="ETHUSDT", id="k2", order_type="limit", price=2700, sl=2750)
        self.ex.place_order("SOLUSDT", "buy", "limit", D(1), "manual-1", price=D(100))  # not the bot's
        r = self.bot.kill("test")
        self.assertEqual((r["closed"], r["cancelled"], r["errors"]), (1, 1, []))
        self.assertEqual(self.ex.positions(), [])
        self.assertEqual([o.client_id for o in self.ex.open_orders()], ["manual-1"], "foreign order untouched")
        r = self.signal(action="buy", symbol="ETHUSDT", id="k3", sl_pct=1)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("kill switch", r["reason"])

    def test_kill_survives_restart_until_reset(self):
        self.bot.kill("news")
        self.restart()
        self.assertEqual(self.signal(action="buy", id="k4", sl_pct=1)["status"], "rejected")
        self.bot.unkill()
        self.assertEqual(self.signal(action="buy", id="k5", sl_pct=1)["status"], "filled")

    def test_kill_keeps_going_after_an_error(self):
        from cryptobot.exchange.base import NetworkError
        self.signal(action="buy", id="k6", sl_pct=1)
        self.signal(action="buy", symbol="ETHUSDT", id="k7", sl_pct=1)
        for _ in range(3):  # BTC close fails 3 times in a row (all retries)
            self.ex.fail("place_order", NetworkError("down"))
        r = self.bot.kill("test")
        self.assertEqual(r["status"], "error")
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(len(self.ex.positions()), 1, "the second position was still closed")
        self.assertEqual(self.signal(action="buy", symbol="SOLUSDT", id="k8", sl_pct=1)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
