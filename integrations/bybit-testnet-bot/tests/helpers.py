"""Shared test setup: a mock exchange, a bot on a temp SQLite file and a clock the test controls."""
import logging
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptobot.config import Settings
from cryptobot.engine import Bot
from cryptobot.exchange.mock import MockExchange
from cryptobot.signals import parse
from cryptobot.store import Store

D = Decimal
# the bot logs every warning it handles; keep the test output readable (run with -v for names)
logging.getLogger("cryptobot").addHandler(logging.NullHandler())
logging.getLogger("cryptobot").propagate = False


class Clock:
    """Fake time: starts 17.10.2026 10:00 UTC, moves only when the test says so."""

    def __init__(self):
        self.t = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Crash(BaseException):
    """Simulated process crash: not an ExchangeError, so the bot does not catch it."""


class BotCase:
    """Mixin for unittest.TestCase: self.ex, self.bot, self.store, self.clock, self.restart()."""

    settings = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock()
        self.sleeps = []
        self.cfg = Settings(exchange="mock", data_dir=self.dir, **self.settings)
        self.ex = MockExchange(equity="10000", fee_rate=self.cfg.fee_rate, clock=self.clock)
        self.store = Store(self.dir / "bot.db", clock=self.clock)
        self.bot = Bot(self.ex, self.store, self.cfg, clock=self.clock, sleep=self.sleeps.append)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def restart(self):
        """New Bot and Store objects on the same database file and the same exchange account:
        everything the old process had in memory is gone."""
        self.store.close()
        self.store = Store(self.dir / "bot.db", clock=self.clock)
        self.bot = Bot(self.ex, self.store, self.cfg, clock=self.clock, sleep=self.sleeps.append)
        return self.bot

    def signal(self, **kw):
        kw.setdefault("symbol", "BTCUSDT")
        return self.bot.handle(parse(kw, "test", self.cfg.max_risk_pct, self.cfg.max_leverage))

    def journal(self):
        return list(reversed(self.store.rows()))
