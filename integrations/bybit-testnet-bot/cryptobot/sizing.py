"""Position size from risk.

On a USDT perpetual, P/L = qty x price move. If the stop is hit we lose
    qty x |entry - stop|  +  taker fee on the entry  +  taker fee on the exit at the stop,
so  qty = risk_cash / (|entry - stop| + fee_rate x (entry + stop)).

Then the qty is limited by free margin (with leverage), by MAX_NOTIONAL and by the exchange
maximum, and rounded DOWN to the exchange qty step: the real risk is never above what was asked.
If the result is below the exchange minimum, the signal is refused. We never round up to the
minimum, because that would risk more than the trader allowed.
Everything is Decimal: float rounding (0.1 + 0.2) has no place next to order sizes.
"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Optional

from .exchange.base import Instrument

MARGIN_BUFFER = Decimal("0.95")  # use at most 95% of free margin, the rest covers fees and moves


class SizingError(ValueError):
    pass


@dataclass
class Size:
    qty: Decimal
    risk_cash: Decimal    # money lost at the stop with this qty, fees included
    risk_pct: Decimal     # the same as % of equity
    notional: Decimal
    margin: Decimal
    capped_by: str = ""   # "", "margin", "max_notional", "max_qty"


def round_step(value: Decimal, step: Decimal, rounding=ROUND_DOWN) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step


def round_price(price: Decimal, tick: Decimal) -> Decimal:
    return round_step(price, tick, ROUND_HALF_UP)


def _loss_per_unit(entry: Decimal, stop: Decimal, fee_rate: Decimal) -> Decimal:
    dist = abs(entry - stop)
    if dist == 0:
        raise SizingError("stop is at the entry price")
    return dist + fee_rate * (entry + stop)


def _finish(qty, entry, stop, equity, inst: Instrument, leverage, available, fee_rate, max_notional,
            order_type, capped_by="") -> Size:
    caps = [(inst.max_qty, "max_qty")]
    if order_type == "market":
        caps.append((inst.max_market_qty, "max_qty"))
    if available is not None:
        caps.append((available * MARGIN_BUFFER * leverage / entry, "margin"))
    if max_notional:
        caps.append((max_notional / entry, "max_notional"))
    for limit, name in caps:
        if qty > limit:
            qty, capped_by = limit, name
    qty = round_step(qty, inst.qty_step)
    if qty < inst.min_qty:
        raise SizingError(f"size {qty} is below the exchange minimum {inst.min_qty} {inst.symbol}: "
                          f"raise the risk, tighten the stop or add funds")
    notional = qty * entry
    if notional < inst.min_notional:
        raise SizingError(f"order value {notional:.2f} is below the exchange minimum {inst.min_notional}")
    risk_cash = qty * _loss_per_unit(entry, stop, fee_rate)
    return Size(qty=qty, risk_cash=risk_cash, risk_pct=risk_cash / equity * 100, notional=notional,
                margin=notional / leverage, capped_by=capped_by)


def size_by_risk(equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal, inst: Instrument,
                 leverage: Decimal, available: Optional[Decimal] = None, fee_rate: Decimal = Decimal(0),
                 max_notional: Optional[Decimal] = None, order_type: str = "market") -> Size:
    if equity <= 0:
        raise SizingError("equity is zero")
    if risk_pct <= 0:
        raise SizingError("risk must be positive")
    qty = equity * risk_pct / 100 / _loss_per_unit(entry, stop, fee_rate)
    return _finish(qty, entry, stop, equity, inst, leverage, available, fee_rate, max_notional, order_type)


def size_fixed(qty: Decimal, max_risk_pct: Decimal, equity: Decimal, entry: Decimal, stop: Decimal,
               inst: Instrument, leverage: Decimal, available: Optional[Decimal] = None,
               fee_rate: Decimal = Decimal(0), max_notional: Optional[Decimal] = None,
               order_type: str = "market") -> Size:
    """A fixed qty from the signal still goes through the exchange filters and the risk ceiling."""
    size = _finish(qty, entry, stop, equity, inst, leverage, available, fee_rate, max_notional, order_type)
    if size.risk_pct > max_risk_pct:
        raise SizingError(f"fixed qty {size.qty} risks {size.risk_pct:.2f}% of equity, above the "
                          f"{max_risk_pct}% ceiling")
    return size
