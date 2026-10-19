# Multi-Asset EA (MetaTrader 5 Expert Advisor, MQL5)

Files:
- `Multi_Asset_EA.mq5`: the EA;
- `run_tester.py`: runs the MT5 Strategy Tester from the command line; writes an ini with every input, collects the report, the EA log and the entries CSV;
- `verify_multi.py`: an independent check of the tester runs (what it checks is below);
- `verify_output.txt`: the output of that check for the runs of 17 Oct 2026;
- `tools/MAEA_Export.mq5` + `tools/export.py`: the server's symbol list and bar export for the check;
- `img/`: balance curves from the tester.

One EA on one chart trades a whole list of symbols: forex, gold, silver, commodities. Risk-based lots per symbol, a daily loss limit, position caps and full restart safety. The entry rule is deliberately simple. The point of this build is the frame around it and the proof that the frame works.

## What it does

### One chart, many symbols
- Symbols come from one input: `EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD`. A broker suffix (`EURUSD.m`) is one more input for all of them.
- A symbol the broker does not have is skipped with a log line; the rest keep working.
- **Timer, not ticks.** Every 5 seconds (input) the EA loops over all symbols. `OnTick` fires only on ticks of the chart symbol: if EURUSD is quiet, gold would wait for someone else's tick.
- Each symbol has its own EMA 50, EMA 200, RSI 14 and ATR 14.
- On start the EA logs a **passport of each symbol**: contract size, tick size, tick value, min lot and step, margin for 1 lot.

### Entry rule (one, transparent)
- **Trend:** close above EMA 200 and EMA 50 above EMA 200, then only buys. Sells mirror it. The trend can come from a higher timeframe (for example H4 with H1 entries).
- **Pullback:** RSI was below 40 and closed back above 40. For sells: was above 60 and closed back below 60.
- Signals on **closed** candles only; entry on the next candle within the first 15 minutes (input, 0 = whole candle).
- **Sessions:** up to two windows in server time (default 08:00 to 20:00) plus days of the week.
- **Spread filter** in % of ATR, not in points, so one setting fits EURUSD and gold alike.
- Every number is an input. The rule can be replaced; the frame stays.

### Stop and target
- **SL:** ATR x 1.5 (default), fixed points, or beyond the high / low of the last N candles.
- **TP:** in R (2R default), in ATR, in points, or none.
- **Break-even** at N x R and an **ATR trailing stop** that starts only once it locks in profit.
- Break-even does not keep the original stop in memory. While the stop is on the losing side it is the original one, and R is computed from it. So break-even also survives a restart.

### Lot size
1. Risk money = balance x risk % (0.5% by default).
2. The loss of 1 lot at the stop is computed **two ways**: from the symbol's tick value and with the terminal's own calculator (`OrderCalcProfit`). The EA takes the **larger** loss, which means the smaller lot.
3. The lot is rounded **down** to the lot step. Below the minimum lot the trade is skipped, not opened with more risk.
4. Margin check: without enough free margin the order is not sent.

### Capital protection
- **Daily loss limit**, % of the balance at the start of the server day (3% by default).
  - Closed P/L of the day plus floating P/L.
  - When hit, the EA closes its positions (input) and makes no new entries until the next day.
  - The halt flag is a terminal global variable, flushed to disk at once. After a restart the EA stays halted until the next day.
  - Closed P/L comes from the server's deal history, not from memory, so a restart mid-day still counts from the right number.
  - Counts only this EA's trades (by magic) or the whole account.
- **Daily risk budget** (on by default): a new trade is not opened if its stop plus the stops of all open positions could take the day past the limit. The limit alone only stops trading; open stops can still fire almost at once and push the day further. The stress test below shows it.
- **Max positions** overall (4) and per symbol (1). On a netting account it is always 1 per symbol.

### Restart and duplicate protection
- The EA **keeps nothing important in memory**. Every cycle it reads from the server: its open positions and orders in flight, its deals since the start of the day, the closed and floating P/L of the day.
- "Did I already enter on this signal?" The EA looks for its own entry deal on this symbol since the current candle opened. If there is one, there is no second entry, even if the position was already stopped out inside the same candle.
- On start it logs a recovery report: positions per symbol, entries on the current candle, P/L of the day, limit status.
- The only thing in memory is a cache "this candle is already processed", for speed. Run B below proves it does not affect decisions.

