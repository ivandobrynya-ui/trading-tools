"""Runs Multi_Asset_EA in the MT5 Strategy Tester from the command line and collects the results.

The MT5 terminal must be closed first (a second instance will not start).
Usage:
  python run_tester.py <run name> <from yyyy.mm.dd> <to yyyy.mm.dd> [deposit=N] [Input=value ...]
Example:
  python run_tester.py main 2025.01.01 2026.10.16 deposit=100000 InpRiskPercent=0.5 InpMagic=261017
Results go to ./results/<run name>/: tester report (.htm + balance .png), the EA log and entries CSV.

Every input is written to the ini, defaults read from Multi_Asset_EA.mq5: the tester otherwise reuses the values
of the previous run (MQL5/Profiles/Tester/Multi_Asset_EA.set) for any input the ini leaves out.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TERMINAL = Path(r"C:\Program Files\MetaTrader 5\terminal64.exe")
DATA = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "D0E8209F77C8CF37AD8BF550E51FF075"
COMMON = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files"
HERE = Path(__file__).resolve().parent
TIMEFRAMES = {"PERIOD_CURRENT": 0, "PERIOD_M1": 1, "PERIOD_M5": 5, "PERIOD_M15": 15, "PERIOD_M30": 30,
              "PERIOD_H1": 16385, "PERIOD_H4": 16388, "PERIOD_D1": 16408}


def default_inputs():
    """Every `input` of the EA with its default, enums turned into their numbers."""
    src = (HERE / "Multi_Asset_EA.mq5").read_text(encoding="utf-8")
    enums = {m.group(1): m.group(2) for m in re.finditer(r"^\s*(\w+)\s*=\s*(-?\d+)\s*,?\s*//", src, re.M)}
    out = {}
    for m in re.finditer(r"^input\s+\w+\s+(\w+)\s*=\s*([^;]+);", src, re.M):
        name, val = m.group(1), m.group(2).strip()
        out[name] = val.strip('"') if val.startswith('"') else str(TIMEFRAMES.get(val, enums.get(val, val)))
    return out


def terminal_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"], capture_output=True, text=True).stdout
    return "terminal64.exe" in out


def main():
    name, date_from, date_to = sys.argv[1:4]
    overrides = dict(a.split("=", 1) for a in sys.argv[4:])
    deposit = overrides.pop("deposit", "10000")
    inputs = default_inputs()
    unknown = set(overrides) - set(inputs)
    if unknown:
        sys.exit(f"unknown inputs: {sorted(unknown)}")
    inputs.update(overrides)
    magic = inputs["InpMagic"]
    report = f"MAEA_{name}"

    if terminal_running():
        sys.exit("MT5 is running. Close it first: the tester needs the only instance.")

    ini = "\n".join([
        "[Tester]",
        r"Expert=Practice\Multi_Asset_EA.ex5",
        "Symbol=EURUSD",
        "Period=H1",
        "Model=1",            # 1 minute OHLC
        "ExecutionMode=0",
        "Optimization=0",
        f"FromDate={date_from}",
        f"ToDate={date_to}",
        "ForwardMode=0",
        f"Deposit={deposit}",
        "Currency=USD",
        "Leverage=100",
        "Visual=0",
        rf"Report=Tester\{report}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "[TesterInputs]",
        *[f"{k}={v}" for k, v in inputs.items()],
        "",
    ])
    cfg = DATA / "config" / f"maea_{name}.ini"
    cfg.write_text(ini, encoding="utf-16", newline="\r\n")  # "\r\n" in text mode would become "\r\r\n"

    t = time.time()
    subprocess.run([str(TERMINAL), f"/config:{cfg}"], check=False)
    while terminal_running():  # terminal64 may hand over to a child process and return early
        time.sleep(2)
    print(f"tester finished in {time.time() - t:.0f} s")

    out = HERE / "results" / name
    out.mkdir(parents=True, exist_ok=True)
    for f in (DATA / "Tester").glob(f"{report}*"):
        if f.suffix in (".htm", ".png"):
            shutil.copy2(f, out / f.name)
    for f in (f"MAEA_{magic}.log", f"MAEA_{magic}_entries.csv"):
        if (COMMON / f).exists():
            shutil.copy2(COMMON / f, out / f)
    print("results:", out, sorted(p.name for p in out.iterdir()))
    log = out / f"MAEA_{magic}.log"
    if log.exists():
        print(next((line for line in log.read_text(encoding="ascii", errors="replace").splitlines() if " Started: " in line), "no Started line"))


if __name__ == "__main__":
    main()
