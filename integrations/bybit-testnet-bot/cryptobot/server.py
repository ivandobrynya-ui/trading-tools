"""Webhook server for TradingView alerts (standard library only).

  POST /webhook  TradingView alert JSON, must contain "secret" (see signals.py for the fields)
  POST /kill     {"secret": "..."}: emergency stop (cancel orders, close positions, block entries)
  GET  /health   liveness check, no data

The secret is compared in constant time and never logged. Signals are executed one at a time.
A background thread runs Bot.tick() every RESYNC_SECONDS: reconnect after an outage, resync with
the exchange, daily loss check (so the daily anchor is set near 00:00 UTC even without signals).
"""
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .signals import parse

log = logging.getLogger("cryptobot.server")
MAX_BODY = 4096


def check_secret(payload, secret: str) -> bool:
    given = str(payload.get("secret", "")) if isinstance(payload, dict) else ""
    return bool(secret) and hmac.compare_digest(given.encode(), secret.encode())


def handle_payload(bot, path: str, payload, secret: str):
    """(http code, body). Separate from the HTTP class so tests can call it directly."""
    if not check_secret(payload, secret):
        return 403, {"error": "forbidden"}
    if path == "/kill":
        return 200, bot.kill("webhook /kill")
    try:
        sig = parse(payload, "webhook", bot.cfg.max_risk_pct, bot.cfg.max_leverage)
    except ValueError as e:
        bot.reject_invalid(payload, str(e), "webhook")
        return 400, {"error": str(e)}
    return 200, bot.handle(sig)


def make_handler(bot, secret: str, allowed_ips: set, trust_proxy: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cryptobot/1.0"

        def _client_ip(self):
            if trust_proxy:
                fwd = self.headers.get("X-Forwarded-For", "") or self.headers.get("CF-Connecting-IP", "")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def _reply(self, code, body):
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
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
            if self.path not in ("/webhook", "/kill"):
                return self._reply(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return self._reply(413 if length > MAX_BODY else 400, {"error": "bad body size"})
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._reply(400, {"error": "body must be JSON"})
            try:
                code, body = handle_payload(bot, self.path, payload, secret)
            except Exception as e:  # never let one bad request kill the server
                log.exception("unexpected error")
                return self._reply(500, {"error": type(e).__name__})
            if code == 403:
                log.warning("refused %s: bad secret", ip)
            else:
                log.info("%s %s -> %s", ip, self.path, body)
            return self._reply(code, body)

    return Handler


def serve(bot, host: str, port: int, secret: str, allowed_ips=(), trust_proxy=False, tick_seconds=60):
    if len(secret) < 16:
        raise SystemExit("Set WEBHOOK_SECRET (at least 16 characters) in .env before starting the server.")
    stop = threading.Event()

    def maintenance():
        while not stop.wait(tick_seconds):
            bot.tick()

    threading.Thread(target=maintenance, daemon=True, name="tick").start()
    server = ThreadingHTTPServer((host, port), make_handler(bot, secret, set(allowed_ips), trust_proxy))
    log.info("listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
