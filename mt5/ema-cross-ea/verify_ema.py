"""Checks the EMA_Cross_EA tester run against an independent calculation.

1) EMA 20/50 crosses are computed in Python from exported MT5 bars (closed candles only).
2) Every EA entry must sit at the open of the candle right after a cross, in the cross direction.
3) No candle may have more than one entry; positions never overlap (in/out alternate).
Usage: python verify_ema.py <bars.csv> <tester report .htm>
"""
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mt5_report  # noqa: E402


def ema(values, period):
    k = 2.0 / (period + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


bars = list(csv.DictReader(open(sys.argv[1], encoding="ascii")))
times = [int(b["time"]) for b in bars]
closes = [float(b["close"]) for b in bars]
f, s = ema(closes, 20), ema(closes, 50)

start = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp())
expected = {}  # open time of the candle after the cross -> direction
for i in range(2, len(bars)):
    # signal from closed candles i-2 (older) and i-1 (just closed); entry at the open of candle i
    up = f[i - 2] <= s[i - 2] and f[i - 1] > s[i - 1]
    dn = f[i - 2] >= s[i - 2] and f[i - 1] < s[i - 1]
    if (up or dn) and times[i] >= start:
        expected[times[i]] = "buy" if up else "sell"

deals = mt5_report.deals(sys.argv[2])
entries = [d for d in deals if d["direction"] == "in"]
def ts(t):
    return int(dt.datetime.strptime(t, "%Y.%m.%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())

matched, wrong_dir, not_at_signal, per_candle = 0, 0, [], {}
for d in entries:
    t = ts(d["time"])
    candle = t - t % 900
    per_candle[candle] = per_candle.get(candle, 0) + 1
    if candle in expected:
        if expected[candle] == d["type"]:
            matched += 1
        else:
            wrong_dir += 1
    else:
        not_at_signal.append(d["time"])
missed = sorted(set(expected) - {ts(d["time"]) - ts(d["time"]) % 900 for d in entries})

# positions never overlap: running count of open positions must stay 0..1
open_pos, max_open = 0, 0
for d in deals:
    if d["direction"] == "in":
        open_pos += 1
    elif d["direction"] in ("out", "out by"):
        open_pos -= 1
    max_open = max(max_open, open_pos)

print(mt5_report.summary(sys.argv[2]))
print(f"crosses (expected entries): {len(expected)}")
print(f"EA entries: {len(entries)} | at the right candle and direction: {matched} | wrong direction: {wrong_dir} | not at a signal candle: {len(not_at_signal)}")
print(f"signals the EA skipped: {len(missed)} {[dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d %H:%M') for t in missed[:10]]}")
print(f"max entries in one candle: {max(per_candle.values())} | max open positions at once: {max_open}")
print("entries more than 60 s after the candle open:", sum(1 for d in entries if ts(d["time"]) % 900 > 60))
