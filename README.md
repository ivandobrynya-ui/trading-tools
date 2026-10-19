# Trading tools: TradingView, MetaTrader 5, Python

I'm Ivan Semernya, a trading tools developer. I build TradingView indicators and strategies in Pine Script v6, MetaTrader 5 Expert Advisors and indicators in MQL5, and Python integrations that connect them to brokers and exchanges. I trade XAU/USD myself, mostly with Smart Money Concepts, so many examples here run on gold.

This repo holds 12 tools I built and checked in October 2026: about 9,200 lines of code in 50 files. Four are my own indicators: three for TradingView and the MT5 port of one of them. The other eight follow specs that are typical for freelance trading jobs. Each tool has a README: what it does, the inputs, how to install it and how I checked it, with the real numbers. Where a backtest lost money, the README says so.

## Tools

| # | Tool | Platform | What it does | How it was checked |
|---|---|---|---|---|
| 1 | [SMC Market Structure](tradingview/smc-market-structure/) | Pine v6 | Swing and internal BOS / CHoCH, HH / HL / LH / LL, strong and weak highs and lows, EQH / EQL, 10 alerts | 0 differences vs an independent replica on 10,613 TradingView bars |
| 2 | [Order Blocks & Fair Value Gaps](tradingview/order-blocks-fvg/) | Pine v6 | Structure-based OBs and FVGs with mitigation rules, ATR filters, 12 alerts | 10,568-bar debug run: 274 OBs, 1,160 FVGs, clean chart |
| 3 | [Liquidity & Sessions](tradingview/liquidity-sessions/) | Pine v6 | Asia / London / NY kill zones, session levels, PDH / PDL, PWH / PWL, sweep vs break, dashboard | Checked on XAUUSD 1h and 15m, drawing bug found and fixed |
| 4 | [Multi-Indicator Value Strategy](tradingview/multi-indicator-value-strategy/) | Pine v6 strategy | BB + RSI + Stochastic + MACD votes, P/E and P/B filter for stocks | Backtest on AAPL and JPM, 3 bugs found and fixed |
| 5 | [External Signal + Fibonacci Targets](tradingview/external-signal-fib-strategy/) | Pine v6 strategy | Backtests another indicator's Buy / Sell arrows with a swing stop and Fibonacci take profits | 0 differences vs the provider on 5,300 bars |
| 6 | [ORB Restart-Safe EA](mt5/orb-restart-safe/) | MQL5 EA | Opening range breakout, OCO, risk-based lots, no duplicates after a restart | 964 days, exactly 1 entry per day |
| 7 | [SMC Market Structure for MT5](mt5/smc-market-structure/) | MQL5 indicator | Port of tool 1 with signal buffers for other EAs | 0 differences in 3,773 signals on 40,050 MT5 bars |
| 8 | [EMA Cross EA](mt5/ema-cross-ea/) | MQL5 EA | EMA 20 / 50 cross on closed candles, one trade per signal, restart-safe | 836 of 836 entries on the right candle |
| 9 | [Volume Profile](mt5/volume-profile/) | MQL5 indicator | Daily POC / VAH / VAL from M1 bars, current and previous session, readable by EAs | 988 bars x 6 values, 0 differences vs a Python rebuild |
| 10 | [Multi-Asset EA](mt5/multi-asset-ea/) | MQL5 EA | One chart, many symbols, lots per symbol, daily loss limit, risk budget, restart-safe | 559 of 559 entries and lots checked; same 1,118 deals with memory wiped |
| 11 | [TradingView to MT5 bridge](integrations/tradingview-mt5-bridge/) | Python + MQL5 | Webhook receiver and executor EA: risk lots, daily limit, drawdown halt, kill switch | 10 unit tests, 20-signal tester scenario |
| 12 | [Bybit testnet bot](integrations/bybit-testnet-bot/) | Python | TradingView webhook or CLI to Bybit V5 API: risk sizing, duplicates, resync, kill switch | 73 unit tests, no network |

Shared helpers: [mt5/tools](mt5/tools/) exports bars and indicator buffers from MT5 to CSV and reads Strategy Tester reports in Python.

## How I verify
A tool that compiles and a tester that shows a profit prove nothing about the code. This is what I do instead.

- **Independent replica.** I write the logic a second time, in Python or JavaScript, and compare it with the tool. The SMC indicator has a JavaScript replica; the EMA, Volume Profile and Multi-Asset tools are recomputed in Python from bars exported from MT5.
- **Bar by bar.** The comparison runs on every bar, not on a few screenshots. The MQL5 port of SMC Market Structure matched the Pine original with 0 differences in 1,032 signals on 10,613 TradingView bars and 0 in 3,773 signals on 40,050 MT5 bars.
- **Tester checks of the mechanics.** For EAs I read the Strategy Tester report deal by deal: one entry per signal, the right candle, the lot from the risk, caps, the daily limit. To test restart safety, the Multi-Asset EA wiped its memory every cycle and produced the same 1,118 deals.
- **Unit tests for Python.** 10 tests for the webhook receiver and 73 for the crypto bot, with failure cases: lost answers, timeouts, crashes between sending an order and recording it.
- **No lookahead.** Signals on closed bars, higher timeframe data with `[1]` and `lookahead_on`, commission and slippage in every strategy test.
- **Honest numbers.** The ORB EA ends with PF 0.84 to 1.10 depending on the session, the Multi-Asset EA with PF 0.87. I keep those numbers. These tools execute rules; they do not promise profit.

## Layout
```
tradingview/<tool>/    Pine Script v6 indicators and strategies
mt5/<tool>/            MQL5 indicators and Expert Advisors, with their verification scripts
mt5/tools/             MT5 export scripts and a tester report reader
integrations/<tool>/   Python services that connect TradingView to MT5 or an exchange
```
No price data is included. The verification scripts work on bars you export from your own terminal.

## License
The code is under the [MIT License](LICENSE). Pine Script files that start with a Mozilla Public License 2.0 header (the TradingView standard for open scripts) stay under MPL-2.0.

Nothing here is financial advice.
