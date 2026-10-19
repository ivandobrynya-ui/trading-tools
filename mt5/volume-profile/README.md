# Volume Profile (MetaTrader 5 indicator, MQL5)

Files:
- `Volume_Profile.mq5`: the indicator;
- `verify_vp.py`: an independent rebuild of the profile in Python;
- export scripts: [ExportBars.mq5 and ExportIndicator.mq5](../tools/).

A daily volume profile for MT5 with the levels as indicator buffers, so an EA can trade from them through `iCustom`.

## What it does
A volume profile shows volume by price instead of by time: how much traded at each price during the session.
- **POC:** the price row with the most volume in the session.
- **Value area (VAL to VAH):** the zone around the POC that holds 70% of the volume.

How the indicator builds it:
1. **Session = one day in broker server time.** The start hour is an input.
2. **Built from M1 bars** on any chart timeframe from M1 to H4, so the profile is precise on H1 too.
3. **The volume of each M1 bar is spread evenly** over the price rows the bar touched. This is the standard method when tick prices are not available.
4. **Row height.** By default the previous session's range / 100, rounded to the price step, so the profile adapts to volatility.
5. **POC** is the row with the most volume (on a tie, the lower one).
6. **Value area** grows from the POC toward the bigger neighbour until it holds 70%.
7. **6 buffers (lines):** developing POC / VAH / VAL of the current session and POC / VAH / VAL of the previous session.
8. **Histogram** of the current and previous session drawn with rectangles, redrawn at most every 5 seconds to keep the terminal fast.

## Inputs
| Input | Default |
|---|---|
| Row height in points (0 = auto from the previous session range) | 0 |
| Auto mode: rows per previous session range | 100 |
| Value area, % of session volume | 70 |
| Session starts at this server hour | 0 (server midnight) |
| Sessions to calculate | 60 |
| Draw the profile histogram | on |
| Histogram width, % of the session length | 30 |

## Install
1. Copy `Volume_Profile.mq5` to `MQL5\Indicators\Practice\` in your MT5 data folder and compile it in MetaEditor (F7). (`ExportIndicator.mq5` looks for it at `Practice\Volume_Profile` by default.)
2. Drag it onto a chart from the Navigator.
3. From an EA: `iCustom(_Symbol, _Period, "Practice\\Volume_Profile")`, buffers 0 to 5.

## Two MT5 limits I had to work around
1. **An indicator can't wait for history** of another timeframe. MT5 forbids it (error 4401). On the first run the indicator saw only 2,880 M1 bars and returned nothing. Now, when data is missing, it reports "not calculated" and recalculates once the history has loaded. The start of the calculation is limited to the first available M1 bar.
2. **No ticks on weekends, no recalculation.** The export script hung on a Saturday. Now `ExportIndicator.mq5` loads the history itself first (scripts are allowed to wait) and only then creates the indicator.

## How I verified it
XAUUSD H1 on MetaQuotes-Demo, October 2026. `verify_vp.py` rebuilds the profile in Python from the same M1 bars and compares every value:
- **988 closed bars x 6 values: 0 differences.** The largest gap is 1.6e-11, which is floating point rounding;
- **0 broken sanity rules:** on every bar VAL <= POC <= VAH and the value area holds at least 70% of the volume;
- 45 sessions.

To repeat it: export M1 bars with `ExportBars.mq5`, export the indicator buffers with `ExportIndicator.mq5`, then `python verify_vp.py <indicator.csv> <m1_bars.csv>`.

## Notes on data
- MT5 volume on CFDs is **tick volume**: the number of price changes, not contracts traded.
- Real volume for gold is on the CME futures (GC), available from data vendors. The CFD price differs from the futures price by the basis, so levels must be shifted by that difference.
