"""Prepares (or cleans up) the live-inbox test for TV_Bridge_EA in the Strategy Tester.

  python inbox_test.py run1    fresh signal + stale signal + duplicate id
  python inbox_test.py run2    the same signal again, as if resent after a restart
  python inbox_test.py clean   remove test files, journal and processed-id list
"""
import calendar
import datetime as dt
import os
import sys
from pathlib import Path

base = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files"
inbox = base / "tvb_inbox"
fresh = calendar.timegm(dt.datetime(2026, 9, 14, 10, 0).timetuple())


def put(name, fields):
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(";".join(fields) + "\n", encoding="ascii")


mode = sys.argv[1]
if mode == "run1":
    put("1789980000000_000001_a.sig", ["id=live1", f"received={fresh}", "symbol=XAUUSD", "action=buy", "sl_points=500", "risk=1"])
    put("1789980000000_000002_b.sig", ["id=live2", "received=1000000000", "symbol=XAUUSD", "action=buy", "sl_points=500", "risk=1"])
    put("1789980000000_000003_c.sig", ["id=live1", f"received={fresh}", "symbol=XAUUSD", "action=buy", "sl_points=500", "risk=1"])
elif mode == "run2":
    put("1789980000000_000004_d.sig", ["id=live1", f"received={fresh}", "symbol=XAUUSD", "action=buy", "sl_points=500", "risk=1"])
elif mode == "clean":
    for folder in ("tvb_inbox", "tvb_done", "tvb_rejected"):
        for f in (base / folder).glob("1789980000000_*.sig"):
            os.remove(f)
    for name in ("tvb_processed_777001.txt", "tvb_journal_777001.csv"):
        if (base / name).exists():
            os.remove(base / name)
for folder in ("tvb_inbox", "tvb_done", "tvb_rejected"):
    names = sorted(p.name for p in (base / folder).glob("*.sig")) if (base / folder).exists() else []
    print(f"{folder}: {names}")
