"""Restart recovery: the bot stops (crash or restart), the market keeps moving, the bot comes back
and must rebuild its picture from the exchange's open orders and positions."""
import unittest
from decimal import Decimal

from cryptobot.engine import client_id
from cryptobot.exchange.base import NetworkError
from helpers import BotCase, Crash

D = Decimal


class ResyncTests(BotCase, unittest.TestCase):
    def test_crash_after_order_reached_exchange(self):
        """Process dies right after sending: journal says 'sending', the exchange has the position."""
        self.ex.fail("place_order", Crash(), after=True)
        with self.assertRaises(Crash):
            self.signal(action="buy", id="r1", sl_pct=1)
        self.assertEqual(self.journal()[0]["status"], "sending")
        self.restart()
        report = self.bot.start()
        self.assertEqual(report["updated"], ["#1 BTCUSDT: sending -> filled"])
        row = self.journal()[0]
        self.assertEqual(row["status"], "filled")
        self.assertEqual(row["fill_price"], "60000")
        self.assertEqual(report["unknown_positions"], [])

    def test_crash_before_order_reached_exchange(self):
        self.ex.fail("place_order", Crash())
        with self.assertRaises(Crash):
            self.signal(action="buy", id="r2", sl_pct=1)
        self.restart()
        self.bot.start()
        row = self.journal()[0]
        self.assertEqual(row["status"], "error")
        self.assertIn("never reached", row["reason"])
        # the id stays used: TradingView re-sending it must not open a late trade
        self.assertEqual(self.signal(action="buy", id="r2", sl_pct=1)["status"], "duplicate")

    def test_crash_while_checking_the_signal(self):
        self.ex.fail("balance", Crash())
        with self.assertRaises(Crash):
            self.signal(action="buy", id="r2b", sl_pct=1)
        self.assertEqual(self.journal()[0]["status"], "received")
        self.restart()
        self.assertEqual(self.bot.start()["updated"], ["#1 BTCUSDT: received -> error"])
        self.assertEqual(self.ex.positions(), [])

    def test_offline_fills_cancels_and_stops(self):
        self.signal(action="buy", id="r3", sl_pct=1)                                          # BTC long
        self.signal(action="sell", symbol="ETHUSDT", id="r4", order_type="limit", price=2600, sl=2650)
        self.signal(action="buy", symbol="SOLUSDT", id="r5", order_type="limit", price=140, sl=135)
        self.restart()
        # while the bot is down: BTC stop hit, ETH limit fills, SOL order cancelled by hand on the website
        self.ex.set_price("BTCUSDT", 59000)
        self.ex.set_price("ETHUSDT", 2610)
        self.ex.cancel_order("SOLUSDT", client_id("r5", "E"))
        report = self.bot.start()
        self.assertCountEqual(report["updated"], [
            "#1 BTCUSDT: filled -> closed, P/L -49.93",
            "#2 ETHUSDT: placed -> filled",
            "#3 SOLUSDT: placed -> cancelled"])
        rows = {r["symbol"]: r for r in self.journal()}
        self.assertEqual(rows["BTCUSDT"]["status"], "closed")
        self.assertLess(D(rows["BTCUSDT"]["realized_pnl"]), 0)
        self.assertEqual(D(rows["ETHUSDT"]["fill_price"]), D(2600))
        self.assertEqual(rows["SOLUSDT"]["status"], "cancelled")

    def test_position_that_lost_its_stop_gets_it_back(self):
        r = self.signal(action="buy", id="r6", sl_pct=1, tp_r=2)
        self.restart()
        self.ex.set_position_stops("BTCUSDT", None, None)  # someone removed SL/TP by hand
        report = self.bot.start()
        self.assertEqual(report["protected"], [f"BTCUSDT: SL {r['sl']} set again"])
        pos = self.ex.positions()[0]
        self.assertEqual((str(pos.sl), str(pos.tp)), (r["sl"], r["tp"]))

    def test_unknown_orders_and_positions_are_reported_not_touched(self):
        self.ex.place_order("ETHUSDT", "buy", "market", D(1), "manual-1")  # opened on the website, no stop
        self.ex.place_order("BTCUSDT", "buy", "limit", D("0.01"), "cb0000000000000000E", price=D(50000))
        report = self.bot.start()
        self.assertEqual(report["unknown_positions"], ["ETHUSDT long 1 NO STOP"])
        self.assertEqual(report["unknown_orders"], ["BTCUSDT buy 0.01 (cb0000000000000000E)"])
        self.assertEqual(len(self.ex.positions()), 1)
        self.assertEqual(len(self.ex.open_orders()), 1)

    def test_exchange_is_the_source_of_truth_after_restart(self):
        self.signal(action="buy", id="r7", sl_pct=1)
        self.restart()
        self.bot.start()
        r = self.signal(action="buy", id="r8", sl_pct=1)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("already long", r["reason"])
        r = self.signal(action="sell", id="r9", sl_pct=1)  # reverse still works after the restart
        self.assertEqual(r["status"], "filled")
        self.assertEqual([(p.side, p.symbol) for p in self.ex.positions()], [("short", "BTCUSDT")])
        statuses = {r["signal_id"]: r["status"] for r in self.journal()}
        self.assertEqual(statuses["r7"], "closed")

    def test_resync_twice_changes_nothing(self):
        self.signal(action="sell", symbol="ETHUSDT", id="r10", order_type="limit", price=2600, sl=2650)
        self.ex.set_price("ETHUSDT", 2600)
        first = self.bot.resync()
        second = self.bot.resync()
        self.assertEqual(first["updated"], ["#1 ETHUSDT: placed -> filled"])
        self.assertEqual(second["updated"], [])

    def test_reconnect_with_backoff(self):
        self.ex.fail("connect", NetworkError("exchange maintenance"))
        self.ex.fail("connect", NetworkError("exchange maintenance"))
        report = self.bot.start()
        self.assertEqual(self.ex.calls["connect"], 3)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertIn("updated", report)

    def test_tick_reconnects_after_outage(self):
        self.bot.start()
        self.ex.fail("positions", NetworkError("down"))
        self.assertIn("error", self.bot.tick())
        self.bot.tick()  # next tick reconnects first, then resyncs
        self.assertEqual(self.ex.calls["connect"], 2)


if __name__ == "__main__":
    unittest.main()
