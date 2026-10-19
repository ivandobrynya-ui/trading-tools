"""Command line. Run from the crypto_exchange_bot folder:

  python -m cryptobot check                          public Bybit testnet endpoints (+ keys, if set)
  python -m cryptobot serve --port 8090              webhook server for TradingView alerts
  python -m cryptobot signal --action buy --symbol BTCUSDT --sl-pct 1 --tp-r 2 --risk 0.5
  python -m cryptobot signal --action sell --symbol ETHUSDT --type limit --price 2550 --sl 2600
  python -m cryptobot signal --action close --symbol BTCUSDT
  python -m cryptobot resync                         rebuild state from the exchange after a restart
  python -m cryptobot status                         equity, daily loss, kill switch, positions, orders
  python -m cryptobot kill --reason "news"           emergency stop;  unkill  to allow entries again
  python -m cryptobot journal --limit 20 [--csv journal.csv]
  python -m cryptobot price BTCUSDT 59000            mock exchange only: move the market
"""
import argparse
import json
import logging
import sys
import time

from .config import load_settings, make_exchange
from .engine import Bot
from .signals import parse
from .store import Store


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def build(args):
    cfg = load_settings()
    ex, note = make_exchange(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stderr),
                                  logging.FileHandler(cfg.data_dir / "bot.log", encoding="utf-8")])
    logging.getLogger("cryptobot").info("exchange: %s", note)
    store = Store(cfg.data_dir / "bot.db")
    return cfg, ex, Bot(ex, store, cfg), note


def cmd_check(args):
    from .exchange.bybit import BybitV5
    cfg = load_settings()
    ex = BybitV5(cfg.bybit_api_key, cfg.bybit_api_secret, env=cfg.bybit_env)
    out = {"env": cfg.bybit_env, "clock_offset_ms": ex.sync_time()}
    for sym in args.symbols:
        inst = ex.instrument(sym)
        out[sym] = {"last_price": str(ex.last_price(sym)), "qty_step": str(inst.qty_step),
                    "min_qty": str(inst.min_qty), "tick": str(inst.tick_size),
                    "min_notional": str(inst.min_notional), "max_leverage": str(inst.max_leverage)}
    if cfg.has_keys:
        b = ex.balance()
        out["keys"] = "OK"
        out["equity"], out["available"] = str(b.equity), str(b.available)
        out["positions"] = [f"{p.symbol} {p.side} {p.qty}" for p in ex.positions()]
    else:
        out["keys"] = "not set (public endpoints only)"
    _print(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cryptobot", description="Practice crypto trading bot (testnet only)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="test the connection to Bybit testnet")
    p.add_argument("symbols", nargs="*", default=["BTCUSDT", "ETHUSDT"])

    p = sub.add_parser("serve", help="run the webhook server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--allow-ip", action="append", default=[], help="allowed source IP (repeatable)")
    p.add_argument("--trust-proxy", action="store_true", help="client IP from X-Forwarded-For (behind a tunnel)")

    p = sub.add_parser("signal", help="send one signal by hand (same checks as a webhook)")
    p.add_argument("--action", required=True, choices=["buy", "sell", "close", "close_all", "cancel"])
    p.add_argument("--symbol", default="")
    p.add_argument("--id", help="signal id (default: cli-<time>)")
    p.add_argument("--type", dest="order_type", default="market", choices=["market", "limit"])
    p.add_argument("--price")
    for name in ("sl", "sl-pct", "tp", "tp-pct", "tp-r", "risk", "qty", "leverage"):
        p.add_argument("--" + name)
    p.add_argument("--comment", default="")

    sub.add_parser("resync", help="rebuild state from the exchange")
    sub.add_parser("status", help="equity, limits, positions, orders")
    p = sub.add_parser("kill", help="emergency stop")
    p.add_argument("--reason", default="manual")
    sub.add_parser("unkill", help="reset the kill switch")
    p = sub.add_parser("journal", help="last journal rows")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--csv", help="export the whole journal to this CSV file")
    p = sub.add_parser("price", help="mock exchange only: set the price of a symbol")
    p.add_argument("symbol")
    p.add_argument("price")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)

    cfg, ex, bot, note = build(args)
    if args.cmd == "serve":
        from .server import serve
        _print({"exchange": note, "startup_resync": bot.start()})
        serve(bot, args.host, args.port, cfg.webhook_secret, args.allow_ip, args.trust_proxy, cfg.resync_seconds)
    elif args.cmd == "signal":
        payload = {k: v for k, v in vars(args).items()
                   if k in ("action", "symbol", "id", "order_type", "price", "sl", "sl_pct", "tp", "tp_pct", "tp_r",
                            "risk", "qty", "leverage", "comment") and v not in (None, "")}
        payload.setdefault("id", f"cli-{int(time.time() * 1000)}")
        try:
            sig = parse(payload, "cli", cfg.max_risk_pct, cfg.max_leverage)
        except ValueError as e:
            bot.reject_invalid(payload, str(e), "cli")
            raise SystemExit(f"invalid signal: {e}")
        bot.start()
        _print(bot.handle(sig))
    elif args.cmd == "resync":
        _print(bot.start())
    elif args.cmd == "status":
        _print({"exchange": note, **bot.status()})
    elif args.cmd == "kill":
        _print(bot.kill(args.reason))
    elif args.cmd == "unkill":
        _print(bot.unkill())
    elif args.cmd == "journal":
        if args.csv:
            print(f"{bot.store.export_csv(args.csv)} rows written to {args.csv}")
        else:
            for r in reversed(bot.store.rows(limit=args.limit)):
                print(f"#{r['id']} {r['time']} {r['source'] or '':7} {r['action'] or '':9} {r['symbol'] or '':8} "
                      f"{r['side'] or '':4} qty={r['qty'] or '-'} entry={r['entry'] or '-'} sl={r['sl'] or '-'} "
                      f"tp={r['tp'] or '-'} risk%={r['risk_pct'] or '-'} -> {r['status']}"
                      f"{' pnl=' + r['realized_pnl'] if r['realized_pnl'] else ''}"
                      f"{' | ' + r['reason'] if r['reason'] else ''}")
    elif args.cmd == "price":
        if ex.name != "mock":
            raise SystemExit("price works only with the mock exchange")
        ex.set_price(args.symbol.upper(), args.price)
        _print({"symbol": args.symbol.upper(), "price": args.price, **bot.status()})


def run():
    from .exchange.base import ExchangeError
    try:
        main()
    except ExchangeError as e:
        raise SystemExit(f"exchange error, nothing more was done: {type(e).__name__}: {e}")


if __name__ == "__main__":
    run()
