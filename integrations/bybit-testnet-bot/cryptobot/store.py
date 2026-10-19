"""Bot state on disk: one SQLite file (standard library, no server).

Tables
  seen     signal ids already taken: the duplicate guard, survives restarts
  journal  one row per signal: what came in, what the bot decided, what the exchange did, the result
  state    small key/value flags: kill switch, daily anchor equity, daily halt

Autocommit: every write is on disk before the bot calls the exchange, so a crash at any point
leaves a trace that resync() can finish.
"""
import csv
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

JOURNAL_COLUMNS = ["id", "time", "signal_id", "source", "action", "symbol", "side", "order_type", "qty",
                   "entry", "sl", "tp", "risk_pct", "risk_cash", "leverage", "client_id", "order_id",
                   "status", "reason", "fill_price", "realized_pnl", "updated", "raw"]
# status values:
#   duplicate | rejected | error          final, nothing (more) on the exchange
#   sending                               order call in progress (after a crash resync() resolves it)
#   placed                                limit order resting on the exchange
#   filled                                position open
#   closed | cancelled | done             finished; done = close / close_all / cancel / kill executed
OPEN_STATUSES = ("sending", "placed", "filled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, time TEXT NOT NULL, signal_id TEXT, source TEXT,
  action TEXT, symbol TEXT, side TEXT, order_type TEXT, qty TEXT, entry TEXT, sl TEXT, tp TEXT,
  risk_pct TEXT, risk_cash TEXT, leverage TEXT, client_id TEXT, order_id TEXT, status TEXT NOT NULL,
  reason TEXT, fill_price TEXT, realized_pnl TEXT, updated TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS journal_status ON journal(status);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ---- duplicate guard ----------------------------------------------------------------------
    def claim(self, signal_id: str) -> bool:
        """True the first time an id is seen (and remembers it), False for a duplicate. Atomic."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO seen(id, ts) VALUES (?, ?)", (signal_id, self.clock()))
            return cur.rowcount == 1

    # ---- journal ------------------------------------------------------------------------------
    def add(self, **fields) -> int:
        now = self.clock()
        fields.setdefault("status", "received")
        if isinstance(fields.get("raw"), dict):
            fields["raw"] = json.dumps(fields["raw"], default=str)
        fields = {k: (None if v is None else str(v) if k != "raw" else v) for k, v in fields.items()}
        fields.update(ts=now, time=iso(now), updated=iso(now))
        keys = list(fields)
        with self.lock:
            cur = self.db.execute(f"INSERT INTO journal({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                                  [fields[k] for k in keys])
            return cur.lastrowid

    def update(self, row_id: int, **fields):
        fields = {k: (None if v is None else str(v)) for k, v in fields.items()}
        fields["updated"] = iso(self.clock())
        with self.lock:
            self.db.execute(f"UPDATE journal SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                            [*fields.values(), row_id])

    def row(self, row_id: int) -> dict:
        r = self.db.execute("SELECT * FROM journal WHERE id=?", (row_id,)).fetchone()
        return dict(r) if r else None

    def rows(self, status=None, limit=1000) -> list:
        if status:
            statuses = (status,) if isinstance(status, str) else tuple(status)
            q = f"SELECT * FROM journal WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id DESC LIMIT ?"
            args = (*statuses, limit)
        else:
            q, args = "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
        return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def find_client(self, client_id: str) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM journal WHERE client_id=?", (client_id,)).fetchall()]

    def export_csv(self, path) -> int:
        rows = list(reversed(self.rows(limit=10 ** 9)))
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return len(rows)

    # ---- flags --------------------------------------------------------------------------------
    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set(self, key, value):
        with self.lock:
            self.db.execute("INSERT INTO state(key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, None if value is None else str(value)))
