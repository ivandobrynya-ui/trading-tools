# SMC Market Structure (TradingView, Pine Script v6)

File: `smc_market_structure.pine`

This indicator maps market structure the way Smart Money Concepts traders read it, on two levels at once: swing structure for bias and internal structure for timing.

## What it does
- **Swing and internal structure.** Breaks are marked BOS when they continue the trend and CHoCH when they flip it. Swing pivots use 25 bars on each side, internal pivots use 5. An internal break that sits on a swing level is skipped, so there are no duplicate labels.
- **Swing points.** Every new swing high or low is labeled HH, LH, HL or LL against the previous one.
- **Strong and weak high and low.** The current range extremes, extended to the right. In a bearish structure the high is strong (protected) and the low is weak (a likely target), and the other way round.
- **Equal highs and lows (EQH / EQL).** Two short-term pivots within 0.1 x ATR(200) of each other. These are typical liquidity pools.
- **No repainting.** Pivots are confirmed N bars later and signals fire on closed bars only. The bar-close rule can be switched off.
- **Clean chart.** Only the last 20 internal and 50 swing breaks stay on the chart. Older lines and labels are deleted.
- **10 alert conditions:** swing and internal bullish and bearish BOS and CHoCH, EQH, EQL.

## Inputs
| Group | Input | Default |
|---|---|---|
| Swing structure | Show swing BOS / CHoCH | on |
| | Swing pivot length | 25 |
| | Label swing points (HH / HL / LH / LL) | on |
| Internal structure | Show internal BOS / CHoCH | on |
| | Internal pivot length | 5 |
| Confirmation | Break confirmed by | Close (or Wick) |
| | Signal only on confirmed (closed) bars | on |
| Levels | Strong / weak high & low | on |
| | Equal highs / lows (EQH / EQL) | on |
| | EQH / EQL pivot length | 3 |
| | EQH / EQL threshold (x ATR) | 0.1 |
| Style | Bullish and bearish colors, internal line style, swing label size | Dashed, Small |
| | Color candles by internal trend | off |
| Display | Max internal breaks on chart | 20 |
| | Max swing breaks on chart | 50 |

## Install
1. Open a chart in TradingView and open the Pine Editor at the bottom.
2. Delete what is in the editor, paste the whole file and click **Add to chart**.
3. Alerts: right click the chart, **Add alert**, Condition: **SMC Structure**, then pick an event, for example "Swing bullish CHoCH".

The free TradingView plan allows 2 indicators per chart.

## How I verified it
- **Compiled in TradingView** with no errors and checked on XAUUSD 15m, 30m and 1h (October 2026).
- **Bar by bar against an independent replica.** When I ported this indicator to MT5, I wrote the same logic a second time in plain JavaScript ([smc_replica.js](../../mt5/smc-market-structure/tools/smc_replica.js)). I ran a debug copy of the Pine script that prints its signal codes to the data window, read the bars and the indicator output from the TradingView chart, and ran the replica on the same bars.
  - OANDA:XAUUSD 1h, January 2025 to October 2026: **10,613 bars, 1,032 signals, 0 differences.**
- **The pivot tie rule.** This check also showed the exact rule `ta.pivothigh` and `ta.pivotlow` use for equal bars. On the left side a pivot is cancelled only by a strictly higher (lower) bar. On the right side an equal bar cancels it too. With the rule the other way round, 1 or 2 signals per 10,000 bars drift. That is a common reason why ported indicators don't quite match the original.

## Notes
- The MetaTrader 5 version is in [mt5/smc-market-structure](../../mt5/smc-market-structure/).
- This is an analysis tool, not a trading system or financial advice.
