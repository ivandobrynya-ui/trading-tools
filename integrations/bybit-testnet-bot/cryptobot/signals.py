"""Signal format: the TradingView alert JSON (same idea as the MT5 bridge receiver) or CLI flags.

TradingView alert message example (Bybit perpetual chart, so {{ticker}} is BTCUSDT.P):
  {"secret": "...", "id": "{{ticker}}-{{interval}}-{{time}}-buy", "action": "buy",
   "symbol": "{{exchange}}:{{ticker}}", "sl_pct": 1.5, "tp_r": 2, "risk": 0.5, "leverage": 5}

Fields
  action      buy | sell | close | close_all | cancel                          (required)
  symbol      BTCUSDT, BYBIT:BTCUSDT.P -> BTCUSDT          (required except for close_all)
  id          unique signal id; or `time` (bar time) and the id is a hash of symbol+action+time
  order_type  market (default) | limit; limit needs `price`
  sl | sl_pct           stop loss price, or distance in % of the entry price
  tp | tp_pct | tp_r    take profit price, % distance, or a multiple of the stop distance (R)
  risk        % of equity to lose if the stop is hit (capped by MAX_RISK_PCT)
  qty         fixed quantity instead of risk sizing
  leverage    1..MAX_LEVERAGE
  comment     free text, kept in the journal
"""
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

ACTIONS = {"buy", "sell", "close", "close_all", "cancel"}
ENTRY_ACTIONS = {"buy", "sell"}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


@dataclass
class Signal:
    id: str
    action: str
    symbol: str = ""
    order_type: str = "market"
    price: Optional[Decimal] = None
    sl: Optional[Decimal] = None
    sl_pct: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    tp_pct: Optional[Decimal] = None
    tp_r: Optional[Decimal] = None
    risk: Optional[Decimal] = None
    qty: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
    comment: str = ""
    source: str = "webhook"
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_entry(self):
        return self.action in ENTRY_ACTIONS


def normalize_symbol(s: str) -> str:
    """'BYBIT:BTCUSDT.P' -> 'BTCUSDT'. TradingView adds the exchange prefix and '.P' for perpetuals."""
    s = str(s or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith(".P"):
        s = s[:-2]
    return s


def _num(payload, key, max_value=None) -> Optional[Decimal]:
    v = payload.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError(f"{key} must be a number")
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if not d.is_finite():
        raise ValueError(f"{key} must be a finite number")
    if d <= 0:
        raise ValueError(f"{key} must be positive")
    if max_value is not None and d > max_value:
        raise ValueError(f"{key} above {max_value} is refused")
    return d


def parse(payload, source: str = "webhook", max_risk: Decimal = Decimal("2"),
          max_leverage: Decimal = Decimal("10")) -> Signal:
    """Validate a dict (webhook JSON or CLI flags) and return a Signal. Raises ValueError with a
    reason a person can read; nothing is sent to the exchange for an invalid signal."""
    if not isinstance(payload, dict):
        raise ValueError("signal must be a JSON object")
    action = str(payload.get("action", "")).strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")
    symbol = normalize_symbol(payload.get("symbol", ""))
    if action != "close_all" and not SYMBOL_RE.match(symbol):
        raise ValueError("symbol is missing or invalid")

    sig_id = str(payload.get("id", "") or "").strip()
    if sig_id:
        if not ID_RE.match(sig_id):
            raise ValueError("id may contain only letters, digits and . _ : - (max 64)")
    else:
        bar_time = str(payload.get("time", "") or "").strip()
        if not bar_time:
            raise ValueError("send an id or the bar time ({{time}}) for duplicate protection")
        sig_id = "h" + hashlib.sha1(f"{symbol}|{action}|{bar_time}".encode()).hexdigest()[:16]

    sig = Signal(id=sig_id, action=action, symbol=symbol, source=source,
                 raw={k: v for k, v in payload.items() if k != "secret"})
    sig.comment = re.sub(r"[^A-Za-z0-9 ._\-]", "", str(payload.get("comment", "") or ""))[:40]
    if not sig.is_entry:
        return sig

    sig.order_type = str(payload.get("order_type", "market") or "market").strip().lower()
    if sig.order_type not in ("market", "limit"):
        raise ValueError("order_type must be market or limit")
    sig.price = _num(payload, "price")
    if sig.order_type == "limit" and sig.price is None:
        raise ValueError("a limit order needs a price")
    sig.sl, sig.sl_pct = _num(payload, "sl"), _num(payload, "sl_pct", Decimal(50))
    sig.tp, sig.tp_pct, sig.tp_r = _num(payload, "tp"), _num(payload, "tp_pct", Decimal(500)), _num(payload, "tp_r", Decimal(50))
    sig.risk = _num(payload, "risk", max_risk)
    sig.qty = _num(payload, "qty")
    sig.leverage = _num(payload, "leverage", max_leverage)
    if sig.leverage is not None and sig.leverage < 1:
        raise ValueError("leverage must be at least 1")
    if sig.sl is not None and sig.sl_pct is not None:
        raise ValueError("send either sl or sl_pct, not both")
    if sum(x is not None for x in (sig.tp, sig.tp_pct, sig.tp_r)) > 1:
        raise ValueError("send only one of tp, tp_pct, tp_r")
    if sig.risk is not None and sig.qty is not None:
        raise ValueError("send either risk or qty, not both")
    return sig
