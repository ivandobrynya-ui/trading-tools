# EMA Cross EA (MetaTrader 5 Expert Advisor, MQL5)

Files:
- `EMA_Cross_EA.mq5`: the EA;
- `verify_ema.py`: an independent check of every trade in the Strategy Tester report.

A simple EMA 20 / EMA 50 crossover robot, built to a common freelance spec: closed candles only, entry on the next candle, never two trades from one signal, one position per symbol and magic number.

## What it does
On every tick:
1. **Signal from the last two closed candles.** EMA 20 was at or below EMA 50 and is now above it: buy. The mirror case: sell. The forming candle is not used, so the signal does not flicker inside a candle.
2. **"Did I already enter on this candle?"** The EA asks the server for its deal history since the candle opened. If its entry is there, there is no second one. This works after a terminal restart too, because the history lives on the broker's server, not in the EA's memory.
3. **Already in a position the same way:** nothing to do.
4. **Position the other way:** with `InpReverse = true` the EA closes it and reverses; with `false` it waits, one position at a time.
5. **Spread filter.** If the spread is above the limit, the entry waits and retries on the next tick of the same candle. Spreads often widen for seconds, for example at the rollover. If the spread stays wide until the candle closes, the signal is skipped. One log line per candle, no spam.
6. **Entry** at market, SL and TP in points from the entry price (0 = none).

## Inputs
| Group | Input | Default |
|---|---|---|
| Signal | Fast EMA period / slow EMA period | 20 / 50 |
| | On an opposite signal close the position and reverse | true |
| Orders | Lot size | 0.10 |
| | Stop loss / take profit (points, 0 = none) | 300 / 600 |
| | Max spread (points, 0 = off) | 20 |
| | Max slippage (points) | 10 |
| | Magic number | 2050 |

## Install
1. Copy `EMA_Cross_EA.mq5` to `MQL5\Experts\` in your MT5 data folder and compile it in MetaEditor (F7).
2. Drag it onto a chart (it was built for EURUSD M15), set the inputs and turn on Algo Trading.

To repeat the check:
1. Run the EA in the Strategy Tester and save the report as HTML.
2. Export the same bars with [ExportBars.mq5](../tools/) (EURUSD M15 by default).
3. `python verify_ema.py <bars.csv> <report.htm>`. The script reads the report with [mt5_report.py](../tools/) from `../tools`. It assumes M15 bars and trades from 1 Jan 2025; change those two numbers in the script for another run.

## How I verified it
**Strategy Tester:** EURUSD M15, 1 Jan 2025 to 16 Oct 2026, 100% history quality: 836 trades, PF 1.02.

**Independent check with `verify_ema.py`.** The script computes EMA 20/50 in Python from bars exported from MT5 and compares every trade in the tester report with it:
- **836 of 836** entries are at the open of the candle right after a cross, in the right direction;
- **0** entries without a signal;
- 12 signals were not traded, **all because of the spread filter** (15 waits in the log; at the rollover the spread reached 59 points);
- at most 1 entry per candle and at most 1 open position at any time.

**Why a separate check.** "The tester shows a profit" says nothing about whether the code is right. A comparison with an independent calculation shows that the robot does exactly what the spec says.

**About profit.** PF 1.02 is about zero. An EMA 20/50 cross has no edge on its own. The robot follows the rules exactly; whether it makes money depends on the rules and filters you give it.
