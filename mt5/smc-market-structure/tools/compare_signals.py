"""Bar-by-bar comparison of SMC signals: TradingView (Pine) vs MetaTrader 5 (MQL5).

Inputs (CSV):
  tv_signals.csv   time,open,high,low,close,swBull,swBear,intBull,intBear,eqh,eql,pivH,pivL  (exported from TradingView)
  mt5_signals.csv  time,swBull,swBear,intBull,intBear,eqh,eql,pivH,pivL                     (written by SMC_Verify.mq5)

Codes: breaks 1=BOS 2=CHoCH, pivH 1=H 2=HH 3=LH, pivL 1=L 2=LL 3=HL, eqh/eql 1=yes.
The last bar is excluded (it may still be forming on TradingView).

Usage: python compare_signals.py tv_signals.csv mt5_signals.csv
"""
import csv
import sys

COLS = ["swBull", "swBear", "intBull", "intBear", "eqh", "eql", "pivH", "pivL"]


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {int(float(r["time"])): r for r in rows}


def main(tv_path, mt_path):
    tv, mt = load(tv_path), load(mt_path)
    times = sorted(set(tv) & set(mt))[:-1]
    only_tv, only_mt = len(set(tv) - set(mt)), len(set(mt) - set(tv))
    print(f"bars: TradingView {len(tv)}, MT5 {len(mt)}, compared {len(times)} (only TV {only_tv}, only MT5 {only_mt})")
    total_events = total_match = 0
    mismatches = []
    for col in COLS:
        ev_tv = sum(1 for t in times if int(float(tv[t][col])) != 0)
        ev_mt = sum(1 for t in times if int(float(mt[t][col])) != 0)
        same = 0
        for t in times:
            a, b = int(float(tv[t][col])), int(float(mt[t][col]))
            if a == b:
                if a != 0:
                    same += 1
            else:
                mismatches.append((t, col, a, b))
        total_events += max(ev_tv, ev_mt)
        total_match += same
        print(f"  {col:8s} TV events {ev_tv:5d}  MT5 events {ev_mt:5d}  identical {same:5d}")
    print(f"identical events: {total_match} of {total_events}; mismatching bar/column pairs: {len(mismatches)}")
    for t, col, a, b in mismatches[:25]:
        print(f"  mismatch time={t} {col}: TV={a} MT5={b}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
