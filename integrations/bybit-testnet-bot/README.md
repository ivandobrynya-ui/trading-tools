# Crypto exchange bot: TradingView to Bybit testnet (Python)

A Python bot that takes TradingView alerts (webhook JSON) or CLI commands and trades USDT perpetuals on
**Bybit testnet** through the official V5 REST API. The exchange sits behind a small adapter, so Binance,
OKX or Phemex is one more class. Without API keys everything runs against a built-in mock exchange.

I built it to a typical spec for TradingView-to-exchange bots. It is a tool, not a strategy: it executes
signals with risk controls and makes no claim about profit. This build trades test money only: there is
no mainnet address in the code.

## What it does
- **Signals:** TradingView alert JSON on `POST /webhook` (shared secret, constant-time check), or the CLI.
  `BYBIT:BTCUSDT.P` is mapped to `BTCUSDT`.
- **Orders:** market or limit entry, long and short, SL/TP attached to the entry (they protect the whole
  position once it fills), leverage per signal with a ceiling, reverse on an opposite signal.
  Close, close all and cancel pending orders.
- **Sizing by risk %:** `qty = risk / (|entry - stop| + fees)`.
  - Rounded down to the exchange qty step; it never rounds up to the minimum.
  - Capped by free margin, by `MAX_NOTIONAL` and by the exchange max order size.
  - Checked against min qty and min order value.
- **Duplicate protection:** every signal id is claimed in SQLite before anything is sent, so it survives
  restarts. Every order carries a client order id derived from the signal id: after a timeout the bot looks
  the order up instead of sending a second one.
- **Daily loss limit:** % of equity at 00:00 UTC, unrealized P/L included. When it is hit, new entries
  stop until the next UTC day, and open positions are optionally closed. The anchor is stored on disk, so a
  restart does not reset the day.
- **Kill switch:** `POST /kill` or `python -m cryptobot kill`. It cancels the bot's orders, closes all
  positions and blocks entries until someone resets it by hand. Closing is always allowed.
- **API errors and rate limits:**
  - Bybit retCodes and HTTP statuses map to error classes (auth, funds, invalid order, duplicate, network,
    rate limit);
  - backoff retries for safe calls; the `X-Bapi-Limit-Status` header is respected;
  - the clock is re-synced on retCode 10002.
- **Reconnect and resync:** on start, and every `RESYNC_SECONDS` while serving, the bot rebuilds its state
  from the exchange's open orders and positions:
  - an order that was in flight during a crash is found by client id;
  - limit fills and SL/TP exits that happened while the bot was down are journaled with realized P/L;
  - a position that lost its stop gets it back;
  - unknown orders and positions are reported and left alone.
- **No position without a stop:** if the exchange fills the entry but the SL is missing, the bot sets it
  again. If that fails, it closes the position.
- **Journal:** SQLite table with one row per signal (accepted, rejected with the reason, duplicate, error)
  and its outcome (placed, filled, closed with P/L, cancelled). CSV export is available.

## Layout
```
cryptobot/
  signals.py        signal format and validation (webhook JSON and CLI)
  sizing.py         risk-based position size, exchange filters, Decimal math
  engine.py         the bot: checks, order flow, daily limit, kill switch, resync
  store.py          SQLite: seen ids, journal, flags
  server.py         webhook server (standard library)
  config.py         settings from .env
  exchange/base.py  adapter interface + error classes
  exchange/bybit.py Bybit V5 REST (testnet / demo only)
  exchange/mock.py  in-memory exchange for tests and key-less runs
  __main__.py       CLI
tests/              73 unit tests, mocked exchange, no network
demo.py             scripted walk-through on the mock exchange
```

## Setup
1. Python 3.10 or newer. Only one package is needed, and only for Bybit: `pip install requests`.
2. Copy `.env.example` to `.env` in this folder. `.env` is git-ignored; keys never go into code, logs
   or the journal.
3. Bybit testnet keys (the account owner does this, not the bot):
   - sign up at https://testnet.bybit.com and claim test USDT (Assets → Request test coins);
   - API Management → Create New Key → System-generated;
   - permissions: Contract (Orders, Positions) read-write; **no withdrawal**;
   - put the key and secret into `.env` as `BYBIT_API_KEY` / `BYBIT_API_SECRET`.

   `BYBIT_ENV=demo` uses Bybit Demo Trading instead. Mainnet is not supported by this build.
4. Check the connection:
   ```
   python -m cryptobot check BTCUSDT ETHUSDT
   ```
   Without keys it tests only the public endpoints and prints the instrument filters.

