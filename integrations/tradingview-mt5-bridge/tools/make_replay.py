"""Writes a replay scenario for TV_Bridge_EA (Strategy Tester, InpReplay=true).

Each signal is a file in Common\\Files\\tvb_replay with the same format the receiver writes,
plus `time` (broker server time) so the EA fires it at that moment of the test.
Usage: python make_replay.py [common_files_dir]
"""
import calendar
import datetime as dt
import os
import sys
from pathlib import Path

base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files"
out = base / "tvb_replay"
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.sig"):
    old.unlink()

# (server time, id, action, extra fields, what it checks)
S = [
    ("2026-09-14 10:00", "s1", "buy", "sl_points=500;tp_points=1000;risk=1", "entry, 1% risk, SL/TP in points"),
    ("2026-09-14 10:00", "s1", "buy", "sl_points=500;tp_points=1000;risk=1", "same id again -> duplicate"),
    ("2026-09-14 10:01", "s3", "buy", "sl_points=500;risk=1", "second long -> max positions"),
    ("2026-09-14 10:02", "s6", "sell", "risk=1", "no stop loss -> rejected, the long must stay open"),
    ("2026-09-14 10:03", "s7", "sell", "sl=1;risk=1", "stop loss on the wrong side -> rejected, the long stays"),
    ("2026-09-14 10:04", "s4", "sell", "sl_points=500;tp_points=500;risk=1", "valid reverse: closes the long, opens a short"),
    ("2026-09-14 10:05", "s5", "close", "", "close by signal"),
    ("2026-09-14 10:06", "s8", "buy", "sl_points=500;risk=5", "risk 5% requested -> capped at 2%"),
    ("2026-09-14 10:07", "s9", "close_all", "", "close all"),
    # day 2: tight stops to hit the 1.5% daily loss limit, then entries are refused
    ("2026-09-15 10:00", "d1", "buy", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 10:30", "d2", "sell", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 11:00", "d3", "buy", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 11:30", "d4", "sell", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 12:00", "d5", "buy", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 12:30", "d6", "sell", "sl_points=60;risk=1", "tight stop"),
    ("2026-09-15 14:00", "d7", "buy", "sl_points=500;risk=1", "after the limit -> refused"),
    # day 3: new day resets the daily limit; then the kill switch
    ("2026-09-16 14:58", "k1", "buy", "sl_points=800;tp_points=800;risk=1", "new day -> allowed again (still open at the kill)"),
    ("2026-09-16 15:00", "k2", "kill", "", "emergency stop: closes and halts"),
    ("2026-09-16 15:30", "k3", "buy", "sl_points=500;risk=1", "halted -> refused"),
    ("2026-09-17 10:00", "k4", "buy", "sl_points=500;risk=1", "next day, still halted until manual reset"),
]

for n, (t, sid, action, extra, why) in enumerate(S):
    epoch = calendar.timegm(dt.datetime.strptime(t, "%Y-%m-%d %H:%M").timetuple())
    fields = [f"id={sid}", f"received={epoch}", "symbol=XAUUSD", f"action={action}", f"time={epoch}"]
    if extra:
        fields += extra.split(";")
    (out / f"{epoch:013d}_{n:03d}_{sid}.sig").write_text(";".join(fields) + "\n", encoding="ascii")
    print(f"{t}  {sid:3s} {action:9s} {extra:38s} # {why}")
print(f"{len(S)} signals -> {out}")
