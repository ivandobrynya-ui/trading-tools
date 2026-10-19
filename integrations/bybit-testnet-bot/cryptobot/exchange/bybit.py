"""Bybit V5 REST adapter (USDT perpetuals, one-way mode) over plain `requests`.

Testnet (api-testnet.bybit.com) or Demo Trading (api-demo.bybit.com) only: this practice build
has no mainnet URL on purpose.

Errors and limits:
  - every response is checked: HTTP status first, then Bybit's retCode;
  - retCodes are mapped to the ExchangeError classes in base.py;
  - reads, leverage, cancels and stops are retried with backoff on timeouts, 5xx and rate limits;
  - creating an order is retried only on a rate limit (the request was refused, so nothing was
    placed). On a timeout it raises NetworkError, and the bot looks the order up by its client id
    before it sends anything again: one signal never becomes two orders;
  - the clock is synced with the server on connect and again on retCode 10002;
  - X-Bapi-Limit-Status / X-Bapi-Limit-Reset-Timestamp headers are respected: when the
    remaining quota hits 0 the adapter waits for the reset instead of getting banned.
API keys come from the caller (read from .env); they are never logged or printed.
"""
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

from .base import (AuthError, Balance, DuplicateOrder, Exchange, ExchangeError, InsufficientFunds,
                   Instrument, InvalidOrder, NetworkError, Order, OrderNotFound, Position,
                   RateLimitError)

try:
    import requests
    _NET_ERRORS = (requests.RequestException, OSError)
except ImportError:  # the mock exchange and the tests do not need requests
    requests = None
    _NET_ERRORS = (OSError,)

log = logging.getLogger("cryptobot.bybit")
D = Decimal

URLS = {"testnet": "https://api-testnet.bybit.com", "demo": "https://api-demo.bybit.com"}
CATEGORY = "linear"
SETTLE = "USDT"

# retCodes from https://bybit-exchange.github.io/docs/v5/error (checked 17.10.2026)
OK_CODES = {110043: "leverage not modified", 34040: "TP/SL not modified"}
RETRY_CODES = {10000: NetworkError, 10016: NetworkError, 10006: RateLimitError, 10018: RateLimitError}
AUTH_CODES = {10003, 10004, 10005, 10007, 10009, 10010, 33004}
FUNDS_CODES = {110004, 110007, 110012, 110044, 110045}
ORDER_STATUS = {"New": "open", "PartiallyFilled": "open", "Untriggered": "open", "Created": "open",
                "Filled": "filled", "Cancelled": "cancelled", "PartiallyFilledCanceled": "cancelled",
                "Deactivated": "cancelled", "Rejected": "rejected"}


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "" or v == "0" or v == 0:
        return None
    return D(str(v))


def _num(v) -> str:
    """Decimal -> plain string without exponent ("0.001", not "1E-3")."""
    return format(D(v).normalize(), "f")


