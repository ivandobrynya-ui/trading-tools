# Multi-Indicator Value Strategy (TradingView, Pine Script v6)

File: `multi_indicator_value_strategy.pine` (strategy)

A long-only strategy that waits until Bollinger Bands, RSI, Stochastic and MACD agree on a turn, and buys a stock only when its P/E and P/B say it is not overpriced. I built it to a typical freelance spec: four indicators, a valuation filter and an honest backtest.

## What it does
- **Four votes for a buy.** Each indicator votes when it sees a turn up after a drop:
  - Bollinger: price crosses back above the lower band;
  - RSI: RSI crosses above 35;
  - Stochastic: %K crosses above %D in the oversold zone (below 30 by default);
  - MACD: the MACD line crosses above the signal line.
- **Votes live for 5 bars.** Four indicators almost never turn on the same candle. A buy signal needs 3 of 4 votes within 5 bars. Both numbers are inputs. The sell side mirrors it: upper band, RSI below 65, Stochastic down above 70, MACD down.
- **Valuation filter (stocks only).** A long is allowed only if P/E (TTM) is 30 or less and P/B (last quarter) is 8 or less. A loss-making company has no P/E and does not pass. On gold, forex and crypto the filter switches itself off.
- **Exits:** stop at 2 x ATR, take profit at 4 x ATR, and the long closes on a sell signal (can be switched off).
- **Table on the chart:** buy and sell votes, current P/E and P/B, and a verdict: OK, too expensive or no data.
- **Alerts:** "Buy signal" and "Sell signal".
- **No lookahead.** Decisions are made on the bar close and filled at the next bar open. The test includes 0.05% commission and 2 ticks of slippage. Position size is 95% of equity.

## Inputs
| Group | Input | Default |
|---|---|---|
| Technical signals | Bollinger length / multiplier | 20 / 2.0 |
| | RSI length / oversold / overbought | 14 / 35 / 65 |
| | Stochastic %K / %D / oversold / overbought | 14 / 3 / 20 / 80 |
| | MACD fast / slow / signal | 12 / 26 / 9 |
| | Signals must agree within N bars | 5 |
| | Votes needed (of 4) | 3 |
| Valuation filter (stocks) | Use P/E and P/B filter | on |
| | Max P/E (TTM) for longs | 30 |
| | Max P/B (last quarter) for longs | 8 |
| | Trade when fundamentals are missing | off |
| Exits | ATR length | 14 |
| | Stop loss (x ATR) / Take profit (x ATR) | 2.0 / 4.0 |
| | Close the long on a sell signal | on |

## Install
1. Open a daily stock chart in TradingView and open the Pine Editor.
2. Paste the whole file and click **Add to chart**. Results are in the Strategy Tester tab.

## How I verified it
Tested in TradingView on daily charts (October 2026, free plan).

| Stock | P/E | P/B | Verdict | Trades | Result |
|---|---|---|---|---|---|
| AAPL | 39.37 | 46.67 | too expensive | 3, all between Nov 2018 and Feb 2019, when the stock was cheaper | the filter blocks an expensive stock, as it should |
| JPM | 14.25 | 2.57 | OK | 32 since 3 Jan 2019 | +69.2%, PF 1.77, 51.6% winners, max drawdown 27.7% |

**What the JPM number means.** The strategy made money, but less than buy and hold: JPM grew about 3.3 times over the same years. 32 trades is too few to conclude anything. A fair next step is 10 to 20 stocks and a part of history that was not used for tuning.

**Three problems I found and fixed during the test:**
1. **P/E and P/B showed "n/a".** TradingView's ready-made ids `PRICE_EARNINGS_RATIO` and `PRICE_BOOK_RATIO` return `na` in `request.financial`. I compute both myself: price / diluted EPS (TTM) and price / book value per share (last quarter), the same way TradingView shows them.
2. **Financial data on the free plan starts on 28 Sep 2018.** Before that date the filter can't check valuation and blocks the trade. The input "Trade when fundamentals are missing" lets you change that. Paid plans have more history.
3. **Two trades closed by "Margin call".** The position was 100% of equity, and commission plus an opening gap made it slightly larger than the account. At 95% of equity there are no margin calls.

## Notes
This is a test of an idea, not a recommendation to trade it.
