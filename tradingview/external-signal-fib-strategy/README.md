# External Signal + Fibonacci Targets (TradingView, Pine Script v6)

Files:
- `external_signal_fib_strategy.pine`: the strategy;
- `demo_signal_provider.pine`: a simple stand-in for a third-party signal indicator, used for testing.

Many traders buy signals from a provider: a closed-source indicator that draws Buy and Sell arrows. This strategy backtests those arrows without the provider's code. It reads the signals from the other indicator on the chart and manages the trade with a swing stop and Fibonacci take profits.

## What it does
- **Reads another indicator's signals.** An `input.source` can point at a plot of any other indicator on the chart. You pick the provider's Buy plot and Sell plot in the settings. If the provider has a single +1 / -1 plot, tick "Single plot".
- **Does not trade blind.** Until a source is picked, the input stays at `close`. The strategy detects that and makes no trades, instead of trading noise on every bar.
- **Demo mode.** "Built-in demo (EMA cross)" uses EMA 20/50 crosses, so the mechanics can be tested without a provider.
- **Stop and targets.**
  - The swing is the last confirmed pivot high and low (10 bars on each side, so nothing repaints).
  - Long: the stop goes below the low of the current leg by 10% of the swing; **TP1** at 1.0 of the swing closes 50%; **TP2** at the 1.618 extension closes the rest. Shorts mirror it.
  - Every exit is tagged **TP1**, **TP2** or **SL** in the trade list.
  - On the last bar the Fibonacci levels of the last swing are drawn: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618.
- **Test settings:** 0.02% commission, 2 ticks of slippage, 10% of equity per trade. All are editable.

## Inputs
| Group | Input | Default |
|---|---|---|
| Signal source | Signals from | External indicator (or Built-in demo) |
| | External BUY plot | close (= not connected) |
| | External SELL plot | close (= not connected) |
| | Single plot: +1 buy / -1 sell | off |
| Fibonacci targets | Swing pivot length | 10 |
| | TP1 (x swing size) | 1.0 |
| | TP2 (x swing size) | 1.618 |
| | Close % at TP1 | 50 |
| | Stop beyond the swing (x swing size) | 0.1 |
| | Draw the Fibonacci levels of the last swing | on |

## Install
1. Add the provider's indicator to the chart. To try it without one, add `demo_signal_provider.pine` first.
2. Paste `external_signal_fib_strategy.pine` into the Pine Editor and click **Add to chart**.
3. In the strategy settings pick the provider's Buy plot as "External BUY plot" and the Sell plot as "External SELL plot".

**TradingView quirk:** when you update the strategy code on the chart, the external source resets to `close`, and the strategy shows 0 trades. Pick the source again after every code update.

## How I verified it
OANDA:XAUUSD 1h, January 2025 to October 2026, free TradingView plan.

| Mode | Rows in the trade list* | PF | Drawdown | What it proves |
|---|---|---|---|---|
| No source picked | 0 | | | it does not trade blind |
| Built-in demo, EMA 20/50 | 224 | 0.97 | 1.1% | TP1 / TP2 / SL and reversals work |
| Demo provider (RSI 30/70) | 485 | 0.80 | 4.9% | signals come from the other indicator |

\* A trade that hits TP1 and TP2 is two rows in the TradingView list.

**Entries follow the provider, checked bar by bar:**
- on 5,300 bars the strategy's signal marks matched the provider's arrows: **0 differences**;
- 123 entries with full data: every one was placed **on the bar right after a signal of the right side**, 0 errors.

**A bug I found and fixed.** In the first version almost every provider trade hit the stop, and many signals were skipped: 54 of 63 long rows in the trade list were stops, and 56 of 63 short rows. The reason: a pivot confirms 10 bars late. An "oversold" signal comes when price is already below the last confirmed low, so a stop "below the swing low" sat right at the price, or above it. Now the stop goes beyond the real extreme of the current leg, the lowest low since the last swing high. After the fix: 0 stops on the wrong side, the average stop on gold is $30 to $60, and a trade that stops out lasts 21 bars on average instead of 1 or 2.

**Why the provider test loses money.** The demo provider is crude on purpose: it buys oversold readings against a strong gold uptrend. The point of this tool is to show honest stats for a provider before anyone automates it. If the stats are bad, that saves money.

## Notes
This tool measures signals. It does not make them better.
