"""Checks the Multi_Asset_EA Strategy Tester runs: the mechanics, not the profit.

Runs (made with run_tester.py, settings in README.md):
  A_main       normal mode
  B_stateless  the same settings, but the EA forgets all its memory every cycle (as after a restart)
  C_stress     tight daily limit, 2 positions per symbol, entry allowed for the whole bar,
               break-even and trailing on, also stateless, daily risk budget off (so the halt fires often)
  D_budget     C_stress with the daily risk budget on
Bars for the independent signal check: results/bars/maea_bars_<SYMBOL>.csv (tools/export.py).
Usage: python verify_multi.py
"""
import csv
import datetime as dt
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import mt5_report  # noqa: E402

RES = HERE / "results"
MAIN = dict(risk=0.5, daily=3.0, max_total=4, max_sym=1, window_min=15, be_trail=False, budget=True)
STRESS = dict(risk=1.0, daily=1.5, max_total=6, max_sym=2, window_min=0, be_trail=True)
RUNS = {
    "A_main": dict(MAIN, magic=261017),
    "B_stateless": dict(MAIN, magic=261018),
    "C_stress": dict(STRESS, magic=261019, budget=False),
    "D_budget": dict(STRESS, magic=261020, budget=True),
}
TEST_FROM, TEST_TO = "2025.01.01 00:00", "2026.10.16 00:00"
SESSION = (8 * 3600, 20 * 3600)  # 08:00-20:00 server time
EMA_FAST, EMA_SLOW, RSI_N, ATR_N, RSI_BUY, RSI_SELL, SL_ATR = 50, 200, 14, 14, 40.0, 60.0, 1.5
H1 = 3600


