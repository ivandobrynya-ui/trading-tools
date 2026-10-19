"""Runs the MAEA_Export script in MT5 without the GUI: server symbol list + bars of the given symbols.

The MT5 terminal must be closed first. The script must be compiled into MQL5\\Scripts\\Practice\\MAEA_Export.ex5.
Usage:
  python export.py                         # symbol list only
  python export.py EURUSD,GBPUSD,XAUUSD    # symbol list + H1 bars 2023.06.01 .. 2026.10.17
Files go to ../results/bars/.
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TERMINAL = Path(r"C:\Program Files\MetaTrader 5\terminal64.exe")
DATA = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "D0E8209F77C8CF37AD8BF550E51FF075"
COMMON = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files"
OUT = Path(__file__).resolve().parent.parent / "results" / "bars"


def running():
    return "terminal64.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"], capture_output=True, text=True).stdout


def unix(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


def main():
    symbols = sys.argv[1] if len(sys.argv) > 1 else ""
    if running():
        sys.exit("MT5 is running. Close it first.")
    preset = DATA / "MQL5" / "Presets" / "maea_export.set"
    # "\n" + newline="\r\n": writing "\r\n" in text mode would give "\r\r\n" and a stray "\r" in every value
    preset.write_text("\n".join([f"InpSymbols={symbols}", "InpTf=16385", f"InpFrom={unix(2023, 6, 1)}",
                                 f"InpTo={unix(2026, 10, 17)}", "InpPrefix=maea_", "InpCloseTerm=true", ""]),
                      encoding="utf-16", newline="\r\n")
    ini = DATA / "config" / "maea_export.ini"
    ini.write_text("\n".join(["[StartUp]", r"Script=Practice\MAEA_Export", "ScriptParameters=maea_export.set",
                              "Symbol=EURUSD", "Period=H1", ""]), encoding="utf-16", newline="\r\n")
    subprocess.run([str(TERMINAL), f"/config:{ini}"], check=False)
    while running():
        time.sleep(2)
    OUT.mkdir(parents=True, exist_ok=True)
    for f in COMMON.iterdir():
        if f.name.startswith("maea_") and f.suffix == ".csv":  # case-sensitive: the EA's own files are MAEA_*
            shutil.copy2(f, OUT / f.name)
    print(sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
