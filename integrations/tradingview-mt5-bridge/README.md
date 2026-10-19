# TradingView to MetaTrader 5 bridge (Python + MQL5)

Pine Script alerts arrive as webhooks and open or close trades in MT5, with risk-based lots, SL/TP, a daily loss limit, a max drawdown halt, an emergency stop and duplicate protection at four levels.

## How it works
```
TradingView alert ──HTTPS──> tvb_receiver.py ──.sig file──> TV_Bridge_EA (MT5) ──> order at the broker
   (JSON message)            secret, format,                Common\Files\tvb_inbox   lot from risk, SL/TP,
                             duplicates                                              limits, journal
```

### `receiver/tvb_receiver.py`: webhook receiver
- Python standard library only, nothing to install.
- Checks the shared secret (constant-time compare) and the signal format, and drops duplicates by id.
- Writes each signal as its own file **atomically** (temp file, then rename), so the EA never reads half a file.
- Keeps the list of seen ids on disk, so duplicates are caught after a restart too.
- `POST /kill`: remote emergency stop, for example from a phone. `GET /health`: is the receiver alive.
- The secret lives only in the `TVB_SECRET` environment variable and never goes to the logs. Optional IP allow list.

### `mt5/TV_Bridge_EA.mq5`: executor EA
- **Lot:** risk % of equity through `OrderCalcProfit`, or fixed lots. A cap on risk per signal and a cap on lots. If the risk-based lot is below the minimum lot, the signal is refused instead of entering with more risk.
- **SL/TP** as prices or in points; SL by default; TP as an R multiple. Side and the broker's minimum distance are checked.
- **Checks first, actions second.** An invalid signal (no stop, stop on the wrong side, no margin) touches nothing. Only a valid signal closes an opposite position.
- **Duplicates, 4 levels:**
  1. id in the receiver;
  2. id in the EA (a file on disk);
  3. a processed file moves to `tvb_done`;
  4. a search for the `TVB <hash>` tag in positions and history, in case of a crash between sending an order and moving the file. The tag is checked before any retry, so a server timeout can't double a trade.
- **Daily loss limit:** % of equity at the start of the day, floating P/L included. When hit, positions close and entries are blocked until the next day.
- **Max drawdown from the equity peak:** halt until a manual reset (`InpResetHalt`).
- **Emergency stop:** an EMERGENCY STOP button on the chart, a flag file from `/kill`, or a signal with `action=kill`. Closes positions and blocks entries until a manual reset.
- **Also:** spread filter, position cap per symbol and direction, symbol map (`XAUUSD=GOLD`), broker suffix, symbol allow list, stale signals dropped (older than 60 s by default).
- **State** (day start, peak, halt) is kept in terminal global variables and survives a restart.
- **Journal** `tvb_journal_<magic>.csv`: every signal with its outcome and reason. A status panel on the chart.

## EA inputs
| Group | Input | Default |
|---|---|---|
| Signals | Inbox folder in Common\Files | tvb_inbox |
| | Poll interval (ms) | 500 |
| | Ignore signals older than N seconds | 60 |
| | Symbol map / broker suffix / allowed symbols | empty |
| | Replay mode for the Strategy Tester | off |
| Position size | Size mode | risk % |
| | Default risk / max risk a signal may request (%) | 0.5 / 2.0 |
| | Fixed lots / max lots per order | 0.01 / 1.0 |
| | SL in points if the signal has none (0 = reject) | 0 |
| | TP as R multiple if the signal has none (0 = no TP) | 0 |
| Execution | Max spread (points, 0 = off) / max slippage | 0 / 30 |
| | Max positions per symbol and direction | 1 |
| | Close opposite positions before a new entry | on |
| | Magic number | 777001 |
| Protection | Daily loss limit (% of day-start equity) | 3.0 |
| | Close all bridge positions on the daily limit | on |
| | Max drawdown from equity peak (%) | 10.0 |
| | Kill switch file in Common\Files | tvb_KILL.flag |
| | Clear a halt on start | off |

## Install and run
1. **Secret** (16+ characters) in an environment variable, never in code: `setx TVB_SECRET "<long random string>"`, then open a new terminal window.
2. **Receiver:** `python receiver/tvb_receiver.py --port 8080`. It writes to the MT5 common folder `%APPDATA%\MetaQuotes\Terminal\Common\Files`.
3. **Public HTTPS address.** TradingView sends webhooks only to ports 80 and 443.
   - For a test: Cloudflare Tunnel, `cloudflared tunnel --url http://localhost:8080`, and start the receiver with `--trust-proxy`.
   - For real use: a Windows VPS with MT5 and the receiver, a domain and HTTPS (Caddy or Cloudflare).