def ts(s):
    s = s.strip()
    return int(dt.datetime.strptime(s, "%Y.%m.%d %H:%M:%S" if s.count(":") == 2 else "%Y.%m.%d %H:%M")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def fmt(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y.%m.%d %H:%M")


def ok(flag):
    return "OK" if flag else "FAIL"


class Run:
    def __init__(self, name):
        self.name, self.cfg = name, RUNS[name]
        d = RES / name
        rep = d / f"MAEA_{name}.htm"
        self.summary = mt5_report.summary(rep)
        self.deals = [x for x in mt5_report.deals(rep) if x.get("symbol")]  # trading deals, report order = time order
        for x in self.deals:
            x["t"] = ts(x["time"])
            x["deal"] = int(x["deal"])
        all_deals = mt5_report.deals(rep)
        self.balance_rows = [(ts(x["time"]), x["balance"]) for x in all_deals]
        magic = self.cfg["magic"]
        self.entries = list(csv.DictReader(open(d / f"MAEA_{magic}_entries.csv", encoding="ascii")))
        self.log = (d / f"MAEA_{magic}.log").read_text(encoding="ascii", errors="replace").splitlines()
        self.positions = self._pair()

    def _pair(self):
        """Entry -> exit. The report has no position id, so an 'out' sell closes an open buy of the same symbol
        (same volume first, then FIFO). Exact for 1 position per symbol; for limits and sums the pairing does not matter."""
        open_, out = defaultdict(list), []
        for x in self.deals:
            if x["direction"] == "in":
                p = dict(sym=x["symbol"], side=x["type"], t_in=x["t"], vol=x["volume"], deal=x["deal"], comment=x["comment"],
                         balance=x["balance"], commission=x["commission"])
                open_[(x["symbol"], x["type"])].append(p)
                out.append(p)
            elif x["direction"] == "out":
                q = open_[(x["symbol"], "buy" if x["type"] == "sell" else "sell")]
                k = next((i for i, p in enumerate(q) if abs(p["vol"] - x["volume"]) < 1e-9), 0)
                p = q.pop(k)
                p.update(t_out=x["t"], profit=x["profit"], net=x["profit"] + x["swap"] + x["commission"] + p["commission"],
                         exit=x["comment"])
        return out

    def timeline(self):
        """Yields (deal, open positions total, open per symbol) after every deal."""
        tot, per = 0, Counter()
        for x in self.deals:
            step = 1 if x["direction"] == "in" else -1
            tot += step
            per[x["symbol"]] += step
            yield x, tot, per


# ---------------------------------------------------------------- results table
def results_table(r):
    print(f"\n### {r.name}: tester summary")
    s = r.summary
    print(f"trades {s.get('trades')}, PF {s.get('pf')}, net {s.get('net')}, max equity DD {s.get('dd')}, "
          f"win {s.get('win')}, history quality {s.get('quality')}")
    by = defaultdict(list)
    for p in r.positions:
        if "net" in p:
            by[p["sym"]].append(p["net"])
    print("| symbol | trades | win % | PF | net P/L |")
    print("|---|---|---|---|---|")
    for sym in sorted(by):
        v = by[sym]
        gw, gl = sum(x for x in v if x > 0), -sum(x for x in v if x < 0)
        print(f"| {sym} | {len(v)} | {100 * sum(1 for x in v if x > 0) / len(v):.0f} | {gw / gl if gl else float('inf'):.2f} | {sum(v):,.0f} |")
    years = defaultdict(float)
    for p in r.positions:
        if "net" in p:
            years[fmt(p["t_out"])[:4]] += p["net"]
    print("by year:", ", ".join(f"{y}: {v:,.0f}" for y, v in sorted(years.items())))


# ---------------------------------------------------------------- 1. one entry per signal
def check_duplicates(r):
    ins = [x for x in r.deals if x["direction"] == "in"]
    per_signal = Counter((x["symbol"], x["comment"]) for x in ins)
    per_bar = Counter((x["symbol"], x["t"] - x["t"] % H1) for x in ins)
    print(f"\n[1] one entry per symbol per signal: {len(ins)} entries, max per (symbol, signal bar) = {max(per_signal.values())}, "
          f"max per (symbol, entry bar) = {max(per_bar.values())}, entries without a signal tag = "
          f"{sum(1 for x in ins if not x['comment'].startswith('MAEA '))} -> {ok(max(per_signal.values()) == 1 and max(per_bar.values()) == 1)}")
    # cases where only the deal-history check stood between the EA and a second entry
    closed_in_bar = sum(1 for p in r.positions if "t_out" in p and p["t_out"] < p["t_in"] - p["t_in"] % H1 + H1)
    window = r.cfg["window_min"] * 60 or H1
    closed_in_window = sum(1 for p in r.positions if "t_out" in p and p["t_out"] < p["t_in"] - p["t_in"] % H1 + window)
    room = 0
    for x, tot, per in r.timeline():
        if x["direction"] == "in" and per[x["symbol"]] < r.cfg["max_sym"] and tot < r.cfg["max_total"]:
            room += 1
    print(f"    positions closed inside their entry bar: {closed_in_bar} (inside the entry window: {closed_in_window}); "
          f"entries after which the position caps still allowed another entry on that symbol: {room}. "
          f"In these cases only the server deal history stood against a repeat entry.")


# ---------------------------------------------------------------- 2. lot size per symbol
def check_lots(r):
    pct = r.cfg["risk"]
    by_deal = {x["deal"]: x for x in r.deals if x["direction"] == "in"}
    pos_by_deal = {p["deal"]: p for p in r.positions}
    bad_risk = bad_formula = bad_report = 0
    stat = defaultdict(lambda: dict(n=0, sl=[], lot_eff=[], tick_only=[], ratio=[]))
    for e in r.entries:
        deal, bal, risk = int(e["deal"]), float(e["balance"]), float(e["risk_money"])
        lt, lc, step, lots = float(e["loss_per_lot"]), float(e["loss_per_lot_calc"]), float(e["vol_step"]), float(e["lots"])
        used = max(lt, lc)
        raw = float(e["raw_lots"])  # the CSV rounds risk to cents, so the floor is checked on the EA's unrounded raw lots
        expect = math.floor(raw / step + 1e-9) * step
        st = stat[e["symbol"]]
        st["n"] += 1
        if lt > 0 and lc > 0:
            st["ratio"].append(lc / lt)
        bad_risk += abs(risk - round(bal * pct / 100, 2)) > 0.011
        bad_formula += abs(raw - risk / used) > raw * 1e-4 + 1e-6 or abs(expect - lots) > 1e-6
        d = by_deal.get(deal)
        bad_report += d is None or abs(d["balance"] - bal) > 0.011 or abs(d["volume"] - lots) > max(1e-6, lots * 0.001)
        p = pos_by_deal.get(deal)
        if p and p.get("exit", "").startswith("sl") and not r.cfg["be_trail"]:
            loss = -p["profit"]  # price P/L only: swap is a holding cost, not part of the stop
            st["sl"].append(loss / risk)
            st["lot_eff"].append(loss / (lots * used))
            if lt > 0:
                st["tick_only"].append(loss * (math.floor(risk / lt / step + 1e-9) * step) / lots / risk)
    print(f"\n[2] lot per symbol: {len(r.entries)} entries. risk money = {pct}% of balance: {ok(bad_risk == 0)} ({bad_risk} off); "
          f"lots = floor(risk / loss per lot, volume step): {ok(bad_formula == 0)} ({bad_formula} off); "
          f"balance and volume match the tester report: {ok(bad_report == 0)} ({bad_report} off)")
    if r.cfg["be_trail"]:
        return  # stops move to break-even and trail, so the loss at SL no longer measures the planned risk
    print("    | symbol | entries | stopped out | loss at SL / planned risk, median (min..max) | loss / (lots x loss per lot) | "
          "terminal calculator / tick value | loss at SL if lots came from tick value only |")
    print("    |---|---|---|---|---|---|---|")
    for sym in sorted(stat):
        st = stat[sym]
        if not st["sl"]:
            continue
        print(f"    | {sym} | {st['n']} | {len(st['sl'])} | {statistics.median(st['sl']):.3f} ({min(st['sl']):.3f}..{max(st['sl']):.3f}) | "
              f"{statistics.median(st['lot_eff']):.4f} | {statistics.median(st['ratio']):.2f} | "
              f"{statistics.median(st['tick_only']):.2f} x risk |")
    gaps = sum(1 for st in stat.values() for v in st["sl"] if v > 1.02)
    print(f"    stops filled more than 2% beyond the planned risk (gaps): {gaps}")


# ---------------------------------------------------------------- 3. position caps, sessions, timing
def check_limits(r):
    max_tot = max_sym = 0
    for x, tot, per in r.timeline():
        max_tot, max_sym = max(max_tot, tot), max(max_sym, max(per.values()))
    ins = [x for x in r.deals if x["direction"] == "in"]
    out_of_session = [x for x in ins if not (SESSION[0] <= x["t"] % 86400 < SESSION[1]) or
                      dt.datetime.fromtimestamp(x["t"], dt.timezone.utc).weekday() > 4]
    window = r.cfg["window_min"] * 60 or H1
    late = [x for x in ins if x["t"] % H1 > window]
    print(f"\n[3] caps: max open overall {max_tot} (limit {r.cfg['max_total']}), max per symbol {max_sym} "
          f"(limit {r.cfg['max_sym']}) -> {ok(max_tot <= r.cfg['max_total'] and max_sym <= r.cfg['max_sym'])}; "
          f"entries outside 08:00-20:00 Mon-Fri: {len(out_of_session)}; entries later than the entry window: {len(late)} -> "
          f"{ok(not out_of_session and not late)}; slowest entry {max(x['t'] % H1 for x in ins)} s after the bar open")


# ---------------------------------------------------------------- 4. daily loss limit
HALT = re.compile(r"^(\S+ \S+) DAILY LIMIT hit: day P/L (-?[\d.]+) \(closed (-?[\d.]+), floating (-?[\d.]+)\) <= -([\d.]+) = "
                  r"([\d.]+)% of day start balance ([\d.]+)\. Open positions: (\d+)\.")


def balance_before(r, t):
    b = r.balance_rows[0][1]
    for tt, bal in r.balance_rows:
        if tt >= t:
            break
        b = bal
    return b


def check_daily(r):
    halts = [HALT.match(line) for line in r.log]
    halts = [m for m in halts if m]
    problems = []
    overshoot = []
    for m in halts:
        t = ts(m.group(1))
        pnl, closed, limit, pct, start, n_open = (float(m.group(2)), float(m.group(3)), float(m.group(5)), float(m.group(6)),
                                                  float(m.group(7)), int(m.group(8)))
        day = t - t % 86400
        if pnl > -limit or abs(limit - start * pct / 100) > 0.011:
            problems.append(f"{m.group(1)} limit arithmetic")
        if abs(balance_before(r, day) - start) > 0.011:
            problems.append(f"{m.group(1)} day start balance {start} vs report {balance_before(r, day)}")
        # stops hit on the halt's own tick happen before the check; the EA's own closes (empty comment) after it
        by_halt = lambda p: p.get("t_out") == t and p["exit"] == ""
        rep_closed = sum(p["net"] for p in r.positions if "t_out" in p and day <= p["t_out"] <= t and not by_halt(p))
        if abs(rep_closed - closed) > 0.05:
            problems.append(f"{m.group(1)} closed P/L {closed} vs report {rep_closed:.2f}")
        open_at = sum(1 for p in r.positions if p["t_in"] <= t and (p.get("t_out", 10 ** 12) > t or by_halt(p)))
        closed_now = [p for p in r.positions if by_halt(p)]
        if len(closed_now) != n_open or open_at != n_open:
            problems.append(f"{m.group(1)} open {n_open}, report open {open_at}, closed at halt {len(closed_now)}")
        if any(p["t_in"] > t and p["t_in"] < day + 86400 for p in r.positions):
            problems.append(f"{m.group(1)} entry after the halt on the same day")
        day_net = sum(p["net"] for p in r.positions if "t_out" in p and day <= p["t_out"] < day + 86400)
        overshoot.append(-day_net / start * 100)
    # every day that lost more than the limit must have a halt that day
    days = defaultdict(float)
    for p in r.positions:
        if "t_out" in p:
            days[p["t_out"] - p["t_out"] % 86400] += p["net"]
    halted_days = {ts(m.group(1)) - ts(m.group(1)) % 86400 for m in halts}
    unprotected = [fmt(d)[:10] for d, v in days.items() if -v > balance_before(r, d) * r.cfg["daily"] / 100 + 0.01 and d not in halted_days]
    print(f"\n[4] daily loss limit {r.cfg['daily']}%: halts {len(halts)}; per halt: P/L <= -limit, day start balance and closed P/L "
          f"match the report, all open positions closed at the halt, no new entry that day -> {ok(not problems)} ({len(problems)} problems)")
    for s in problems[:10]:
        print("    problem:", s)
    if overshoot:
        print(f"    final loss on halt days, % of day start balance: median {statistics.median(overshoot):.2f}, "
              f"max {max(overshoot):.2f} (limit {r.cfg['daily']}; checks run every tester tick, about 20 s)")
    print(f"    days that lost more than the limit without a halt: {len(unprotected)} {unprotected[:5]}")
    pct = sorted((v / balance_before(r, d) * 100, d) for d, v in days.items())
    over = [x for x in pct if -x[0] > r.cfg["daily"] + 0.005]
    print(f"    worst day: {fmt(pct[0][1])[:10]} {pct[0][0]:.2f}%; days that closed beyond the limit: {len(over)} of {len(days)} "
          f"(daily risk budget {'on' if r.cfg['budget'] else 'off'})")


# ---------------------------------------------------------------- 5. restart: stateless run vs normal run
def check_restart(a, b):
    key = lambda d: (d["time"], d["symbol"], d["type"], d["direction"], d["volume"], d["price"], d["profit"])
    same = sum(1 for x, y in zip(a.deals, b.deals) if key(x) == key(y))
    print(f"\n[5] restart safety: {b.name} forgot all memory every cycle; deals {len(b.deals)} vs {len(a.deals)} in {a.name}, "
          f"identical {same} -> {ok(same == len(a.deals) == len(b.deals))}")


# ---------------------------------------------------------------- 6. signals from independent indicator math
def ema(v, n):
    k, out, prev = 2 / (n + 1), [], None
    for x in v:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(c, n):
    """MT5 RSI: Wilder smoothing seeded with simple averages of the first n changes."""
    out = [None] * len(c)
    pos = sum(max(c[i] - c[i - 1], 0) for i in range(1, n + 1)) / n
    neg = sum(max(c[i - 1] - c[i], 0) for i in range(1, n + 1)) / n
    for i in range(n, len(c)):
        if i > n:
            d = c[i] - c[i - 1]
            pos = (pos * (n - 1) + max(d, 0)) / n
            neg = (neg * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + pos / neg) if neg else (100.0 if pos else 50.0)
    return out


def atr(h, l, c, n):
    """MT5 ATR: simple average of the true range over n bars."""
    tr = [0.0] + [max(h[i], c[i - 1]) - min(l[i], c[i - 1]) for i in range(1, len(c))]
    return [None if i < n else sum(tr[i - n + 1:i + 1]) / n for i in range(len(c))]


def check_signals(r):
    t_from, t_to = ts(TEST_FROM), ts(TEST_TO)
    expected, atr_at = {}, {}
    syms = sorted({x["symbol"] for x in r.deals})
    syms_all = sorted(set(syms) | {m.group(1) for m in (re.search(r"\[(\w+)\] spec:", line) for line in r.log) if m})
    for sym in syms_all:
        f = RES / "bars" / f"maea_bars_{sym}.csv"
        if not f.exists():
            print(f"\n[6] no bars for {sym}, run tools/export.py")
            return
        bars = list(csv.DictReader(open(f, encoding="ascii")))
        t = [int(b["time"]) for b in bars]
        o, h, l, c = ([float(b[k]) for b in bars] for k in ("open", "high", "low", "close"))
        ef, es, rs, at = ema(c, EMA_FAST), ema(c, EMA_SLOW), rsi(c, RSI_N), atr(h, l, c, ATR_N)
        for i in range(2, len(t)):
            if not (t_from <= t[i] < t_to) or rs[i - 2] is None:
                continue
            j = i - 1  # signal bar (closed), i = entry bar
            up = c[j] > es[j] and ef[j] > es[j]
            dn = c[j] < es[j] and ef[j] < es[j]
            if up and rs[j - 1] < RSI_BUY <= rs[j]:
                expected[(sym, t[i])] = ("buy", fmt(t[j]))
            elif dn and rs[j - 1] > RSI_SELL >= rs[j]:
                expected[(sym, t[i])] = ("sell", fmt(t[j]))
            atr_at[(sym, fmt(t[j]))] = at[j]
    ins = [x for x in r.deals if x["direction"] == "in"]
    match = wrong = 0
    no_signal = []
    for x in ins:
        e = expected.get((x["symbol"], x["t"] - x["t"] % H1))
        if e and e[0] == x["type"] and x["comment"] == "MAEA " + e[1]:
            match += 1
        elif e:
            wrong += 1
        else:
            no_signal.append(f"{x['symbol']} {x['time']}")
    # ATR the EA used vs ATR computed here, and SL distance = 1.5 ATR
    atr_bad = sl_bad = 0
    for e in r.entries:
        a = atr_at.get((e["symbol"], e["signal_bar"]))
        tick = float(e["tick_size"])
        if a is None or abs(float(e["atr"]) - a) > tick:
            atr_bad += 1
        if a is None or abs(abs(float(e["price"]) - float(e["sl"])) - SL_ATR * a) > 2 * tick:
            sl_bad += 1
    print(f"\n[6] signals (EMA {EMA_FAST}/{EMA_SLOW} trend + RSI {RSI_N} pullback, recomputed in Python from exported bars): "
          f"{len(expected)} signals, {len(ins)} entries; entries on the right bar, side and signal tag: {match}; wrong side or tag: {wrong}; "
          f"entries without a signal: {len(no_signal)} {no_signal[:5]} -> {ok(match == len(ins))}")
    print(f"    ATR used by the EA = ATR here (within 1 tick): {len(r.entries) - atr_bad} of {len(r.entries)}; "
          f"SL distance = {SL_ATR} ATR (within 2 ticks): {len(r.entries) - sl_bad} of {len(r.entries)}")
    # why the other signals were not traded: the EA's own log, cross-checked where the report allows it
    entered = {(x["symbol"], x["t"] - x["t"] % H1) for x in ins}
    reasons = Counter()
    text = defaultdict(list)
    for line in r.log:
        m = re.match(r"^\S+ \S+ \[(\w+)\] (BUY|SELL) signal (\S+ \S+) (waiting|skipped): (.*)$", line)
        if m:
            text[(m.group(1), m.group(3))].append(m.group(5))
    unexplained, per_sym = [], defaultdict(Counter)
    for (sym, t_i), (side, tag) in expected.items():
        if (sym, t_i) in entered:
            continue
        why = [w for w in text.get((sym, tag), []) if not w.startswith("entry window passed")]
        if why:
            reasons[re.split(r"[.:]| -?\d", why[0])[0]] += 1
            per_sym[sym][re.split(r"[.:]| -?\d", why[0])[0]] += 1
        elif text.get((sym, tag)):
            reasons["first seen after the entry window (market closed at the bar open)"] += 1
        else:
            unexplained.append(f"{sym} {tag}")
    print(f"    signals not traded: {sum(reasons.values()) + len(unexplained)}, by the EA's logged reason:")
    for k, v in reasons.most_common():
        print(f"      {v:4d}  {k}")
    print(f"      {len(unexplained):4d}  no log line (EA saw no signal there) {unexplained[:5]}")
    traded = {x["symbol"] for x in ins}
    for sym in sorted(set(syms_all) - traded):
        print(f"    {sym}: 0 entries; its signals were stopped by: {dict(per_sym[sym].most_common())}")


def main():
    runs = {n: Run(n) for n in RUNS if (RES / n).exists()}
    for r in runs.values():
        print(f"\n================ {r.name} {r.cfg}")
        results_table(r)
        check_duplicates(r)
        check_lots(r)
        check_limits(r)
        check_daily(r)
        if r.name == "A_main":
            check_signals(r)
        if r.cfg["be_trail"]:
            be = sum(1 for line in r.log if "] break-even: #" in line)
            tr = sum(1 for line in r.log if "] trailing: #" in line)
            locked = sum(1 for p in r.positions if p.get("exit", "").startswith("sl") and p["profit"] > 0)
            print(f"\n[7] break-even moves {be}, trailing moves {tr}; stop-outs with a locked-in profit: {locked}")
    if "A_main" in runs and "B_stateless" in runs:
        check_restart(runs["A_main"], runs["B_stateless"])


if __name__ == "__main__":
    main()
