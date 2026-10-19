# Order Blocks & Fair Value Gaps (TradingView, Pine Script v6)

File: `order_blocks_fvg.pine`

This indicator draws Smart Money Concepts supply and demand zones, order blocks (OB) and fair value gaps (FVG), and tracks each zone until price mitigates it.

## What it does
- **Order blocks tied to structure.** When a candle closes beyond the last confirmed pivot (length 10), that is a break of structure. The OB is the extreme candle of the leg that led to the break: the lowest candle before a bullish break, the highest before a bearish one.
- **Filter for news candles.** If the OB candle is taller than 3 x ATR(200), the indicator uses its body instead of the full range. Otherwise one spike creates a huge, useless zone. You can also use bodies for all OBs.
- **Overlapping OBs.** A new OB removes an older active OB of the same direction when they overlap. The chart shows the latest relevant zones, not a pile of boxes.
- **Fair value gaps.** A three-candle imbalance where the middle candle closes through the gap. Gaps smaller than 0.25 x ATR are ignored. Each FVG has a dotted 50% line (consequent encroachment).
- **Mitigation.**
  - An OB is invalidated by a close beyond it (default) or by a wick.
  - An FVG is mitigated by a full fill, a close beyond it or a touch of the 50% line.
  - Mitigated zones are removed or kept on the chart in grey.
- **Limits.** At most 5 active OBs and 10 FVGs per side by default.
- **Labels** sit at the right edge of each zone, next to the current price.
- **No repainting.** Zones and alerts are created and updated on closed bars only (can be switched off).
- **12 alert conditions:** new bullish or bearish OB, first retest of an OB, OB invalidated, new bullish or bearish FVG, price entering an FVG, FVG filled.

## Inputs
| Group | Input | Default |
|---|---|---|
| Order Blocks | Show order blocks | on |
| | Structure pivot length | 10 |
| | Max active OBs per side | 5 |
| | OB invalidated by | Close (or Wick) |
| | Use candle body instead of full range | off |
| | Max OB height (x ATR) | 3.0 |
| | Replace overlapping older OBs | on |
| | Bullish OB and bearish OB colors | |
| Fair Value Gaps | Show fair value gaps | on |
| | Min gap size (x ATR) | 0.25 |
| | Max active FVGs per side | 10 |
| | FVG mitigated when | Full fill (wick), Close beyond, 50% touch (CE) |
| | Show 50% line | on |
| | Bullish FVG and bearish FVG colors | |
| General | Detect only on confirmed (closed) bars | on |
| | Keep mitigated zones on chart (greyed out) | off |
| | Show zone labels | on |

## Install
1. Open a chart in TradingView and open the Pine Editor at the bottom.
2. Delete what is in the editor, paste the whole file and click **Add to chart**.
3. Alerts: right click the chart, **Add alert**, Condition: **OB & FVG**, then pick an event, for example "Bullish OB retest".

## How I verified it
- **Compiled in TradingView** with no errors and no warnings (October 2026).
- **Debug run on XAUUSD 1h, 10,568 bars.** The script created 146 bullish and 128 bearish order blocks and 647 bullish and 513 bearish fair value gaps. With the default limits the chart showed 6 active OBs and 9 FVGs at the same time, which is readable.
- **Visual check on 1h and 15m** around the gold sell-off of 14 October 2026: the bearish FVG after the drop and the OBs above it are where a trader would draw them by hand.
- **Two fixes came from that review:**
  - an old OB on a news candle of about 60 points covered half the screen, so I added the max height filter (3 x ATR);
  - the "FVG" label was cut off inside small zones, so labels moved to the right edge of the zone.

## Notes
This is an analysis tool, not a trading system or financial advice.
