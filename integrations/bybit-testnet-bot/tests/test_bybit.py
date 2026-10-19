"""Bybit V5 adapter with a fake HTTP session: signing, error mapping, retries, rate limits, parsing.
No network and no keys: the fake session plays the exchange's answers."""
import hashlib
import hmac
import json
import unittest
from decimal import Decimal
from urllib.parse import urlparse

from cryptobot.exchange.base import (AuthError, DuplicateOrder, InsufficientFunds, NetworkError,
                                     RateLimitError)
from cryptobot.exchange.bybit import BybitV5

D = Decimal
KEY, SECRET = "testkey123", "testsecret456"

# shape copied from a real api-testnet.bybit.com answer (17.10.2026), shortened
INSTRUMENT = {"list": [{"symbol": "BTCUSDT", "status": "Trading",
                        "leverageFilter": {"minLeverage": "1", "maxLeverage": "150.00", "leverageStep": "0.01"},
                        "priceFilter": {"minPrice": "0.10", "maxPrice": "1999999.80", "tickSize": "0.10"},
                        "lotSizeFilter": {"maxOrderQty": "1190.000", "minOrderQty": "0.001", "qtyStep": "0.001",
                                          "maxMktOrderQty": "500.000", "minNotionalValue": "5"}}]}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def ok(result=None, **headers):
    return Resp(200, {"retCode": 0, "retMsg": "OK", "result": result or {}}, headers)


def err(code, msg="error"):
    return Resp(200, {"retCode": code, "retMsg": msg, "result": {}})


class FakeSession:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.sent.append({"method": method, "url": url, "data": data, "headers": headers or {}})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


