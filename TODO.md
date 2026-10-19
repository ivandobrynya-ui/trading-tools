# TODO

## Images to retake with English labels
My MT5 terminal runs in Russian, so these balance charts carry the Russian label "Баланс". Switch MT5 to English (View > Languages > English, restart the terminal), run the same test again and replace the file. Keep the file names, so the README links keep working.

| Image | Test to rerun |
|---|---|
| `mt5/orb-restart-safe/img/orb_xauusd_0800_balance.png` | ORB, XAUUSD M5, 2023.01.01 to 2026.10.16, range 08:00 to 08:30 |
| `mt5/orb-restart-safe/img/orb_xauusd_london_balance.png` | the same, range 10:00 to 10:30 |
| `mt5/orb-restart-safe/img/orb_xauusd_newyork_balance.png` | the same, range 16:30 to 17:00 |
| `mt5/multi-asset-ea/img/multi_asset_A_main_balance.png` | Multi-Asset EA, run A |
| `mt5/multi-asset-ea/img/multi_asset_D_budget_balance.png` | Multi-Asset EA, run D |

`mt5/tools/mt5_report.py` reads reports in both languages, so the verification scripts keep working after the switch.

## Images to add
- TradingView screenshots for the five Pine tools (there are none yet): XAUUSD 15m or 1h, camera icon > Save image. Put them in `tradingview/<tool>/img/` and link them from each README.
- A short screen recording of a restart test (see below) for the ORB or Multi-Asset EA.

## Live checks still open
- ORB and Multi-Asset EA: restart test on a demo account on a weekday. Wait for orders or an entry, close and reopen MT5, check the log and the Trade tab for duplicates.
- TradingView to MT5 bridge: the live chain with a real webhook (needs a paid TradingView plan).
- Bybit bot: live orders on testnet (needs testnet API keys in a local `.env`).

## Links to add
- TradingView script links once the three indicators are published as open source.
- Upwork profile link in the top README.
