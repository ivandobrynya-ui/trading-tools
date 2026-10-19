"""Position sizing by risk %: formula, fees, exchange filters, caps, rounding."""
import random
import unittest
from decimal import Decimal

from cryptobot.exchange.mock import DEFAULT_INSTRUMENTS
from cryptobot.sizing import SizingError, round_price, size_by_risk, size_fixed
from helpers import BotCase

D = Decimal
BTC, ETH, SOL = (DEFAULT_INSTRUMENTS[s] for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"))


class SizingTests(unittest.TestCase):
    def test_long_without_fees(self):
        # 1% of 10 000 = 100 USDT; stop 600 away -> 0.1666 BTC -> rounded DOWN to 0.166
        s = size_by_risk(D(10000), D(1), D(60000), D(59400), BTC, D(5))
        self.assertEqual(s.qty, D("0.166"))
        self.assertEqual(s.risk_cash, D("99.600"))
        self.assertEqual(s.notional, D("9960.000"))
        self.assertEqual(s.margin, D("1992"))

    def test_fees_are_part_of_the_risk(self):
        # loss per BTC at the stop = 600 + 0.055% x (60000 + 59400) = 665.67
        s = size_by_risk(D(10000), D(1), D(60000), D(59400), BTC, D(5), fee_rate=D("0.00055"))
        self.assertEqual(s.qty, D("0.150"))
        self.assertLessEqual(s.risk_cash, D(100))
        self.assertGreater(s.risk_cash, D(99))

    def test_short_uses_the_same_distance(self):
        long = size_by_risk(D(5000), D(1), D(2500), D(2450), ETH, D(3))
        short = size_by_risk(D(5000), D(1), D(2500), D(2550), ETH, D(3))
        self.assertEqual(long.qty, D("1.00"))
        self.assertEqual(short.qty, long.qty)

    def test_never_rounds_up(self):
        # 100 / 599 = 0.16694... -> 0.166, not 0.167 (0.167 would risk 100.03 > 100)
        s = size_by_risk(D(10000), D(1), D(60000), D(59401), BTC, D(5))
        self.assertEqual(s.qty, D("0.166"))

    def test_too_small_is_refused_not_rounded_up(self):
        # 0.5 USDT of risk with a 600 USDT stop = 0.0008 BTC < min 0.001
        with self.assertRaisesRegex(SizingError, "below the exchange minimum"):
            size_by_risk(D(100), D("0.5"), D(60000), D(59400), BTC, D(5))

    def test_min_order_value(self):
        # 1 USDT risk, stop 2.5 away -> 0.4 SOL x 10 = 4 USDT < 5 USDT minimum order value
        with self.assertRaisesRegex(SizingError, "order value"):
            size_by_risk(D(100), D(1), D(10), D("7.5"), SOL, D(2))

    def test_capped_by_free_margin(self):
        # tight stop: risk wants 3.33 BTC (200k USDT), but 10k free x 5x leverage x 95% buys 0.791
        s = size_by_risk(D(10000), D(2), D(60000), D(59940), BTC, D(5), available=D(10000))
        self.assertEqual(s.qty, D("0.791"))
        self.assertEqual(s.capped_by, "margin")
        self.assertLess(s.risk_pct, D(2))

    def test_capped_by_max_notional_and_market_max(self):
        s = size_by_risk(D(10000), D(1), D(60000), D(59400), BTC, D(5), max_notional=D(3000))
        self.assertEqual((s.qty, s.capped_by), (D("0.05"), "max_notional"))
        s = size_by_risk(D(10 ** 9), D(1), D(60000), D(59400), BTC, D(100))
        self.assertEqual((s.qty, s.capped_by), (BTC.max_market_qty, "max_qty"))

    def test_stop_at_entry(self):
        with self.assertRaises(SizingError):
            size_by_risk(D(10000), D(1), D(60000), D(60000), BTC, D(5))

    def test_fixed_qty_respects_risk_ceiling(self):
        ok = size_fixed(D("0.05"), D(2), D(10000), D(60000), D(59400), BTC, D(5))
        self.assertEqual(ok.qty, D("0.05"))
        with self.assertRaisesRegex(SizingError, "ceiling"):
            size_fixed(D("1"), D(2), D(10000), D(60000), D(59400), BTC, D(5))  # 600 USDT = 6%

    def test_property_largest_qty_within_risk(self):
        """Random cases: qty is a multiple of the step, risk <= asked, and one more step would be too much."""
        rnd = random.Random(7)
        for _ in range(500):
            inst = rnd.choice([BTC, ETH, SOL])
            entry = round_price(D(rnd.uniform(5, 90000)), inst.tick_size)
            stop = round_price(entry * D(1 + rnd.choice([-1, 1]) * rnd.uniform(0.002, 0.08)), inst.tick_size)
            if stop == entry:
                continue
            equity, risk = D(rnd.randint(500, 200000)), D(str(round(rnd.uniform(0.1, 2), 2)))
            try:
                s = size_by_risk(equity, risk, entry, stop, inst, D(10), fee_rate=D("0.00055"))
            except SizingError:
                continue
            self.assertEqual(s.qty % inst.qty_step, 0)
            allowed = equity * risk / 100
            self.assertLessEqual(s.risk_cash, allowed)
            if not s.capped_by:
                per_unit = s.risk_cash / s.qty
                self.assertGreater((s.qty + inst.qty_step) * per_unit, allowed)


class SizingOnExchangeTests(BotCase, unittest.TestCase):
    def test_loss_at_stop_equals_planned_risk(self):
        """End to end on the mock: the realized loss at the stop matches the journal's planned risk."""
        r = self.signal(action="buy", id="s1", sl_pct=1, risk=1, leverage=5)
        self.assertEqual(r["status"], "filled")
        self.ex.set_price("BTCUSDT", 59000)  # through the stop at 59400
        self.assertEqual(self.ex.positions(), [])
        self.bot.resync()
        row = self.journal()[0]
        self.assertEqual(row["status"], "closed")
        self.assertAlmostEqual(float(row["realized_pnl"]), -float(row["risk_cash"]), delta=0.01)
        self.assertLessEqual(-float(row["realized_pnl"]), 100.0)

    def test_short_limit_entry_sizes_from_limit_price(self):
        r = self.signal(action="sell", symbol="ETHUSDT", id="s2", order_type="limit", price=2600, sl=2650, risk=1)
        self.assertEqual(r["status"], "placed")
        # (10 000 x 1%) / (50 + 0.055% x 5250) = 1.89 ETH
        self.assertEqual(r["qty"], "1.89")


if __name__ == "__main__":
    unittest.main()
