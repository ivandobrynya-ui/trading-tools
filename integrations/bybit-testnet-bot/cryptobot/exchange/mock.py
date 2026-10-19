"""In-memory exchange for tests and for running the bot without API keys.

Behaves like a USDT perpetual exchange in one-way mode, close enough to test the bot's logic:
  - instrument filters (qty step, min qty, min notional, tick size, max leverage);
  - market orders fill at the current price, limit orders rest until the price reaches them;
  - SL/TP attached to an order protect the whole position once it fills;
  - taker fee on every fill, realized P/L after fees, equity = wallet + unrealized P/L;
  - unique client order ids (a second order with the same id is refused);
  - failures can be injected: rate limits, timeouts before or after the order reaches the "exchange".

Prices move only when a test (or the `price` CLI command) calls set_price().
With `path`, the state is saved to a JSON file after every change, so the CLI can stop and start
again and still see the same positions: that is how a bot restart is simulated.
"""
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .base import (Balance, DuplicateOrder, Exchange, InsufficientFunds, Instrument, InvalidOrder,
                   Order, OrderNotFound, Position)

D = Decimal

# Filters copied from Bybit testnet instruments-info (linear) on 17.10.2026:
# symbol, qty step, min qty, max qty, max market qty, tick, min notional, max leverage
DEFAULT_INSTRUMENTS = {
    "BTCUSDT": Instrument("BTCUSDT", D("0.001"), D("0.001"), D("1190"), D("500"), D("0.1"), D("5"), D("150")),
    "ETHUSDT": Instrument("ETHUSDT", D("0.01"), D("0.01"), D("10000"), D("2000"), D("0.01"), D("5"), D("150")),
    "SOLUSDT": Instrument("SOLUSDT", D("0.1"), D("0.1"), D("96000"), D("12000"), D("0.01"), D("5"), D("100")),
}
DEFAULT_PRICES = {"BTCUSDT": D("60000"), "ETHUSDT": D("2500"), "SOLUSDT": D("150")}


def _d(v):
    return None if v is None or v == "" else D(str(v))