4. **MT5:** copy `mt5/TV_Bridge_EA.mq5` to `MQL5\Experts`, compile it (F7), attach it to any chart and turn on Algo Trading. Set the risk, the limits and the broker's symbol map.
5. **TradingView:** alert, Webhook URL `https://<address>/webhook`, and JSON in the Message field.

   From a strategy (`strategy.entry`):
   ```json
   {"secret":"YOUR_SECRET","id":"{{ticker}}-{{interval}}-{{time}}-{{strategy.order.action}}","symbol":"{{ticker}}","action":"{{strategy.order.action}}","sl_points":500,"risk":0.5}
   ```
   From an indicator with a computed stop (Pine `alert()`):
   ```pine
   if longSignal
       alert('{"secret":"' + secretInput + '","id":"' + syminfo.ticker + '-' + timeframe.period + '-' + str.tostring(time) + '-buy","symbol":"' + syminfo.ticker + '","action":"buy","sl":' + str.tostring(stopPrice, format.mintick) + ',"tp":' + str.tostring(targetPrice, format.mintick) + ',"risk":0.5}', alert.freq_once_per_bar_close)
   ```
   - Close: `"action":"close"`; close everything: `"action":"close_all"`.
   - An id with the bar time `{{time}}` means "one signal per bar": a repeated alert on the same bar is a duplicate.
6. **Remote emergency stop:** `curl -X POST https://<address>/kill -d "{\"secret\":\"YOUR_SECRET\"}"`

**Running costs:** a paid TradingView plan (webhooks are not on the free plan) with 2FA on; a Windows VPS for MT5 and the receiver, usually $10 to $30 a month; Cloudflare Tunnel is free.

## How I verified it (17 Oct 2026)
- **Receiver:** 10 unit tests (`cd receiver && python -m unittest test_tvb_receiver.py`), all pass.
  - Format and secret; numbers (NaN, negative, risk above 5%); id from the bar time.
  - Duplicates, including after a restart; atomic write; `/kill`.
  - Full HTTP round trip: 403 without the secret, 400 for non-JSON, 413 for a too large body, 404.
- **EA:** MetaEditor, 0 errors, 0 warnings.
- **Scenario in the MT5 Strategy Tester** (replay mode, XAUUSD M1, 14 to 17 Sep 2026, $10,000, risk 1%, daily limit 1.5%). Full journal: [tests/replay_journal_main.csv](tests/replay_journal_main.csv). The scenario files come from `tools/make_replay.py`.

| Signal | What it tests | Result |
|---|---|---|
| s1 | entry, risk 1% | 0.20 lots: $5 stop x 100 oz x 0.2 = $100 = 1% |
| s1 again | duplicate | dropped |
| s3 | second long | refused: position cap |
| s6 / s7 | no stop / stop on the wrong side | refused, **the open long untouched** |
| s4 | reversal | closed the long, opened a 0.19 short |
| s5 / s9 | close / close all | 1 position closed each |
| s8 | asks for 5% risk | cut to 2%, 0.39 lots |
| d1 to d3 | tight stop | lot capped at 1.0 |
| d4 to d7 | after -1.8% on the day | refused: daily limit |
| k1 | next day | entries allowed again |
| k2 | emergency stop | 1 position closed, halt |
| k3, k4 | entries after the halt | refused, the next day too |

- **A separate run with a 1.5% max drawdown:** after the first loss the EA halted with "max drawdown", and every later entry was refused, on other days too.
- **The live inbox path** (tester without replay, journal: [tests/inbox_journal.csv](tests/inbox_journal.csv), files from `tools/inbox_test.py`):
  - a fresh signal executed;
  - a stale one refused ("stale");
  - a duplicate in the same batch dropped;
  - after a "restart" the same signal was recognized as processed;
  - files sorted into `tvb_done` and `tvb_rejected`.

## Limits
- Between the alert and the order there are a few seconds: TradingView delivery, the network, the EA polls the folder every 500 ms. Not for scalping on seconds.
- **Stop in points** is measured from the fill price in MT5. **Stop as a price** is used as is, and the broker's price can differ from the TradingView feed. With different feeds, points are safer.
- **Signal age** is measured by the computers' clocks: run the receiver and MT5 on one machine or keep the time in sync.
- The bridge executes signals, it does not invent them. Profit depends on the strategy, not on the bridge.

## Not verified yet
The live chain "TradingView, internet, receiver, MT5" on an open market. It needs a paid TradingView plan (webhooks) and a weekday.
