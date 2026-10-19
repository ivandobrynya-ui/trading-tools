"""Signal parsing (TradingView JSON / CLI) and the entry checks that refuse bad signals."""
import unittest
from decimal import Decimal

from cryptobot.signals import normalize_symbol, parse
from helpers import BotCase

D = Decimal


class ParseTests(unittest.TestCase):
    def test_tradingview_alert(self):
        s = parse({"secret": "x" * 20, "id": "BTCUSDT.P-15-1792260000000-buy", "action": "BUY",
                   "symbol": "BYBIT:BTCUSDT.P", "sl_pct": "1.5", "tp_r": 2, "risk": 0.5, "leverage": "5"})
        self.assertEqual((s.action, s.symbol, s.order_type), ("buy", "BTCUSDT", "market"))
        self.assertEqual((s.sl_pct, s.tp_r, s.risk, s.leverage), (D("1.5"), D(2), D("0.5"), D(5)))
        self.assertNotIn("secret", s.raw, "the secret is never stored in the journal")

    def test_symbols(self):
        self.assertEqual(normalize_symbol("BINANCE:ethusdt.p"), "ETHUSDT")
        self.assertEqual(normalize_symbol("SOLUSDT"), "SOLUSDT")

    def test_refused(self):
        base = {"id": "a1", "action": "buy", "symbol": "BTCUSDT", "sl_pct": 1}
        bad = [{"action": "hedge"}, {"symbol": "BTC USDT;"}, {"id": "a b"}, {"order_type": "stop"},
               {"order_type": "limit"}, {"sl": "abc"}, {"sl": -1}, {"sl": "nan"}, {"risk": 5},
               {"leverage": 50}, {"leverage": "0.5"}, {"sl": 59000}, {"tp": 1, "tp_r": 2},
               {"risk": 1, "qty": 1}, {"risk": True}]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                parse({**base, **b})
        with self.assertRaises(ValueError):
            parse({"action": "buy", "symbol": "BTCUSDT"})  # neither id nor time

    def test_close_all_needs_no_symbol(self):
        self.assertEqual(parse({"id": "x", "action": "close_all"}).action, "close_all")


class EntryCheckTests(BotCase, unittest.TestCase):
    def check_rejected(self, text, **kw):
        r = self.signal(**kw)
        self.assertEqual(r["status"], "rejected", r)
        self.assertIn(text, r["reason"])
        self.assertNotIn("place_order", self.ex.calls)

    def test_no_stop(self):
        self.check_rejected("no stop loss", action="buy", id="e1")

    def test_stop_on_wrong_side(self):
        self.check_rejected("wrong side", action="buy", id="e2", sl=61000)
        self.check_rejected("wrong side", action="sell", id="e3", sl=59000)
        self.check_rejected("wrong side", action="buy", id="e4", sl=59000, tp=59500)

    def test_symbol_not_allowed(self):
        self.cfg.symbols = ("ETHUSDT",)
        self.check_rejected("not in SYMBOLS", action="buy", id="e5", sl_pct=1)

    def test_invalid_signal_does_not_touch_open_position(self):
        self.signal(action="buy", id="e6", sl_pct=1)
        r = self.signal(action="sell", id="e7", sl=59000)  # a short with its stop below the price
        self.assertEqual(r["status"], "rejected")
        self.assertEqual([p.side for p in self.ex.positions()], ["long"], "the long was not closed")

    def test_default_stop_and_target(self):
        self.cfg.default_sl_pct, self.cfg.default_tp_r = D(2), D(3)
        r = self.signal(action="sell", id="e8")
        self.assertEqual((r["sl"], r["tp"]), ("61200.0", "56400.0"))

    def test_leverage_is_capped(self):
        self.cfg.max_leverage = D(200)
        r = self.signal(action="buy", symbol="SOLUSDT", id="e9", sl_pct=1, leverage=150)  # SOL allows 100x
        self.assertEqual(r["leverage"], "100")
        self.cfg.max_leverage, self.cfg.leverage = D(4), D(8)  # default above the ceiling
        r = self.signal(action="buy", id="e9b", sl_pct=1)
        self.assertEqual(r["leverage"], "4")
        self.assertEqual(self.ex.leverage["BTCUSDT"], D(4))

    def test_lost_stop_is_set_again_after_fill(self):
        self.ex.drop_attached_stops = True  # exchange filled the order but ignored its SL/TP
        r = self.signal(action="buy", id="e10", sl_pct=1)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(str(self.ex.positions()[0].sl), r["sl"])
        self.assertIn("set again", self.journal()[0]["reason"])

    def test_position_closed_if_stop_cannot_be_set(self):
        from cryptobot.exchange.base import InvalidOrder
        self.ex.drop_attached_stops = True
        self.ex.fail("set_position_stops", InvalidOrder("retCode 10001"))
        self.signal(action="buy", id="e11", sl_pct=1)
        self.assertEqual(self.ex.positions(), [], "no position is left without a stop")
        self.assertEqual(self.journal()[0]["status"], "closed")


if __name__ == "__main__":
    unittest.main()
