# Liquidity & Sessions (TradingView, Pine Script v6)

File: `liquidity_sessions.pine`

This indicator maps the liquidity intraday traders watch most: session ranges and the previous day and week extremes. It flags when price sweeps them.

## What it does
- **Kill zones.** Range boxes for Asia, London and New York. Defaults are 20:00 to 00:00, 02:00 to 05:00 and 07:00 to 10:00 New York time, so US daylight saving is handled automatically. Times and timezone are inputs. Works on timeframes up to 1h.
- **Liquidity levels.**
  - When a session ends, its high and low extend to the right until price trades through them. A level nobody touches for 3 days (default) is removed.
  - Previous day high and low (PDH / PDL) and previous week high and low (PWH / PWL).
  - No repainting: the higher timeframe values come from `request.security` with `[1]` and `lookahead_on`, so only closed days and weeks are used.
- **Sweep or break.**
  - **Sweep:** a wick beyond the level and a close back inside. The chart gets a label such as "PDH sweep" or "Asia L sweep".
  - **Break:** a close beyond the level. The level ends; the label is optional.
  - Checked on closed bars only (can be switched off).
- **Dashboard.** The current kill zone and a table of PWH, PDH, London H, Asia H, Asia L, London L, PDL and PWL with price and status: open, swept, broken or expired.
- **Alerts.**
  - 10 alert conditions: buy-side sweep, sell-side sweep, PDH, PDL, PWH, PWL, Asia high and low, London high and low.
  - One `alert()` call with a message that names the level that was swept and the price.

## Inputs
| Group | Input | Default |
|---|---|---|
| Sessions (kill zones) | Timezone | America/New_York |
| | Asia (on/off, time, color) | 2000-0000 |
| | London (on/off, time, color) | 0200-0500 |
| | New York (on/off, time, color) | 0700-1000 |
| | Session range boxes | on |
| | Keep boxes for last N days | 10 |
| Liquidity levels | Session highs/lows (extend until swept) | on |
| | Drop untouched session levels after (days) | 3 |
| | Previous day high/low (PDH/PDL) | on |
| | Previous week high/low (PWH/PWL) | on |
| Sweeps & display | Confirm sweeps on bar close | on |
| | Label sweeps | on |
| | Label breaks | off |
| | Dashboard | on |

If you trade full sessions instead of kill zones, change the times, for example Asia 1800-0300, London 0300-1200, New York 0800-1700.

## Install
1. Open a chart in TradingView (1h or lower) and open the Pine Editor at the bottom.
2. Delete what is in the editor, paste the whole file and click **Add to chart**.
3. Alerts: right click the chart, **Add alert**, Condition: **Liquidity & Sessions**, then pick a level, or pick "Any alert() function call" to get the message with the level name and price.

## How I verified it
- **Compiled in TradingView** on the first try, no errors, on XAUUSD 1h and 15m (October 2026).
- **Found and fixed a drawing bug.** PDH, PDL, PWH and PWL lines were created with zero length on the first bar of a day, and TradingView draws a zero-length line with `extend.right` as a vertical line. Each line now starts as a one-bar segment, and all levels are horizontal.
- **Checked on XAUUSD 15m for 14 October 2026:**
  - the Asia, London and New York boxes;
  - the New York sell-off with a sweep of the session low ("NY L sweep");
  - PDH about 4170, PDL about 4073, PWL about 4066;
  - the dashboard showed PWH swept, Asia H and L swept, PDH and PDL open, which matches the chart.

## Notes
This is an analysis tool, not a trading system or financial advice.
