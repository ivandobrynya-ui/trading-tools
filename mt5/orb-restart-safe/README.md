# ORB Restart-Safe (MetaTrader 5 Expert Advisor, MQL5)

File: `ORB_RestartSafe.mq5`

An Opening Range Breakout EA built around one requirement: a restart of the terminal or the VPS must never create a duplicate order.

## What it does
- **Range** in broker server time, 08:00 to 08:30 by default. It is built from M1 bars, so the EA can rebuild it at any moment.
- **Orders.** After the range closes, the EA places a **Buy Stop** above the high and a **Sell Stop** below the low, with an offset in points.
  - When one fills, the other is cancelled (OCO).
  - OCO also works if the terminal was off at the moment of the fill: the EA sees the fill in the deal history.
- **Stop and target.** SL at the opposite side of the range, TP at 1.5R (input).
- **Position size.** Fixed lots or risk % of balance. Risk is computed with `OrderCalcProfit` and rounded down to the lot step. If the risk-based lot is below the minimum lot, that side is skipped instead of opening with more risk than asked.
- **Filters:** max spread, min and max range size, days of the week, long only or short only.
- **Break-even** (optional): at N x R profit the stop moves past the entry.
- **Time rules:** cancel untriggered orders at a set time; close the position at a set time (optional).
- **Price already beyond the level** when orders go out: wait for a return (until a deadline) or enter at market. Your choice.
- **Decision log** in the Experts tab and in `MQL5/Files/ORB_<symbol>_<magic>.log`: the range, orders, OCO, spread rejects, break-even and server errors with codes.

### Restart protection
The EA keeps **nothing in memory**. On every tick and every trade event it reads from the server again:
- its own orders and positions (by symbol and magic number);
- the order and deal history of the current day.

The decision "place an order or not" comes only from that state. If an order of this side already existed today (active, filled or cancelled), it is not placed a second time. So a restart of MT5 or the VPS, a lost connection or a server timeout does not create duplicates. Pending orders left from previous days are deleted automatically.

## Inputs
| Group | Input | Default |
|---|---|---|
| Range (server time) | Range start / end | 08:00 / 08:30 |
| | Stop placing orders N minutes after range end | 30 |
| | Cancel untriggered orders at | 12:00 |
| | Close open position at | empty (never) |
| | Trade Monday ... Friday | all on |
| Orders | Direction | both |
| | Offset from range high/low (points) | 20 |
| | If price is already beyond the level | wait |
| | Min / max range size (points, 0 = off) | 0 / 0 |
| Risk | Position size mode | risk % |
| | Fixed lots | 0.10 |
| | Risk per trade (% of balance) | 1.0 |
| | Take profit (R multiple) | 1.5 |
| Filters | Max spread (points, 0 = off) | 50 |
| Break-even | Move SL to break-even / trigger (R) / lock-in (points) | off / 1.0 / 5 |
| General | Magic number | 260417 |
| | Max slippage for market orders (points) | 20 |
| | Write decisions to a log file | on |

## Install
1. Copy `ORB_RestartSafe.mq5` to `MQL5\Experts\` in your MT5 data folder (File, Open Data Folder).
2. Open it in MetaEditor and compile (F7).
3. Drag the EA onto a chart (M5 or lower works well), set the inputs and turn on Algo Trading.

## How I verified it
- **Compile:** MetaEditor (MT5 build 6272), 0 errors, 0 warnings.
- **Strategy Tester:** XAUUSD M5 on MetaQuotes-Demo, 1 Jan 2023 to 16 Oct 2026, "1 minute OHLC" model, 99% history quality, 266,702 bars, $10,000 deposit, 1% risk.
- **Mechanics, read from the tester report:**
  - 964 trading days with **exactly one entry per day**; days with two entries: **0**;
  - orders: 964 entries, 964 exits (SL or TP) and 964 cancelled opposite orders, so OCO worked every day;
  - exits: 605 by stop loss, 359 by take profit.

### Backtest results (the rules as they are, no optimization)
| Range window (server time) | Trades | PF | Net | Max drawdown | 2023 / 2024 / 2025 / 2026 |
|---|---|---|---|---|---|
| 08:00 to 08:30 (default) | 964 | 0.84 | -$5,283 | 62% | -4.4K / -0.4K / -0.3K / -0.2K |
| 10:00 to 10:30 (London) | 960 | 1.10 | +$6,715 | 24% | +1.6K / -0.5K / +7.5K / -1.9K |
| 16:30 to 17:00 (New York) | 765 | 0.95 | -$1,833 | 40% | +1.1K / -1.3K / -0.7K / -0.6K |

**What this means.** A plain ORB on gold has no edge. The London window is positive almost only because of 2025, which is not stable. This EA is a reliable way to execute a trader's rules. It is not a profitable system out of the box.

Balance curves are in [img/](img/): [08:00](img/orb_xauusd_0800_balance.png), [London](img/orb_xauusd_london_balance.png), [New York](img/orb_xauusd_newyork_balance.png).

## Not verified yet
- A live restart test on a demo account on a weekday: place the two orders, close and reopen MT5, confirm in the log and the Trade tab that no new orders appeared.
- A run on "Every tick based on real ticks" for the final numbers.
