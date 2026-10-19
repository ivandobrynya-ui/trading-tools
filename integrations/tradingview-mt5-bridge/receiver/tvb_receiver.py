"""TradingView -> MetaTrader 5 bridge: webhook receiver.

Receives TradingView alert webhooks (JSON body), checks the shared secret, validates the
signal, drops duplicates by id and writes each accepted signal as its own small file into
the MT5 common folder (Common\\Files\\tvb_inbox). The MT5 executor EA (TV_Bridge_EA.mq5)
picks the files up and trades them.

Standard library only: python tvb_receiver.py --port 8080
The secret comes from the TVB_SECRET environment variable (never from code or logs).

Endpoints
  POST /webhook  TradingView alert. JSON fields:
                 secret (str, required), action (buy|sell|close|close_all, required),
                 symbol (str, required; "OANDA:XAUUSD" -> "XAUUSD"),
                 id (str) or time (bar time) - used for duplicate protection,
                 sl / tp (price), sl_points / tp_points, risk (% of equity), lots, comment
  POST /kill     {"secret": "..."} - emergency stop: the EA closes its positions and halts
  GET  /health   liveness check
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ACTIONS = {"buy", "sell", "close", "close_all"}
SYMBOL_RE = re.compile(r"^[A-Za-z0-9._!\-]{1,32}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
MAX_BODY = 4096
MAX_RISK = 5.0

log = logging.getLogger("tvb")


def default_common_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def _num(payload, key, positive=True):
    v = payload.get(key)
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError(f"{key} must be a finite number")
    if positive and f <= 0:
        raise ValueError(f"{key} must be positive")
    return f


def validate(payload, secret: str, now: float):
    """Returns a normalized signal dict. Raises PermissionError (bad secret) or ValueError (bad signal)."""
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    given = str(payload.get("secret", ""))
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        raise PermissionError("bad secret")

    action = str(payload.get("action", "")).strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")

    symbol = str(payload.get("symbol", "")).strip()
    if ":" in symbol:  # TradingView {{ticker}} may come as EXCHANGE:SYMBOL
        symbol = symbol.split(":", 1)[1]
    if action != "close_all" and not SYMBOL_RE.match(symbol):
        raise ValueError("symbol is missing or invalid")

    sig_id = str(payload.get("id", "")).strip()
    if sig_id:
        if not ID_RE.match(sig_id):
            raise ValueError("id may contain only letters, digits and . _ : - (max 64)")
    else:
        bar_time = str(payload.get("time", "")).strip()
        if not bar_time:
            raise ValueError("send an id or the bar time ({{time}}) for duplicate protection")
        raw = f"{symbol}|{action}|{bar_time}".encode()
        sig_id = "h" + hashlib.sha1(raw).hexdigest()[:16]

    sig = {
        "id": sig_id,
        "received": f"{now:.3f}",
        "symbol": symbol,
        "action": action,
    }
    for key in ("sl", "tp", "sl_points", "tp_points", "lots"):
        v = _num(payload, key)
        if v is not None:
            sig[key] = repr(v)
    risk = _num(payload, "risk")
    if risk is not None:
        if risk > MAX_RISK:
            raise ValueError(f"risk above {MAX_RISK}% is refused")
        sig["risk"] = repr(risk)
    if "sl" in sig and "sl_points" in sig:
        raise ValueError("send either sl or sl_points, not both")
    if "tp" in sig and "tp_points" in sig:
        raise ValueError("send either tp or tp_points, not both")
    comment = re.sub(r"[^A-Za-z0-9 ._\-]", "", str(payload.get("comment", "")))[:24]
    if comment:
        sig["comment"] = comment
    return sig


class SeenIds:
    """Ids already accepted, persisted to a file so a restart does not forget them."""

    def __init__(self, path: Path, keep: int = 20000):
        self.path = path
        self.keep = keep
        self.lock = threading.Lock()
        self.ids = []
        self.set = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-keep:]:
                if line:
                    self.ids.append(line)
                    self.set.add(line)

    def add(self, sig_id: str) -> bool:
        """True if new (and remembers it), False if it is a duplicate."""
        with self.lock:
            if sig_id in self.set:
                return False
            self.ids.append(sig_id)
            self.set.add(sig_id)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(sig_id + "\n")
            if len(self.ids) > self.keep * 2:  # compact the file now and then
                self.ids = self.ids[-self.keep:]
                self.set = set(self.ids)
                self.path.write_text("\n".join(self.ids) + "\n", encoding="utf-8")
            return True


class Bridge:
    def __init__(self, base: Path, secret: str):
        self.base = base
        self.inbox = base / "tvb_inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.seen = SeenIds(base / "tvb_seen_ids.txt")
        self.seq = 0
        self.lock = threading.Lock()

    def write_signal(self, sig: dict) -> Path:
        """Atomic: write to a temp name, then rename, so the EA never reads half a file."""
        with self.lock:
            self.seq = (self.seq + 1) % 1000000
            stamp = int(float(sig["received"]) * 1000)
            name = f"{stamp:013d}_{self.seq:06d}_{hashlib.sha1(sig['id'].encode()).hexdigest()[:8]}.sig"
        line = ";".join(f"{k}={v}" for k, v in sig.items())
        tmp = self.inbox / (name + ".tmp")
        with open(tmp, "w", encoding="ascii", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        final = self.inbox / name
        os.replace(tmp, final)
        return final

    def handle_webhook(self, payload, now=None):
        now = time.time() if now is None else now
        sig = validate(payload, self.secret, now)
        if not self.seen.add(sig["id"]):
            return 200, {"status": "duplicate", "id": sig["id"]}
        self.write_signal(sig)
        return 200, {"status": "queued", "id": sig["id"]}

    def handle_kill(self, payload):
        given = str(payload.get("secret", "")) if isinstance(payload, dict) else ""
        if not hmac.compare_digest(given.encode(), self.secret.encode()):
            raise PermissionError("bad secret")
        flag = self.base / "tvb_KILL.flag"
        flag.write_text(f"kill requested at {time.time():.0f}\n", encoding="ascii")
        return 200, {"status": "kill switch set"}


def make_handler(bridge: Bridge, allowed_ips: set, trust_proxy: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tvb/1.0"

        def _client_ip(self):
            if trust_proxy:
                fwd = self.headers.get("X-Forwarded-For", "") or self.headers.get("CF-Connecting-IP", "")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def _reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # route http.server logs through logging
            log.debug("%s %s", self._client_ip(), fmt % args)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, {"ok": True})
            return self._reply(404, {"error": "not found"})

        def do_POST(self):
            ip = self._client_ip()
            if allowed_ips and ip not in allowed_ips:
                log.warning("refused %s: ip not allowed", ip)
                return self._reply(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return self._reply(413 if length > MAX_BODY else 400, {"error": "bad body size"})
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                log.warning("refused %s: body is not JSON", ip)
                return self._reply(400, {"error": "body must be JSON"})
            try:
                if self.path == "/webhook":
                    code, body = bridge.handle_webhook(payload)
                elif self.path == "/kill":
                    code, body = bridge.handle_kill(payload)
                    log.warning("KILL switch set by %s", ip)
                else:
                    return self._reply(404, {"error": "not found"})
            except PermissionError:
                log.warning("refused %s: bad secret", ip)
                return self._reply(403, {"error": "forbidden"})
            except ValueError as e:
                log.warning("refused %s: %s", ip, e)
                return self._reply(400, {"error": str(e)})
            log.info("%s %s -> %s", ip, self.path, body)
            return self._reply(code, body)

    return Handler


def main():
    ap = argparse.ArgumentParser(description="TradingView -> MT5 webhook receiver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default=str(default_common_dir()), help="MT5 Common\\Files folder")
    ap.add_argument("--allow-ip", action="append", default=[], help="allowed source IP (repeatable)")
    ap.add_argument("--trust-proxy", action="store_true", help="take client IP from X-Forwarded-For (behind a tunnel)")
    args = ap.parse_args()

    secret = os.environ.get("TVB_SECRET", "")
    if len(secret) < 16:
        raise SystemExit("Set TVB_SECRET (at least 16 characters) before starting the receiver.")
    base = Path(args.dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(base / "tvb_receiver.log", encoding="utf-8")])
    bridge = Bridge(base, secret)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge, set(args.allow_ip), args.trust_proxy))
    log.info("listening on %s:%d, inbox %s", args.host, args.port, bridge.inbox)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