class BybitV5(Exchange):
    name = "bybit"

    def __init__(self, api_key: str, api_secret: str, env: str = "testnet", session=None,
                 recv_window: int = 5000, max_retries: int = 4, timeout: float = 10.0,
                 min_interval: float = 0.05, sleep=time.sleep, clock=time.time):
        if env not in URLS:
            raise ValueError(f"env must be one of {sorted(URLS)}; mainnet is not enabled in this build")
        if session is None:
            if requests is None:
                raise RuntimeError("pip install requests")
            session = requests.Session()
        self.base = URLS[env]
        self.env = env
        self._key = api_key
        self._secret = api_secret.encode()
        self.session = session
        self.recv_window = recv_window
        self.max_retries = max_retries
        self.timeout = timeout
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self.offset_ms = 0          # server time - local time
        self._last_call = 0.0
        self._instruments = {}

    def __repr__(self):
        return f"BybitV5(env={self.env!r}, key=***{self._key[-4:] if self._key else ''})"

    # ---- transport ----------------------------------------------------------------------------
    def sign(self, ts: str, payload: str) -> str:
        """HMAC-SHA256 of timestamp + api_key + recv_window + (query string | JSON body), hex."""
        msg = f"{ts}{self._key}{self.recv_window}{payload}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: Optional[dict] = None, auth: bool = True,
                 retry_network: bool = True):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            try:
                return self._send(method, path, params, auth)
            except ExchangeError as e:
                if getattr(e, "code", None) == 10002 and attempt == 0:
                    self.sync_time()  # local clock drifted out of recv_window
                elif not e.retryable or (isinstance(e, NetworkError) and not retry_network):
                    raise
                attempt += 1
                if attempt > self.max_retries:
                    raise
                wait = min(0.5 * 2 ** (attempt - 1), 8.0)
                log.warning("%s %s failed (%s), retry %d/%d in %.1fs", method, path, e, attempt,
                            self.max_retries, wait)
                self.sleep(wait)

    def _send(self, method, path, params, auth):
        gap = self.clock() - self._last_call
        if gap < self.min_interval:
            self.sleep(self.min_interval - gap)
        self._last_call = self.clock()
        url = self.base + path
        headers = {}
        if method == "GET":
            payload = urlencode(params)
            if payload:
                url += "?" + payload
            body = None
        else:
            payload = json.dumps(params, separators=(",", ":"))
            body = payload
            headers["Content-Type"] = "application/json"
        if auth:
            ts = str(int(self.clock() * 1000) + self.offset_ms)
            headers.update({"X-BAPI-API-KEY": self._key, "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": str(self.recv_window),
                            "X-BAPI-SIGN": self.sign(ts, payload)})
        try:
            resp = self.session.request(method, url, data=body, headers=headers, timeout=self.timeout)
        except _NET_ERRORS as e:
            raise NetworkError(f"{type(e).__name__}: {e}") from None
        reason = getattr(resp, "reason", "") or ""
        if resp.status_code == 401:
            # a bad key comes back as HTTP 401 with an empty body, not as retCode 10003 (seen on testnet)
            raise AuthError(f"HTTP 401 {reason}: check the API key and secret, and that they are {self.env} keys")
        if resp.status_code in (403, 429):
            raise RateLimitError(f"HTTP {resp.status_code} {reason}: IP rate limit or access refused")
        if resp.status_code >= 500:
            raise NetworkError(f"HTTP {resp.status_code} {reason}")
        try:
            data = resp.json()
        except ValueError:
            if 400 <= resp.status_code < 500:
                raise InvalidOrder(f"HTTP {resp.status_code} {reason}") from None
            raise NetworkError(f"HTTP {resp.status_code}: response is not JSON") from None
        self._respect_limit(resp.headers)
        code = int(data.get("retCode", -1))
        if code == 0 or code in OK_CODES:
            return data.get("result") or {}
        raise self._error(code, str(data.get("retMsg", "")))

    def _respect_limit(self, headers):
        left = headers.get("X-Bapi-Limit-Status")
        reset = headers.get("X-Bapi-Limit-Reset-Timestamp")
        if left is not None and reset and int(left) <= 0:
            wait = (int(reset) - (self.clock() * 1000 + self.offset_ms)) / 1000
            if 0 < wait <= 10:
                log.warning("rate limit quota used up, waiting %.2fs", wait)
                self.sleep(wait)

    @staticmethod
    def _error(code: int, msg: str) -> ExchangeError:
        text = f"retCode {code}: {msg}"
        if code in RETRY_CODES:
            e = RETRY_CODES[code](text)
        elif code in AUTH_CODES:
            e = AuthError(text)
        elif code in FUNDS_CODES:
            e = InsufficientFunds(text)
        elif code == 110072:
            e = DuplicateOrder(text)
        elif code == 110001:
            e = OrderNotFound(text)
        elif code == 10002:
            e = NetworkError(text)
            e.retryable = False  # handled once in _request by a clock sync
        else:
            e = InvalidOrder(text)
        e.code = code
        return e

    # ---- interface ----------------------------------------------------------------------------
    def sync_time(self):
        r = self._request("GET", "/v5/market/time", auth=False)
        server_ms = int(r["timeNano"]) // 1_000_000 if r.get("timeNano") else int(r["timeSecond"]) * 1000
        self.offset_ms = server_ms - int(self.clock() * 1000)
        return self.offset_ms

    def connect(self):
        self.sync_time()
        self.balance()  # proves the key, the signature and the read permission

    def instrument(self, symbol):
        if symbol not in self._instruments:
            r = self._request("GET", "/v5/market/instruments-info",
                              {"category": CATEGORY, "symbol": symbol}, auth=False)
            items = r.get("list") or []
            if not items or items[0].get("status") != "Trading":
                raise InvalidOrder(f"{symbol} is not a trading linear contract on Bybit {self.env}")
            i = items[0]
            lot, pf, lf = i["lotSizeFilter"], i["priceFilter"], i["leverageFilter"]
            self._instruments[symbol] = Instrument(
                symbol=symbol, qty_step=D(lot["qtyStep"]), min_qty=D(lot["minOrderQty"]),
                max_qty=D(lot["maxOrderQty"]), max_market_qty=D(lot.get("maxMktOrderQty") or lot["maxOrderQty"]),
                tick_size=D(pf["tickSize"]), min_notional=D(lot.get("minNotionalValue") or "0"),
                max_leverage=D(lf["maxLeverage"]))
        return self._instruments[symbol]

    def last_price(self, symbol):
        r = self._request("GET", "/v5/market/tickers", {"category": CATEGORY, "symbol": symbol}, auth=False)
        return D(r["list"][0]["lastPrice"])

    def balance(self):
        r = self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": SETTLE})
        acct = (r.get("list") or [{}])[0]
        coin = next((c for c in acct.get("coin", []) if c.get("coin") == SETTLE), {})
        equity = D(coin.get("equity") or acct.get("totalEquity") or "0")
        available = D(acct.get("totalAvailableBalance") or "0")
        return Balance(equity=equity, available=available)

    def positions(self, symbol=None):
        params = {"category": CATEGORY, "symbol": symbol} if symbol else {"category": CATEGORY, "settleCoin": SETTLE}
        out = []
        for p in self._paged("/v5/position/list", params):
            size = D(p.get("size") or "0")
            if size == 0 or p.get("side") not in ("Buy", "Sell"):
                continue
            out.append(Position(symbol=p["symbol"], side="long" if p["side"] == "Buy" else "short", qty=size,
                                entry_price=D(p.get("avgPrice") or "0"), leverage=D(p.get("leverage") or "1"),
                                unrealized_pnl=D(p.get("unrealisedPnl") or "0"),
                                sl=_dec(p.get("stopLoss")), tp=_dec(p.get("takeProfit"))))
        return out

    def open_orders(self, symbol=None):
        params = {"category": CATEGORY, "symbol": symbol} if symbol else {"category": CATEGORY, "settleCoin": SETTLE}
        params["openOnly"] = 0
        return [self._order(o) for o in self._paged("/v5/order/realtime", params)]

    def get_order(self, symbol, client_id):
        # recent orders first; after a Bybit restart closed orders are only in the history
        for path in ("/v5/order/realtime", "/v5/order/history"):
            r = self._request("GET", path, {"category": CATEGORY, "symbol": symbol, "orderLinkId": client_id})
            items = r.get("list") or []
            if items:
                return self._order(items[0])
        return None

    def set_leverage(self, symbol, leverage):
        lev = _num(leverage)
        self._request("POST", "/v5/position/set-leverage",
                      {"category": CATEGORY, "symbol": symbol, "buyLeverage": lev, "sellLeverage": lev})

    def place_order(self, symbol, side, type, qty, client_id, price=None, sl=None, tp=None, reduce_only=False):
        params = {"category": CATEGORY, "symbol": symbol, "side": "Buy" if side == "buy" else "Sell",
                  "orderType": "Market" if type == "market" else "Limit", "qty": _num(qty),
                  "positionIdx": 0, "orderLinkId": client_id}
        if type == "limit":
            params["price"] = _num(price)
            params["timeInForce"] = "GTC"
        if reduce_only:
            params["reduceOnly"] = True
        if sl is not None or tp is not None:
            params["tpslMode"] = "Full"
            if sl is not None:
                params["stopLoss"] = _num(sl)
                params["slTriggerBy"] = "MarkPrice"
            if tp is not None:
                params["takeProfit"] = _num(tp)
                params["tpTriggerBy"] = "LastPrice"
        r = self._request("POST", "/v5/order/create", params, retry_network=False)
        # Bybit accepts orders asynchronously: return "open" and let the caller poll get_order()
        return Order(order_id=r.get("orderId", ""), client_id=r.get("orderLinkId", client_id), symbol=symbol,
                     side=side, type=type, qty=D(qty), status="open", price=price, reduce_only=reduce_only,
                     sl=sl, tp=tp, created_ms=int(self.clock() * 1000))

    def cancel_order(self, symbol, client_id):
        self._request("POST", "/v5/order/cancel", {"category": CATEGORY, "symbol": symbol, "orderLinkId": client_id})

    def set_position_stops(self, symbol, sl, tp):
        params = {"category": CATEGORY, "symbol": symbol, "positionIdx": 0, "tpslMode": "Full",
                  "stopLoss": _num(sl) if sl is not None else "0", "takeProfit": _num(tp) if tp is not None else "0"}
        if sl is not None:
            params["slTriggerBy"] = "MarkPrice"
        self._request("POST", "/v5/position/trading-stop", params)

    def closed_pnl(self, symbol, since_ms):
        total = D(0)
        for item in self._paged("/v5/position/closed-pnl",
                                {"category": CATEGORY, "symbol": symbol, "startTime": since_ms, "limit": 100}):
            total += D(item.get("closedPnl") or "0")
        return total

    # ---- helpers ------------------------------------------------------------------------------
    def _paged(self, path, params, max_pages=20):
        cursor = None
        for _ in range(max_pages):
            r = self._request("GET", path, {**params, "cursor": cursor})
            yield from r.get("list") or []
            cursor = r.get("nextPageCursor")
            if not cursor:
                return

    @staticmethod
    def _order(o) -> Order:
        return Order(order_id=o.get("orderId", ""), client_id=o.get("orderLinkId", ""), symbol=o.get("symbol", ""),
                     side="buy" if o.get("side") == "Buy" else "sell",
                     type="market" if o.get("orderType") == "Market" else "limit",
                     qty=D(o.get("qty") or "0"), status=ORDER_STATUS.get(o.get("orderStatus"), "open"),
                     price=_dec(o.get("price")), filled_qty=D(o.get("cumExecQty") or "0"),
                     avg_price=_dec(o.get("avgPrice")), reduce_only=bool(o.get("reduceOnly")),
                     sl=_dec(o.get("stopLoss")), tp=_dec(o.get("takeProfit")),
                     created_ms=int(o.get("createdTime") or 0))
