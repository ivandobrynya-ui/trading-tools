# MT5 export and report toolkit

Small helpers I use to check MT5 tools against independent calculations. Several READMEs in this repo point here.

| File | Type | What it does |
|---|---|---|
| `ExportBars.mq5` | MT5 script | Writes bars of a symbol and timeframe to `Common\Files` as CSV: `time,open,high,low,close,tick_volume,spread`. |
| `ExportIndicator.mq5` | MT5 script | Runs any custom indicator with its default inputs through `iCustom` and writes the bar time plus its first N buffers to `Common\Files` as CSV. Loads the history first, so it also works on weekends. |
| `mt5_report.py` | Python | Reads an MT5 Strategy Tester HTML report (Russian or English terminal) and returns the summary (trades, PF, net profit, drawdown, history quality) and the deal list. |

## Inputs
**ExportBars.mq5**
| Input | Default |
|---|---|
| Symbol | EURUSD |
| Timeframe | M15 |
| From / To | 2024.01.01 / 2026.10.17 |
| Output file (in Common\Files) | bars.csv |
| Close the terminal when done | true |

**ExportIndicator.mq5**
| Input | Default |
|---|---|
| Indicator path under MQL5\Indicators | Practice\Volume_Profile |
| Symbol / timeframe | XAUUSD / H1 |
| Number of buffers to export | 6 |
| Last N bars | 2000 |
| Output file (in Common\Files) | indicator.csv |
| History to load before creating the indicator (days) | 90 |
| Close the terminal when done | true |

## Install and use
1. Copy the two `.mq5` scripts to `MQL5\Scripts\` and compile them in MetaEditor (F7).
2. Run a script from the Navigator on any chart, or without the GUI: an ini file with a `[StartUp]` section (`Script=`, `Symbol=`, `Period=`) passed to `terminal64.exe /config:<ini>`. With "Close the terminal when done" the script closes MT5 itself, so a batch run ends cleanly.
3. The CSV files land in `%APPDATA%\MetaQuotes\Terminal\Common\Files`.
4. In Python:
   ```python
   import mt5_report
   print(mt5_report.summary("report.htm"))  # trades, pf, net, dd, quality, bars, win
   deals = mt5_report.deals("report.htm")   # time, type, direction, volume, price, profit, comment
                                            # + deal, symbol, commission, swap, balance when present
   ```

Used by: [EMA Cross EA](../ema-cross-ea/), [Volume Profile](../volume-profile/), [Multi-Asset EA](../multi-asset-ea/).

No price data is included in this repo. Export your own bars from your terminal.
