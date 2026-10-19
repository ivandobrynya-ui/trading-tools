"""Scripted walk-through on the mock exchange: python demo.py

Every safety feature in one run, with a fresh mock account (10 000 USDT) in a temp folder.
Nothing is sent anywhere; no keys needed. Prints each step and the final journal.
"""
import logging
import tempfile
from decimal import Decimal
from pathlib import Path

from cryptobot.config import Settings
from cryptobot.engine import Bot
from cryptobot.exchange.base import NetworkError
from cryptobot.exchange.mock import MockExchange
from cryptobot.signals import parse
from cryptobot.store import Store

logging.getLogger("cryptobot").addHandler(logging.NullHandler())
logging.getLogger("cryptobot").propagate = False


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cryptobot_demo_"))
    cfg = Settings(exchange="mock", data_dir=tmp, daily_loss_pct=Decimal(2), risk_pct=Decimal(1))
    ex = MockExchange(equity="10000", fee_rate=cfg.fee_rate)
    store = Store(tmp / "bot.db")
    bot = Bot(ex, store, cfg, sleep=lambda s: None)

    def step(title, **signal):
        nonlocal bot
        res = bot.handle(parse(signal, "demo", cfg.max_risk_pct, cfg.max_leverage)) if signal else None
        short = {k: v for k, v in (res or {}).items() if k in ("status", "qty", "entry", "sl", "tp", "risk_pct", "reason")}
        print(f"- {title}\n    {short if res else ''}")

    print("Mock account: 10 000 USDT, risk 1% per trade, daily loss limit 2%\n")
    step("BUY BTC, stop 1%, target 2R", action="buy", symbol="BYBIT:BTCUSDT.P", id="a1", sl_pct=1, tp_r=2)
    step("the same alert again (TradingView retry)", action="buy", symbol="BTCUSDT", id="a1", sl_pct=1, tp_r=2)
    step("second BUY BTC while long", action="buy", symbol="BTCUSDT", id="a2", sl_pct=1)
    step("SELL with the stop on the wrong side", action="sell", symbol="BTCUSDT", id="a3", sl=59000)
    step("BUY ETH without a stop", action="buy", symbol="ETHUSDT", id="a4")
    step("SELL ETH limit 2600, stop 2650", action="sell", symbol="ETHUSDT", id="a5", order_type="limit",
         price=2600, sl=2650, tp_pct=4)
    ex.fail("place_order", NetworkError("read timeout"), after=True)
    step("BUY SOL, the exchange answer is lost (timeout)", action="buy", symbol="SOLUSDT", id="a6", sl_pct=2)
    print(f"    SOL orders on the exchange: {sum(o.symbol == 'SOLUSDT' for o in ex.orders.values())}")

    print("\n--- bot process stops; while it is down BTC hits the stop and ETH limit fills ---")
    ex.set_price("BTCUSDT", 59000)
    ex.set_price("ETHUSDT", 2620)
    store.close()
    store = Store(tmp / "bot.db")
    bot = Bot(ex, store, cfg, sleep=lambda s: None)
    report = bot.start()
    print(f"- restart + resync: {report['updated']}")

    ex.set_price("SOLUSDT", 146)  # SOL stop hit
    bot.resync()
    step("BUY BTC again after two stops (about -2% today)", action="buy", symbol="BTCUSDT", id="a7", sl_pct=1)
    r = bot.kill("demo")
    print(f"- kill switch: {r}")
    step("BUY after the kill switch", action="sell", symbol="SOLUSDT", id="a8", sl_pct=1)

    print("\nJournal:")
    for r in reversed(store.rows()):
        print(f"  #{r['id']:<2} {r['action'] or '':10} {r['symbol'] or '':8} {r['status']:9} "
              f"qty={r['qty'] or '-':6} risk%={r['risk_pct'] or '-':5} pnl={r['realized_pnl'] or '-':9} {r['reason'] or ''}")
    print(f"\nEquity at the end: {bot.status()['equity']} USDT. Files: {tmp}")


if __name__ == "__main__":
    main()
