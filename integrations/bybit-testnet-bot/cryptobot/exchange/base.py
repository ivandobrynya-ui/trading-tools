"""Exchange adapter interface.

The bot talks only to this interface. Adding another exchange (Binance, OKX, Phemex) means one
new class with these methods; the signal, sizing and safety logic stays the same.

Conventions for every adapter:
  - USDT-margined perpetuals, one-way position mode (one position per symbol);
  - prices and quantities are Decimal, never float;
  - side of an order is "buy"/"sell", side of a position is "long"/"short";
  - errors are raised as the ExchangeError subclasses below, so the bot can tell
    "retry later" from "this order is wrong".
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


class ExchangeError(Exception):
    """Any error reported by the exchange or on the way to it."""
    retryable = False


class NetworkError(ExchangeError):
    """Timeout, dropped connection, 5xx. The request may or may not have reached the exchange."""
    retryable = True


class RateLimitError(ExchangeError):
    """Too many requests. The exchange rejected the request, so it is safe to send it again later."""
    retryable = True


class AuthError(ExchangeError):
    """Bad key, bad signature, missing permission. Retrying will not help."""


class InsufficientFunds(ExchangeError):
    pass


class InvalidOrder(ExchangeError):
    """Bad parameters: price off tick, qty below minimum, stop on the wrong side and so on."""


class DuplicateOrder(ExchangeError):
    """This client order id was already used: the order exists, do not send it again."""


class OrderNotFound(ExchangeError):
    pass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    qty_step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    max_market_qty: Decimal
    tick_size: Decimal
    min_notional: Decimal
    max_leverage: Decimal


@dataclass
class Balance:
    equity: Decimal      # wallet balance + unrealized P/L, in the settle coin (USDT)
    available: Decimal   # free margin for new orders


@dataclass
class Position:
    symbol: str
    side: str            # "long" | "short"
    qty: Decimal
    entry_price: Decimal
    leverage: Decimal
    unrealized_pnl: Decimal = Decimal(0)
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None


@dataclass
class Order:
    order_id: str
    client_id: str
    symbol: str
    side: str            # "buy" | "sell"
    type: str            # "market" | "limit"
    qty: Decimal
    status: str          # "open" | "filled" | "cancelled" | "rejected"
    price: Optional[Decimal] = None
    filled_qty: Decimal = Decimal(0)
    avg_price: Optional[Decimal] = None
    reduce_only: bool = False
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    created_ms: int = 0
    extra: dict = field(default_factory=dict)


class Exchange(ABC):
    """What the bot needs from an exchange. Implementations: MockExchange, BybitV5."""

    name = "base"

    def connect(self) -> None:
        """Check connectivity and credentials, sync the clock. Called on start and after outages."""

    @abstractmethod
    def instrument(self, symbol: str) -> Instrument: ...

    @abstractmethod
    def last_price(self, symbol: str) -> Decimal: ...

    @abstractmethod
    def balance(self) -> Balance: ...

    @abstractmethod
    def positions(self, symbol: Optional[str] = None) -> list: ...

    @abstractmethod
    def open_orders(self, symbol: Optional[str] = None) -> list: ...

    @abstractmethod
    def get_order(self, symbol: str, client_id: str) -> Optional[Order]:
        """Open or recently closed order by client id, or None if the exchange never saw it."""

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: Decimal) -> None: ...

    @abstractmethod
    def place_order(self, symbol: str, side: str, type: str, qty: Decimal, client_id: str,
                    price: Optional[Decimal] = None, sl: Optional[Decimal] = None,
                    tp: Optional[Decimal] = None, reduce_only: bool = False) -> Order:
        """Send one order. SL/TP, if given, are attached and protect the whole position once filled."""

    @abstractmethod
    def cancel_order(self, symbol: str, client_id: str) -> None: ...

    @abstractmethod
    def set_position_stops(self, symbol: str, sl: Optional[Decimal], tp: Optional[Decimal]) -> None:
        """Set or replace SL/TP of an open position."""

    @abstractmethod
    def closed_pnl(self, symbol: str, since_ms: int) -> Decimal:
        """Realized P/L (after fees) of positions on this symbol closed since the given time."""
