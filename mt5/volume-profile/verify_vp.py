"""Recomputes the volume profile from exported M1 bars and compares it with the MQL5 buffers.

Usage: python verify_vp.py <indicator.csv from ExportIndicator> <M1 bars csv from ExportBars>
Checks every closed H1 bar: developing POC/VAH/VAL and previous-session POC/VAH/VAL,
plus sanity rules (VAL <= POC <= VAH, value area holds >= 70% of the volume).
"""
import csv
import math
import sys
from bisect import bisect_left

TICK, AUTO_ROWS, VA_PCT, MAX_DAYS, HOUR = 0.01, 100, 70.0, 60, 0


def session_day(t):
    s = t - HOUR * 3600
    return s - s % 86400 + HOUR * 3600


class Profile:
    def __init__(self, day, row):
        self.day, self.row, self.ok, self.base, self.vol, self.hi, self.lo = day, row, False, 0.0, [], 0.0, 0.0

    def add(self, high, low, volume):
        if volume <= 0 or high < low:
            return
        if not self.ok:
            self.base = math.floor(low / self.row) * self.row
            self.ok, self.hi, self.lo = True, high, low
        self.hi, self.lo = max(self.hi, high), min(self.lo, low)
        lo = math.floor((low - self.base) / self.row + 1e-9)
        hi = math.floor((high - self.base) / self.row + 1e-9)
        if lo < 0:
            shift = -lo
            self.vol = [0.0] * shift + self.vol
            self.base -= shift * self.row
            lo += shift
            hi += shift
        if hi >= len(self.vol):
            self.vol += [0.0] * (hi + 1 - len(self.vol))
        share = volume / (hi - lo + 1)
        for k in range(lo, hi + 1):
            self.vol[k] += share

    def value_area(self):
        n = len(self.vol)
        if not self.ok or n == 0:
            return None
        total, poc = 0.0, 0
        for k in range(n):
            total += self.vol[k]
            if self.vol[k] > self.vol[poc]:
                poc = k
        if total <= 0:
            return None
        target, acc, lo, hi = total * VA_PCT / 100.0, self.vol[poc], poc, poc
        while acc < target and (lo > 0 or hi < n - 1):
            up = self.vol[hi + 1] if hi + 1 < n else -1.0
            dn = self.vol[lo - 1] if lo - 1 >= 0 else -1.0
            if up >= dn:
                hi += 1
                acc += self.vol[hi]
            else:
                lo -= 1
                acc += self.vol[lo]
        return (self.base + (poc + 0.5) * self.row, self.base + (hi + 1) * self.row, self.base + lo * self.row, acc / total)


def row_height(prev_range):
    r = prev_range / AUTO_ROWS if prev_range > 0 else TICK * AUTO_ROWS
    return max(TICK, math.ceil(r / TICK) * TICK)


ind = list(csv.DictReader(open(sys.argv[1], encoding="ascii")))
m1 = list(csv.DictReader(open(sys.argv[2], encoding="ascii")))
m1_t = [int(r["time"]) for r in m1]
times = [int(r["time"]) for r in ind]
cutoff = session_day(times[-1]) - MAX_DAYS * 86400
# the indicator may start later if the terminal had less M1 history: start where its output starts
first_out = next(int(r['time']) for r in ind if r['b0'])
cutoff = max(cutoff, session_day(first_out))

S, prev, prev_range = None, None, 0.0
checked = mism = 0
worst = 0.0
bad_sanity = 0
for i in range(len(ind) - 1):  # closed bars only
    t = times[i]
    if t < cutoff:
        continue
    day = session_day(t)
    if S is None or day != S.day:
        if S is not None:
            va = S.value_area()
            if va:
                prev, prev_range = va[:3], S.hi - S.lo
        S = Profile(day, row_height(prev_range))
    a = bisect_left(m1_t, t)
    b = bisect_left(m1_t, t + 3600)
    for r in m1[a:b]:
        S.add(float(r["high"]), float(r["low"]), float(r["tick_volume"]))
    va = S.value_area()
    mine = list(va[:3]) if va else [None] * 3
    mine += list(prev) if prev else [None] * 3
    theirs = [float(ind[i][f"b{k}"]) if ind[i][f"b{k}"] else None for k in range(6)]
    checked += 1
    for a_, b_ in zip(mine, theirs):
        if (a_ is None) != (b_ is None) or (a_ is not None and abs(a_ - b_) > 1e-6):
            mism += 1
            if mism <= 5:
                print("mismatch", t, mine, theirs)
        elif a_ is not None:
            worst = max(worst, abs(a_ - b_))
    if va and not (va[2] <= va[0] <= va[1] and va[3] >= VA_PCT / 100 - 1e-9):
        bad_sanity += 1

print(f"closed H1 bars checked: {checked} (from {cutoff}), value mismatches: {mism}, max abs diff: {worst:.2e}")
print(f"sanity violations (VAL<=POC<=VAH, value area >= {VA_PCT}%): {bad_sanity}")
print(f"sessions: {len({session_day(t) for t in times if t >= cutoff})}, last developing: POC {S.value_area()[0]:.2f} VAH {S.value_area()[1]:.2f} VAL {S.value_area()[2]:.2f}, previous: {prev}")