## Inputs (48 in total, main ones)
| Group | Input | Default |
|---|---|---|
| Symbols and timer | Symbols, broker suffix, signal timeframe, timer | 5 symbols, none, H1, 5 s |
| Trend filter | Use trend, trend timeframe, fast / slow EMA | on, signal TF, 50 / 200 |
| Pullback entry | RSI period, buy level, sell level | 14, 40, 60 |
| | Direction, entry window (min), close on opposite signal | both, 15, off |
| Sessions | Session 1, session 2, Monday ... Friday | 08:00-20:00, off, all on |
| Stop and target | ATR period, SL mode, SL ATR multiple | 14, ATR, 1.5 |
| | TP mode, TP R multiple | R, 2.0 |
| | Break-even (trigger / lock-in, R), ATR trailing (multiple) | off (1.0 / 0.1), off (2.0) |
| Risk | Risk per trade, max spread (% of ATR) | 0.5%, 10 |
| | Max positions overall / per symbol | 4 / 1 |
| | Daily loss limit, counts (EA or account), close on limit, risk budget | 3%, EA, on, on |
| General | Magic number, slippage, log to file | 261017, 20, on |
| Testing | Forget all memory every cycle (simulates a restart each cycle) | off |

## Install
1. Copy `Multi_Asset_EA.mq5` to `MQL5\Experts\Practice\` in your MT5 data folder and compile it in MetaEditor (F7). (`run_tester.py` expects `Experts\Practice\Multi_Asset_EA.ex5`.)
2. Attach it to one chart (EURUSD H1 in my tests), set the symbol list for your broker and turn on Algo Trading.

To repeat the tests (Windows, MT5 closed, Python 3), also compile `tools/MAEA_Export.mq5` into `MQL5\Scripts\Practice\`:
```
python run_tester.py A_main 2025.01.01 2026.10.16 deposit=100000 InpSymbols=EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,NICKEL
python tools/export.py EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,NICKEL
python verify_multi.py
```
Runs B, C and D change the inputs listed in the table below. `run_tester.py` and `tools/export.py` assume the default MT5 install in `C:\Program Files\MetaTrader 5`; edit `TERMINAL` and `DATA` at the top for another install. Tester results and exported bars go to `results/`, which is not in the repo (broker data).

## How I verified it (17 Oct 2026)

### Setup
- **Compile:** MetaEditor (MT5 build 6272), 0 errors, 0 warnings.
- **Strategy Tester:** MetaQuotes-Demo, 1 Jan 2025 to 16 Oct 2026, H1, "1 minute OHLC", 100% history quality, $100,000 deposit, leverage 1:100.
- **One EURUSD H1 chart, 6 symbols traded:** EURUSD, GBPUSD, USDJPY, XAUUSD, XAGUSD, NICKEL.
- **Why $100,000.** Lots round down to 0.01. At $10,000 with 0.5% risk, gold hits the minimum lot and the risk check loses precision.

| Run | What it tests | Settings |
|---|---|---|
| A | main | defaults: risk 0.5%, daily limit 3%, risk budget on, up to 4 positions, 1 per symbol, entry in the first 15 minutes |
| B | restart | as A, but the EA **wipes all its memory every cycle**, as if restarted every ~20 seconds |
| C | daily limit stress | risk 1%, limit 1.5%, risk budget **off**, up to 6 positions, 2 per symbol, entry during the whole candle, break-even and trailing on, no memory |
| D | risk budget | as C, but the risk budget **on** |

### Run A results (the rule as it is, no optimization)
| Symbol | Trades | Winners | PF | Net |
|---|---|---|---|---|
| EURUSD | 137 | 25% | 0.63 | -$14,831 |
| GBPUSD | 104 | 24% | 0.61 | -$12,081 |
| USDJPY | 108 | 31% | 0.87 | -$3,758 |
| XAUUSD | 98 | 41% | 1.36 | +$8,023 |
| XAGUSD | 112 | 38% | 1.11 | +$3,071 |
| NICKEL | 0 | | | |
| **Total** | **559** | **31%** | **0.87** | **-$19,576 (-19.6%)** |

- Max equity drawdown 37.5%. By year: 2025 -$28,062, 2026 +$8,487.
- **NICKEL made no trades.** Its demo spread is $200 to $400 against an hourly ATR of $55 to $85. The spread filter (max 10% of ATR) blocked every signal. That is the filter doing its job.
- Balance curve: [img/multi_asset_A_main_balance.png](img/multi_asset_A_main_balance.png). Run D: [img/multi_asset_D_budget_balance.png](img/multi_asset_D_budget_balance.png).

**About profit.** PF 0.87, -19.6% over 21 months. Metals positive, forex negative, 2025 negative, 2026 positive. That is not stable; the rule has no edge. I picked a simple rule to test the mechanics. The frame and the capital protection are ready and checked; profit depends on the trader's rules, and tuning needs an out-of-sample check.

### Mechanics: what `verify_multi.py` checks
The check does not take the EA's word for anything. Trades come from the tester report, indicators are recomputed in Python from bars exported from MT5. Full output: [verify_output.txt](verify_output.txt).

1. **One entry per signal.** 559 entries; at most 1 per symbol and signal candle. 43 positions closed by SL or TP inside their own candle, 5 of them still inside the entry window: the signal was still valid, the position was gone, and only the server deal history check stopped a second entry. In run C (2 positions per symbol, whole-candle entry, no memory) there were 506 such cases and 0 repeat entries.
2. **Signals match an independent calculation.** Python computed EMA 50/200, RSI 14 and ATR 14 with the same formulas as MT5. **559 of 559** entries are on the right candle, in the right direction, with the right signal tag. 0 entries without a signal. The EA's ATR matched Python's in all 559 cases, and the stop was exactly 1.5 ATR. Of 1,361 signals, 802 were not traded, each with a reason in the EA log: 621 outside the session, 93 symbol already has a position, 84 spread above 10% of ATR, 2 position cap, 2 risk budget.
3. **Lot per symbol.** 559 of 559: risk = 0.5% of balance, lot = risk / loss of 1 lot, rounded down to the step. Balance and volume match the tester report. The key test is the **real loss of trades closed at the stop** divided by the planned risk:

| Symbol | Example (balance ~$99k, risk $490 to $500) | Stop-outs | Loss at SL / risk, median (min..max) | If lots came from tick value only |
|---|---|---|---|---|
| EURUSD | SL 14.2 pips, 3.50 lots | 102 | 0.998 (0.993..1.000) | 1.00 x risk |
| GBPUSD | SL 18.4 pips, 2.70 lots | 79 | 0.997 (0.989..1.000) | 1.00 x risk |
| USDJPY | SL 48.4 pips, 1.58 lots | 75 | 0.996 (0.973..1.000) | 1.00 x risk |
| XAUUSD | SL $7.94, 0.62 lots | 58 | 0.973 (0.771..1.000) | **9.98 x risk** |
| XAGUSD | SL $0.183, 0.54 lots | 70 | 0.976 (0.550..1.000) | **9.98 x risk** |

   The loss at the stop never went above the planned risk. It is below 1.0 only because lots round down (for silver one 0.01 lot step is 2% of the risk). Stops filled worse than planned (gaps): 0.
4. **Position caps and sessions.** At most 4 positions open at once, at most 1 per symbol. 0 entries outside 08:00 to 20:00 or on weekends. The latest entry came 14 min 40 s after the candle opened (window: 15 minutes).
5. **Daily limit.**
   - **C (no risk budget):** 94 halts. In each one the day's P/L was at or below the limit, the start balance and closed P/L matched the report, all positions were closed at the halt, and there were 0 new entries that day. But the median halt day ended at -1.57% and the worst at **-2.77%** with a 1.5% limit: several stops fired almost at once.
   - **D (same + risk budget):** 3 halts, worst day **-1.58%**. The overshoot is at most 0.08%: in the tester the check runs about every 20 seconds and price moves in between.
   - **A:** 0 halts, worst day -2.99% with a 3% limit. The risk budget blocks a trade whose stop could break the limit.
6. **Restart.** Run B wiped all EA memory every cycle and produced **the same 1,118 deals as A: 1,118 of 1,118** match by time, symbol, direction, volume, price and profit. Decisions come from server data only. Stress runs C and D also ran without memory.
7. **Break-even and trailing (run C):** 181 moves to break-even, 1,566 trailing moves, 135 stop-outs with profit already locked in.

### What I found on the way
- **Tick value of metals on MetaQuotes-Demo is 10 times too small.** You can see it in the symbol passport, and real tester trades confirm it: a $165 loss on 0.14 lots for an $11.80 move means a 100 oz contract. With lots from tick value alone, metals would risk 10 times more than asked. The EA logs a warning and uses the safer number.
- **The Strategy Tester reuses the previous run's inputs** for every input the ini leaves out, not the defaults. One of my runs went out with stress settings because of that. `run_tester.py` now writes all 48 inputs and prints the EA's `Started` line with the real settings.
- **A halt on closed loss while floating profit was positive.** The first version had a fallback rule "closed loss >= limit" that closed winning positions although the day as a whole was above the limit. The stress test C caught it; the halt now uses closed + floating only.
- **"WTI" on MetaQuotes-Demo is a stock** (W&T Offshore, $2 to $4), not oil. That server has no oil or gas CFDs and indices are off, so the commodities in the test are silver and nickel.

## Not verified yet
- A live test on a demo account on a weekday: wait for an entry, close and reopen MT5, check the recovery report in the log and that there are no duplicates.
- "Every tick based on real ticks". MetaQuotes-Demo has no commissions and the tester has no slippage. For the mechanics this does not matter; for profit the result would be worse.
- A netting account: the logic is there (one position per symbol, an opposite signal does not open), but only a hedging account was tested.