class BybitTests(unittest.TestCase):
    def make(self, *answers):
        self.sleeps = []
        self.session = FakeSession(*answers)
        return BybitV5(KEY, SECRET, session=self.session, sleep=self.sleeps.append, clock=lambda: 1792260000.0,
                       min_interval=0)

    def test_signature_get_and_post(self):
        ex = self.make(ok({"list": []}), ok({"orderId": "1", "orderLinkId": "cbE"}))
        ex.positions("BTCUSDT")
        ex.place_order("BTCUSDT", "buy", "market", D("0.075"), "cbE", sl=D("59400.0"), tp=D("61200.0"))
        for sent in self.session.sent:
            h = sent["headers"]
            payload = urlparse(sent["url"]).query if sent["method"] == "GET" else sent["data"]
            expected = hmac.new(SECRET.encode(), f"{h['X-BAPI-TIMESTAMP']}{KEY}5000{payload}".encode(),
                                hashlib.sha256).hexdigest()
            self.assertEqual(h["X-BAPI-SIGN"], expected)
            self.assertEqual(h["X-BAPI-API-KEY"], KEY)
            self.assertEqual(h["X-BAPI-TIMESTAMP"], "1792260000000")
        self.assertTrue(self.session.sent[0]["url"].startswith("https://api-testnet.bybit.com/v5/position/list?"))
        body = json.loads(self.session.sent[1]["data"])
        self.assertEqual(body, {"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Market",
                                "qty": "0.075", "positionIdx": 0, "orderLinkId": "cbE", "tpslMode": "Full",
                                "stopLoss": "59400", "slTriggerBy": "MarkPrice", "takeProfit": "61200",
                                "tpTriggerBy": "LastPrice"})

    def test_rate_limit_is_retried_with_backoff(self):
        ex = self.make(err(10006, "Too many visits"), Resp(429, {}), ok({"list": [{"lastPrice": "60000.5"}]}))
        self.assertEqual(ex.last_price("BTCUSDT"), D("60000.5"))
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_gives_up_after_max_retries(self):
        ex = self.make(*[err(10016, "Server error")] * 5)
        with self.assertRaises(NetworkError):
            ex.balance()
        self.assertEqual(len(self.session.sent), 5)

    def test_auth_error_is_not_retried(self):
        ex = self.make(err(10003, "API key is invalid"))
        with self.assertRaises(AuthError):
            ex.balance()
        self.assertEqual(len(self.session.sent), 1)

    def test_http_401_is_an_auth_error(self):
        """Found live on testnet: a wrong key gets HTTP 401 with an empty body, not retCode 10003."""
        ex = self.make(Resp(401, None))
        with self.assertRaisesRegex(AuthError, "testnet keys"):
            ex.balance()
        self.assertEqual(len(self.session.sent), 1, "not retried")

    def test_order_timeout_is_not_resent_by_the_adapter(self):
        """The adapter cannot know whether the order went through: the bot checks by client id."""
        ex = self.make(TimeoutError("read timed out"))
        with self.assertRaises(NetworkError):
            ex.place_order("BTCUSDT", "buy", "market", D("0.01"), "cbE")
        self.assertEqual(len(self.session.sent), 1)

    def test_rate_limited_order_is_resent(self):
        ex = self.make(err(10006), ok({"orderId": "9", "orderLinkId": "cbE"}))
        self.assertEqual(ex.place_order("BTCUSDT", "buy", "market", D("0.01"), "cbE").order_id, "9")

    def test_error_codes(self):
        cases = [(110072, DuplicateOrder), (110007, InsufficientFunds), (403, RateLimitError)]
        for code, exc in cases:
            ex = self.make(Resp(403, None) if code == 403 else err(code))
            ex.max_retries = 0
            with self.assertRaises(exc, msg=str(code)):
                ex.place_order("BTCUSDT", "buy", "market", D("0.01"), "cbE")

    def test_not_modified_is_success(self):
        ex = self.make(err(110043, "Set leverage has not been modified."), err(34040, "Not modified"))
        ex.set_leverage("BTCUSDT", D(5))
        ex.set_position_stops("BTCUSDT", D(59000), None)
        body = json.loads(self.session.sent[0]["data"])
        self.assertEqual((body["buyLeverage"], body["sellLeverage"]), ("5", "5"))

    def test_clock_drift_resyncs_time(self):
        ex = self.make(err(10002, "time window"), ok({"timeSecond": "1792260003", "timeNano": "1792260003000000000"}),
                       ok({"list": [{"totalAvailableBalance": "900", "coin": [{"coin": "USDT", "equity": "1000.5"}]}]}))
        b = ex.balance()
        self.assertEqual(ex.offset_ms, 3000)
        self.assertEqual((b.equity, b.available), (D("1000.5"), D(900)))
        self.assertEqual(self.session.sent[2]["headers"]["X-BAPI-TIMESTAMP"], "1792260003000")

    def test_waits_when_quota_is_used_up(self):
        ex = self.make(ok({"list": [{"lastPrice": "1"}]}, **{"X-Bapi-Limit-Status": "0",
                                                              "X-Bapi-Limit-Reset-Timestamp": "1792260001500"}))
        ex.last_price("BTCUSDT")
        self.assertEqual(self.sleeps, [1.5])

    def test_network_error_then_success(self):
        ex = self.make(ConnectionResetError("reset by peer"), ok(INSTRUMENT))
        inst = ex.instrument("BTCUSDT")
        self.assertEqual((inst.qty_step, inst.min_qty, inst.tick_size, inst.max_market_qty, inst.max_leverage),
                         (D("0.001"), D("0.001"), D("0.10"), D("500"), D(150)))

    def test_parse_positions_and_orders(self):
        ex = self.make(
            ok({"list": [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.075", "avgPrice": "60000", "leverage": "5",
                          "unrealisedPnl": "-3.2", "stopLoss": "59400", "takeProfit": ""},
                         {"symbol": "ETHUSDT", "side": "", "size": "0", "avgPrice": "0", "leverage": "3"}],
                "nextPageCursor": ""}),
            ok({"list": [{"orderId": "1", "orderLinkId": "cbabcE", "symbol": "ETHUSDT", "side": "Sell",
                          "orderType": "Limit", "qty": "0.94", "price": "2550", "orderStatus": "New",
                          "cumExecQty": "0", "avgPrice": "", "reduceOnly": False, "createdTime": "1792260000000"}]}))
        [p] = ex.positions()
        self.assertEqual((p.side, p.qty, p.sl, p.tp), ("long", D("0.075"), D(59400), None))
        [o] = ex.open_orders()
        self.assertEqual((o.side, o.type, o.status, o.price, o.avg_price), ("sell", "limit", "open", D(2550), None))

    def test_get_order_falls_back_to_history(self):
        ex = self.make(ok({"list": []}), ok({"list": [{"orderLinkId": "cbE", "orderStatus": "Filled", "side": "Buy",
                                                        "orderType": "Market", "qty": "0.01", "avgPrice": "60010"}]}))
        o = ex.get_order("BTCUSDT", "cbE")
        self.assertEqual((o.status, o.avg_price), ("filled", D(60010)))
        self.assertIn("/v5/order/history", self.session.sent[1]["url"])

    def test_no_mainnet_and_no_key_leak(self):
        with self.assertRaises(ValueError):
            BybitV5(KEY, SECRET, env="mainnet", session=FakeSession())
        ex = self.make()
        self.assertNotIn(SECRET, repr(ex))
        self.assertNotIn(KEY, repr(ex))


if __name__ == "__main__":
    unittest.main()