## Use
```
python demo.py                                     # every feature on the mock exchange, 1 second
python -m cryptobot signal --action buy --symbol BTCUSDT --sl-pct 1 --tp-r 2 --risk 0.5 --leverage 5
python -m cryptobot signal --action sell --symbol ETHUSDT --type limit --price 2600 --sl 2650
python -m cryptobot signal --action close --symbol BTCUSDT
python -m cryptobot status                         # equity, loss today, kill switch, positions, orders
python -m cryptobot resync                         # after a restart (serve and signal do it themselves)
python -m cryptobot kill --reason "news"           # emergency stop;  unkill to allow entries again
python -m cryptobot journal --limit 20             # or --csv journal.csv
python -m cryptobot price BTCUSDT 59000            # mock only: move the market to test SL/TP and limits
python -m cryptobot serve --port 8090              # webhook server
```
Runtime files (`bot.db`, `bot.log`, mock state) go to `data/`, which is git-ignored.

### TradingView alert
TradingView webhooks need a paid TradingView plan and a public HTTPS URL. Put a tunnel (for example
Cloudflare Tunnel) or a reverse proxy on a VPS in front of `serve`, and use `--trust-proxy` and
`--allow-ip` for TradingView's IPs. Alert message:
```json
{"secret": "<WEBHOOK_SECRET>", "id": "{{ticker}}-{{interval}}-{{time}}-buy", "action": "buy",
 "symbol": "{{ticker}}", "sl_pct": 1.5, "tp_r": 2, "risk": 0.5, "leverage": 5, "comment": "my strategy"}
```
Fields: `action` (buy, sell, close, close_all, cancel), `symbol`, `id` or `time`, `order_type`
(market/limit) + `price`, `sl` or `sl_pct`, `tp`/`tp_pct`/`tp_r`, `risk` or `qty`, `leverage`, `comment`.
A signal without a stop is refused unless `DEFAULT_SL_PCT` is set.

## Tests
```
python -m unittest discover -s tests -v
```
73 tests, about 4 seconds, no network:

| File | What it proves |
|---|---|
| `test_sizing.py` | formula with fees, rounding down, min qty/value, margin/notional/max caps, 500 random cases; on the mock, the realized loss at the stop equals the planned risk to the cent |
| `test_duplicates.py` | same id twice, after a restart, after a rejection; lost answer after the exchange accepted; lost request; lost answer plus failed lookup; exchange down |
| `test_daily_limit.py` | realized and floating loss, flatten on/off, restart mid-day keeps the anchor, next UTC day resets, closing allowed; kill switch flattens, survives restarts, keeps going after an error, leaves foreign orders alone |
| `test_resync.py` | crash before/after the order reached the exchange, crash during checks, offline fills/cancels/stop-outs, lost SL restored, unknown orders/positions reported, idempotent, reconnect with backoff |
| `test_bybit.py` | V5 signature, retCode mapping, retries and backoff, no resend of a timed-out order, quota header, clock drift, HTTP 401, parsing of real-shaped responses, no mainnet, no key in repr |
| `test_signals.py`, `test_server.py` | validation, wrong-side stops, default SL/TP, leverage caps, real HTTP round trip (403/400/413/404) |

## Adding an exchange
Implement `exchange/base.py: Exchange` with these methods:
- `instrument`, `last_price`, `balance`;
- `positions`, `open_orders`, `get_order(client_id)`;
- `set_leverage`, `place_order`, `cancel_order`;
- `set_position_stops`, `closed_pnl`.

Map the exchange's errors to the classes in `base.py`. Notes:
- **Binance USDⓈ-M futures:** `newClientOrderId`, SL/TP as separate `STOP_MARKET`/`TAKE_PROFIT_MARKET`
  orders with `closePosition=true`; testnet at testnet.binancefuture.com.
- **OKX:** `clOrdId` and `attachAlgoOrds`; demo trading via the `x-simulated-trading: 1` header.

The engine, the sizing and all safety checks stay as they are.

## Limits of this build
- USDT linear perpetuals, one-way mode, one position per symbol. Use a dedicated (sub)account: the
  kill switch and the daily limit close every position on it.
- The daily anchor is the first equity the bot sees after 00:00 UTC. Deposits and withdrawals during the
  day count as P/L.
- Bybit confirms orders asynchronously; the bot polls REST for the fill (no WebSocket stream). Closed P/L
  comes from `/v5/position/closed-pnl` and can lag a few seconds behind the exit.
- A partly filled limit order stays "placed" in the journal until it fills or is cancelled.

## How I verified it (17 Oct 2026)
- **73 of 73 unit tests pass** (table above). The mock exchange behaves like the real one (qty step,
  min order, fees, stops, limit orders) and can also fail on purpose: timeout, rate limit, lost answer.
- **Sizing to the cent.** On the mock, the realized loss at the stop equals the planned risk, fees included.
- Live against api-testnet.bybit.com, public endpoints, no keys:
  - server time sync;
  - instrument filters for BTCUSDT/ETHUSDT/SOLUSDT, the same values the mock uses;
  - last prices.
- Live signed requests with a made-up key: Bybit answers HTTP 401 with an empty body, not retCode 10003
  as the docs suggest. The first version treated that as a network error and retried it 4 times. Fixed:
  it is now an `AuthError`, not retried, with a test.

## Not verified yet
Live orders on testnet. This needs testnet API keys in `.env`; then run `check`, one market and one limit
signal, `kill`, `resync`.