class MockExchange(Exchange):
    name = "mock"

    def __init__(self, equity="10000", fee_rate="0.00055", path: Optional[Path] = None, clock=time.time):
        self.clock = clock
        self.path = Path(path) if path else None
        self.fee_rate = D(fee_rate)
        self.wallet = D(equity)
        self.instruments = dict(DEFAULT_INSTRUMENTS)
        self.prices = dict(DEFAULT_PRICES)
        self.pos = {}        # symbol -> dict(side, qty, entry, leverage, sl, tp, fees)
        self.orders = {}     # client_id -> Order
        self.leverage = {}   # symbol -> Decimal
        self.closed = []     # [symbol, pnl after fees, ms]
        self.seq = 0
        self.failures = []   # [method, exception, after]; see fail()
        self.calls = {}      # method -> number of calls, for tests
        self.drop_attached_stops = False  # simulate an exchange that accepts the order but not its SL/TP
        if self.path and self.path.exists():
            self._load()

    # ---- test helpers -------------------------------------------------------------------------
    def fail(self, method: str, exc: Exception, after: bool = False):
        """Make the next call of `method` raise `exc`. With after=True the call is executed first,
        then the error is raised: the exchange got the order but the answer was lost."""
        self.failures.append([method, exc, after])

    def set_price(self, symbol: str, price) -> None:
        """Move the market: fill resting limit orders and trigger SL/TP the price crossed."""
        price = D(str(price))
        self.prices[symbol] = price
        for o in list(self.orders.values()):
            if o.symbol == symbol and o.status == "open" and o.type == "limit":
                if (o.side == "buy" and price <= o.price) or (o.side == "sell" and price >= o.price):
                    self._fill(o, o.price)
        p = self.pos.get(symbol)
        if p:
            long = p["side"] == "long"
            hit = None
            if p["sl"] is not None and ((long and price <= p["sl"]) or (not long and price >= p["sl"])):
                hit = p["sl"]
            elif p["tp"] is not None and ((long and price >= p["tp"]) or (not long and price <= p["tp"])):
                hit = p["tp"]
            if hit is not None:
                self.seq += 1
                o = Order(f"m{self.seq}", "", symbol, "sell" if long else "buy", "market", p["qty"], "open",
                          reduce_only=True, created_ms=self._ms())
                self._fill(o, hit)
        self._save()

    # ---- Exchange interface -------------------------------------------------------------------
    def connect(self):
        self._hook("connect")

    def instrument(self, symbol):
        self._hook("instrument")
        if symbol not in self.instruments:
            raise InvalidOrder(f"unknown symbol {symbol}")
        return self.instruments[symbol]

    def last_price(self, symbol):
        self._hook("last_price")
        self.instrument(symbol)
        return self.prices[symbol]

    def balance(self):
        self._hook("balance")
        upnl = sum((self._upnl(s, p) for s, p in self.pos.items()), D(0))
        equity = self.wallet + upnl
        used = sum((p["qty"] * p["entry"] / p["leverage"] for p in self.pos.values()), D(0))
        used += sum((o.qty * o.price / self.leverage.get(o.symbol, D(1))
                     for o in self.orders.values() if o.status == "open" and not o.reduce_only), D(0))
        return Balance(equity=equity, available=max(equity - used, D(0)))

    def positions(self, symbol=None):
        self._hook("positions")
        out = []
        for s, p in self.pos.items():
            if symbol is None or s == symbol:
                out.append(Position(s, p["side"], p["qty"], p["entry"], p["leverage"],
                                    self._upnl(s, p), p["sl"], p["tp"]))
        return out

    def open_orders(self, symbol=None):
        self._hook("open_orders")
        return [o for o in self.orders.values()
                if o.status == "open" and (symbol is None or o.symbol == symbol)]

    def get_order(self, symbol, client_id):
        self._hook("get_order")
        return self.orders.get(client_id)

    def set_leverage(self, symbol, leverage):
        self._hook("set_leverage")
        inst = self.instrument(symbol)
        lev = D(str(leverage))
        if lev < 1 or lev > inst.max_leverage:
            raise InvalidOrder(f"leverage {lev} outside 1..{inst.max_leverage}")
        self.leverage[symbol] = lev
        self._save()

    def place_order(self, symbol, side, type, qty, client_id, price=None, sl=None, tp=None, reduce_only=False):
        exc = self._hook("place_order", defer=True)
        inst = self.instrument(symbol)
        qty = D(str(qty))
        if client_id and client_id in self.orders:
            raise DuplicateOrder(f"client order id {client_id} already used")
        if side not in ("buy", "sell") or type not in ("market", "limit"):
            raise InvalidOrder("bad side or type")
        if qty <= 0 or qty % inst.qty_step != 0:
            raise InvalidOrder(f"qty {qty} is not a multiple of {inst.qty_step}")
        if type == "limit":
            if price is None or price % inst.tick_size != 0:
                raise InvalidOrder(f"limit price {price} is not a multiple of tick {inst.tick_size}")
        ref = price if type == "limit" else self.prices[symbol]
        pos = self.pos.get(symbol)
        if reduce_only:
            if sl is not None or tp is not None:
                raise InvalidOrder("TP/SL cannot be set on a reduce-only order")
            if not pos or (pos["side"] == "long") == (side == "buy"):
                raise InvalidOrder("reduce-only order has no position to reduce")
            qty = min(qty, pos["qty"])
        else:
            if qty < inst.min_qty or qty * ref < inst.min_notional:
                raise InvalidOrder(f"qty {qty} below the minimum order size")
            if type == "market" and qty > inst.max_market_qty:
                raise InvalidOrder(f"qty {qty} above the max market order size")
            lev = self.leverage.get(symbol, D(1))
            if qty * ref / lev > self.balance().available:
                raise InsufficientFunds("available balance is insufficient")
            for name, level, below in (("SL", sl, side == "buy"), ("TP", tp, side == "sell")):
                if level is not None and ((level >= ref) if below else (level <= ref)):
                    raise InvalidOrder(f"{name} {level} is on the wrong side of {ref}")
        self.seq += 1
        o = Order(f"m{self.seq}", client_id, symbol, side, type, qty, "open", price=price,
                  reduce_only=reduce_only, sl=None if self.drop_attached_stops else sl,
                  tp=None if self.drop_attached_stops else tp, created_ms=self._ms())
        self.orders[client_id or o.order_id] = o
        cur = self.prices[symbol]
        if type == "market":
            self._fill(o, cur)
        elif (side == "buy" and cur <= price) or (side == "sell" and cur >= price):
            self._fill(o, cur)  # marketable limit fills at once
        self._save()
        if exc:
            raise exc
        return o

    def cancel_order(self, symbol, client_id):
        self._hook("cancel_order")
        o = self.orders.get(client_id)
        if not o or o.status != "open":
            raise OrderNotFound(f"no open order {client_id}")
        o.status = "cancelled"
        self._save()

    def set_position_stops(self, symbol, sl, tp):
        self._hook("set_position_stops")
        p = self.pos.get(symbol)
        if not p:
            raise InvalidOrder(f"no position on {symbol}")
        cur = self.prices[symbol]
        long = p["side"] == "long"
        if sl is not None and ((sl >= cur) if long else (sl <= cur)):
            raise InvalidOrder(f"SL {sl} is on the wrong side of {cur}")
        if tp is not None and ((tp <= cur) if long else (tp >= cur)):
            raise InvalidOrder(f"TP {tp} is on the wrong side of {cur}")
        p["sl"], p["tp"] = sl, tp
        self._save()

    def closed_pnl(self, symbol, since_ms):
        self._hook("closed_pnl")
        return sum((D(pnl) for s, pnl, ms in self.closed if s == symbol and ms >= since_ms), D(0))

    # ---- internals ----------------------------------------------------------------------------
    def _ms(self):
        return int(self.clock() * 1000)

    def _hook(self, method, defer=False):
        """Count the call and raise an injected failure. For after=True failures with defer=True
        the error is returned instead, and the caller raises it after doing the work."""
        self.calls[method] = self.calls.get(method, 0) + 1
        for i, (m, exc, after) in enumerate(self.failures):
            if m == method:
                del self.failures[i]
                if after and defer:
                    return exc
                raise exc
        return None

    def _upnl(self, symbol, p):
        diff = self.prices[symbol] - p["entry"]
        return diff * p["qty"] if p["side"] == "long" else -diff * p["qty"]

    def _fill(self, o: Order, price: Decimal):
        fee = o.qty * price * self.fee_rate
        self.wallet -= fee
        o.status, o.filled_qty, o.avg_price = "filled", o.qty, price
        side = "long" if o.side == "buy" else "short"
        p = self.pos.get(o.symbol)
        lev = self.leverage.get(o.symbol, D(1))
        if p is None:
            self.pos[o.symbol] = dict(side=side, qty=o.qty, entry=price, leverage=lev, sl=o.sl, tp=o.tp, fees=fee)
            return
        if p["side"] == side:  # add to the position
            total = p["qty"] + o.qty
            p["entry"] = (p["entry"] * p["qty"] + price * o.qty) / total
            p["qty"], p["fees"] = total, p["fees"] + fee
            if o.sl is not None or o.tp is not None:
                p["sl"], p["tp"] = o.sl, o.tp
            return
        closed = min(o.qty, p["qty"])
        gross = (price - p["entry"]) * closed * (1 if p["side"] == "long" else -1)
        open_fee = p["fees"] * closed / p["qty"]
        self.wallet += gross
        self.closed.append([o.symbol, str(gross - open_fee - fee * closed / o.qty), self._ms()])
        p["qty"] -= closed
        p["fees"] -= open_fee
        rest = o.qty - closed
        if p["qty"] == 0:
            del self.pos[o.symbol]
            if rest > 0 and not o.reduce_only:  # one-way mode: a bigger opposite order flips the position
                self.pos[o.symbol] = dict(side=side, qty=rest, entry=price, leverage=lev,
                                          sl=o.sl, tp=o.tp, fees=fee * rest / o.qty)

    def _save(self):
        if not self.path:
            return
        orders = {k: {f: (str(v) if isinstance(v, D) else v) for f, v in o.__dict__.items()}
                  for k, o in self.orders.items()}
        pos = {s: {k: (str(v) if isinstance(v, D) else v) for k, v in p.items()} for s, p in self.pos.items()}
        data = {"wallet": str(self.wallet), "prices": {k: str(v) for k, v in self.prices.items()},
                "pos": pos, "orders": orders, "leverage": {k: str(v) for k, v in self.leverage.items()},
                "closed": self.closed, "seq": self.seq}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _load(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.wallet = D(data["wallet"])
        self.prices.update({k: D(v) for k, v in data["prices"].items()})
        self.pos = {s: {k: (_d(v) if k not in ("side",) else v) for k, v in p.items()} for s, p in data["pos"].items()}
        self.orders = {}
        for k, o in data["orders"].items():
            for f in ("qty", "price", "filled_qty", "avg_price", "sl", "tp"):
                o[f] = _d(o[f])
            self.orders[k] = Order(**o)
        self.leverage = {k: D(v) for k, v in data["leverage"].items()}
        self.closed = data["closed"]
        self.seq = data["seq"]
