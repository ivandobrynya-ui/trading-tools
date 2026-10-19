# SMC Market Structure for MetaTrader 5 (Pine Script to MQL5 port)

Files:
- `SMC_Market_Structure.mq5`: the MT5 indicator, a port of my TradingView indicator [SMC Market Structure](../../tradingview/smc-market-structure/);
- `SMC_Verify.mq5`: a script that runs the indicator through `iCustom` and writes the bars and the signal buffers to CSV. Two modes: a broker symbol as it is, or bars from a CSV loaded into a custom symbol;
- `tools/smc_replica.js`: an independent copy of the signal logic in plain JavaScript;
- `tools/compare_replica.js`: MQL5 output vs the replica, bar by bar (Node);
- `tools/compare_signals.py`: TradingView output vs MQL5 output, bar by bar (Python).

The goal of a port is simple: the same signals on the same bars, no repainting, decisions on closed bars only. I checked that bar by bar instead of by eye.

## What it does (same as on TradingView)
- **Structure:** swing (pivots 25) and internal (pivots 5) BOS and CHoCH as lines and labels. Internal breaks on a swing level are skipped.
- **Swing points:** HH / HL / LH / LL.
- **Strong / weak high and low** to the right of the last bar.
- **EQH / EQL:** tolerance 0.1 x ATR(200). ATR uses the same RMA formula as Pine, otherwise rare signals near the threshold differ.
- **Candle colors** by internal trend (optional).
- **Object limits:** the last N internal and swing breaks and labels stay, older ones are deleted.
- **MT5 alerts** (Alert and push) for swing and internal BOS / CHoCH and EQH / EQL, on bar close only.
- **8 signal buffers** with a code per bar. They show in the Data Window and other EAs can read them with `iCustom`:
  - breaks: 1 = BOS, 2 = CHoCH;
  - swing highs: 1 = H, 2 = HH, 3 = LH; swing lows: 1 = L, 2 = LL, 3 = HL;
  - EQH / EQL: 1 = yes.
- **No repainting:** every decision is made on closed bars; the forming bar never gives a signal.

## Inputs
| Group | Input | Default |
|---|---|---|
| Swing structure | Show swing BOS / CHoCH, swing pivot length, label swing points | on, 25, on |
| Internal structure | Show internal BOS / CHoCH, internal pivot length | on, 5 |
| Confirmation | Break confirmed by | close (or wick) |
| Levels | Strong / weak high & low, EQH / EQL, EQH / EQL pivot length, threshold (x ATR 200) | on, on, 3, 0.1 |
| Style | Colors, internal line style, swing label font size, color candles by internal trend | dash, 8, off |
| Display | Max internal breaks / max swing breaks / max HH-LL and EQH-EQL objects | 20 / 50 / 300 |
| Alerts (on bar close) | Swing, internal, EQH / EQL, push notifications | all off |

## Install
1. Copy `SMC_Market_Structure.mq5` to `MQL5\Indicators\Portfolio\` in your MT5 data folder and compile it in MetaEditor (F7). `SMC_Verify.mq5` looks for the indicator at `Portfolio\SMC_Market_Structure`; change that line if you keep it elsewhere.
2. Navigator, Indicators, SMC_Market_Structure: drag it onto a chart.

To repeat the check:
1. Copy `SMC_Verify.mq5` to `MQL5\Scripts\`, compile it and run it on any chart. It writes `mt5_signals.csv` to `Common\Files`.
2. `node tools/compare_replica.js <path to mt5_signals.csv>` prints the bar count, the signals by type and every mismatch.
3. With bars and signals exported from TradingView: `python tools/compare_signals.py tv_signals.csv mt5_signals.csv`.

## How I verified it
**Compile:** MetaEditor (MT5 build 6272), 0 errors, 0 warnings.

**Step 1. Pine = replica, on TradingView data.**
- OANDA:XAUUSD 1h, January 2025 to October 2026.
- A debug copy of the Pine indicator printed its signal codes to the data window. I read the bars and the indicator output from the TradingView chart model (the built-in data export is not available on the free plan) and ran the JavaScript replica on the same bars.
- Result: **10,613 bars, 1,032 signals, 0 differences.**

**Step 2. MQL5 = replica, on MT5 data.**
- XAUUSD H1 on MetaQuotes-Demo, 40,050 bars (30 Dec 2019 to 16 Oct 2026).
- `SMC_Verify.mq5` ran the indicator through `iCustom` and wrote the bars and signals; the replica ran on the same bars.
- Result: **3,773 signals, 0 differences.** By type: swing BOS / CHoCH 240 + 204, internal 967 + 841, EQH 243, EQL 252, HH / LH 509, LL / HL 517.

Together the two steps show that the MQL5 version gives exactly the signals of the Pine original on identical bars.

**What I learned on the way.**
- **TradingView's pivot tie rule,** found from the data: on the left a pivot is cancelled only by a strictly higher (lower) bar, on the right also by an equal one. With the rule the other way round there were 1 or 2 differences per 10,000 bars. This is a common reason why ported indicators drift a little from the original.
- **An MT5 bug I fixed.** Switching a plot to `DRAW_NONE` at run time shifts the buffer-to-plot mapping, and `iCustom` loses the last buffers (error 4806). The draw type now never changes; empty buffers simply draw nothing.

**Different price feeds give different signals.** A broker in MT5 and a data feed in TradingView have slightly different prices, so some signals will differ. For an exact match, compare on the same bars: mode 1 of `SMC_Verify.mq5` loads TradingView bars from CSV into a custom symbol.
