"""The bot: signal -> safety checks -> size -> orders -> journal. Exchange-agnostic.

Rules this file enforces:
  - the exchange is the source of truth: before every decision the bot reads positions, orders
    and balance from the exchange, not from memory;
  - check first, act second: a signal that fails any check changes nothing on the account;
  - one signal id = at most one order: the id is claimed on disk before anything is sent, and
    every order carries a client order id derived from it, so a retry after a timeout cannot
    create a second order;
  - no position without a stop: if the exchange did not attach the SL, the bot sets it again,
    and if that fails too, it closes the position;
  - the daily loss limit and the kill switch block new entries; closing is always allowed.
"""
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

from .exchange.base import DuplicateOrder, ExchangeError, NetworkError, OrderNotFound, RateLimitError
from .signals import Signal
from .sizing import SizingError, round_price, size_by_risk, size_fixed
from .store import OPEN_STATUSES, Store

log = logging.getLogger("cryptobot")
PREFIX = "cb"  # client order ids of this bot start with it; other orders on the account are not touched
D = Decimal


class Rejected(Exception):
    """The signal is refused by a check. Nothing was sent to the exchange."""


def client_id(signal_id: str, kind: str) -> str:
    """Exchange client order id: 'cb' + 16 hex of the signal id + kind (E entry, C close, R reverse).
    19 characters, fits Bybit/Binance/OKX limits (36/36/32) and their allowed characters."""
    return PREFIX + hashlib.sha1(signal_id.encode()).hexdigest()[:16] + kind


class Bot:
    def __init__(self, exchange, store: Store, cfg, clock=time.time, sleep=time.sleep):
        self.ex = exchange
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.RLock()  # one signal at a time
        self._leverage = {}
        self._need_connect = True

    # ==== entry points ===========================================================================
    def start(self, attempts: int = 5) -> dict:
        """Connect (with backoff) and rebuild state from the exchange. Call after every restart."""
        for i in range(1, attempts + 1):
            try:
                self.ex.connect()
                self._need_connect = False
                break
            except (NetworkError, RateLimitError) as e:
                if i == attempts:
                    raise
                wait = min(2 ** i, 30)
                log.warning("connect failed (%s), retry in %ss", e, wait)
                self.sleep(wait)
        return self.resync()

    def handle(self, sig: Signal) -> dict:
        with self.lock:
            base = dict(signal_id=sig.id, source=sig.source, action=sig.action, symbol=sig.symbol, raw=sig.raw)
            if not self.store.claim(sig.id):
                self.store.add(**base, status="duplicate", reason="signal id already processed")
                log.info("duplicate signal %s ignored", sig.id)
                return {"status": "duplicate", "id": sig.id}
            row = self.store.add(**base, status="received")
            try:
                if sig.is_entry:
                    res = self._enter(sig, row)
                elif sig.action == "close":
                    res = self._close_symbol(sig.symbol, row, f"close signal {sig.id}")
                elif sig.action == "close_all":
                    res = self._flatten(f"close_all signal {sig.id}", row)
                else:
                    res = self._cancel_symbol(sig.symbol, row, f"cancel signal {sig.id}")
            except Rejected as e:
                self.store.update(row, status="rejected", reason=str(e))
                log.info("signal %s rejected: %s", sig.id, e)
                res = {"status": "rejected", "reason": str(e)}
            except ExchangeError as e:
                reason = f"{type(e).__name__}: {e}"
                if self.store.row(row)["status"] == "sending":
                    # the order may or may not exist; resync() looks it up by client id
                    self.store.update(row, reason=f"result unknown ({reason}); resync will check")
                else:
                    self.store.update(row, status="error", reason=reason)
                log.error("signal %s: %s", sig.id, reason)
                res = {"status": "error", "reason": reason}
            res.update(id=sig.id, row=row)
            return res

    def reject_invalid(self, payload, reason: str, source: str):
        """Journal a signal that failed validation (it never reaches handle())."""
        payload = payload if isinstance(payload, dict) else {"body": str(payload)[:200]}
        raw = {k: v for k, v in payload.items() if k != "secret"}
        self.store.add(signal_id=str(raw.get("id", ""))[:64], source=source, action=str(raw.get("action", ""))[:16],
                       symbol=str(raw.get("symbol", ""))[:32], status="rejected", reason=f"invalid: {reason}", raw=raw)

    def kill(self, reason: str = "manual") -> dict:
        """Emergency stop: block entries, cancel this bot's orders, close all positions."""
        with self.lock:
            self.store.set("kill", reason or "manual")
            row = self.store.add(action="kill", source="kill", status="received", reason=reason)
            log.warning("KILL SWITCH: %s", reason)
            return self._flatten(f"kill switch: {reason}", row)

    def unkill(self) -> dict:
        with self.lock:
            self.store.set("kill", "")
            self.store.add(action="unkill", source="cli", status="done", reason="kill switch reset by hand")
            return {"status": "kill switch off"}

    def tick(self) -> dict:
        """Periodic job of the server: reconnect if needed, resync, check the daily limit."""
        with self.lock:
            try:
                if self._need_connect:
                    self.ex.connect()
                    self._need_connect = False
                report = self.resync()
                self._daily_check(self.ex.balance().equity)
                return report
            except ExchangeError as e:
                self._need_connect = isinstance(e, (NetworkError, RateLimitError))
                log.warning("tick failed: %s: %s", type(e).__name__, e)
                return {"error": f"{type(e).__name__}: {e}"}

    # ==== entries ================================================================================
    def _enter(self, sig: Signal, row: int) -> dict:
        cfg = self.cfg
        if self.store.get("kill"):
            raise Rejected(f"kill switch is on ({self.store.get('kill')}); reset it by hand")
        if cfg.symbols and sig.symbol not in cfg.symbols:
            raise Rejected(f"{sig.symbol} is not in SYMBOLS")
        bal = self.ex.balance()
        ok, loss = self._daily_check(bal.equity)
        if not ok:
            raise Rejected(f"daily loss limit hit: {loss:.2f}% today, limit {cfg.daily_loss_pct}%")

        inst = self.ex.instrument(sig.symbol)
        side = "buy" if sig.action == "buy" else "sell"
        long = side == "buy"
        sign = 1 if long else -1
        entry = round_price(sig.price, inst.tick_size) if sig.order_type == "limit" else self.ex.last_price(sig.symbol)

        sl = sig.sl
        sl_pct = sig.sl_pct or (cfg.default_sl_pct if cfg.default_sl_pct > 0 else None)
        if sl is None and sl_pct is not None:
            sl = entry * (1 - sign * sl_pct / 100)
        if sl is None:
            raise Rejected("no stop loss: send sl or sl_pct, or set DEFAULT_SL_PCT")
        sl = round_price(sl, inst.tick_size)
        if (sl - entry) * sign >= 0:
            raise Rejected(f"stop loss {sl} is on the wrong side of entry {entry}")
        tp = sig.tp
        if tp is None and sig.tp_pct is not None:
            tp = entry * (1 + sign * sig.tp_pct / 100)
        tp_r = sig.tp_r or (cfg.default_tp_r if cfg.default_tp_r > 0 else None)
        if tp is None and tp_r is not None:
            tp = entry + sign * tp_r * abs(entry - sl)
        if tp is not None:
            tp = round_price(tp, inst.tick_size)
            if (tp - entry) * sign <= 0:
                raise Rejected(f"take profit {tp} is on the wrong side of entry {entry}")
        lev = min(sig.leverage or cfg.leverage, cfg.max_leverage, inst.max_leverage)

        pos = next(iter(self.ex.positions(sig.symbol)), None)
        want = "long" if long else "short"
        if pos and pos.side == want:
            raise Rejected(f"already {pos.side} {pos.qty} {sig.symbol}; one position per symbol")
        if pos and not cfg.reverse:
            raise Rejected(f"{pos.side} position open on {sig.symbol} and REVERSE is off")
        size = self._size(sig, bal, entry, sl, inst, lev, None if pos else bal.available)

        # ---- every check passed: from here on the bot acts --------------------------------------
        cid = client_id(sig.id, "E")
        self._cancel_pending(sig.symbol, f"replaced by signal {sig.id}")
        if pos:
            self._close_position(pos, client_id(sig.id, "R"), f"reversed by signal {sig.id}")
            bal = self.ex.balance()
            try:
                size = self._size(sig, bal, entry, sl, inst, lev, bal.available)
            except Rejected as e:
                raise Rejected(f"{pos.side} position closed (reverse), new entry refused: {e}") from None
        self._set_leverage(sig.symbol, lev)
        self.store.update(row, side=side, order_type=sig.order_type, qty=size.qty, entry=entry, sl=sl, tp=tp,
                          risk_pct=round(size.risk_pct, 3), risk_cash=round(size.risk_cash, 2), leverage=lev,
                          client_id=cid, status="sending",
                          reason=f"size capped by {size.capped_by}" if size.capped_by else None)
        order = self._place(sig.symbol, side, sig.order_type, size.qty, cid,
                            price=entry if sig.order_type == "limit" else None, sl=sl, tp=tp)
        order = self._await_fill(order)
        if order.status == "filled":
            self.store.update(row, status="filled", order_id=order.order_id, fill_price=order.avg_price)
            self._ensure_stops(sig.symbol, sl, tp, row)
        elif order.status == "open":
            self.store.update(row, status="placed", order_id=order.order_id)
        else:
            self.store.update(row, status="error", order_id=order.order_id, reason=f"order {order.status} by the exchange")
        res = self.store.row(row)
        log.info("signal %s: %s %s %s qty %s entry %s sl %s tp %s risk %s%% -> %s", sig.id, side, sig.order_type,
                 sig.symbol, size.qty, entry, sl, tp, res["risk_pct"], res["status"])
        return {"status": res["status"], "side": side, "qty": str(size.qty), "entry": str(entry), "sl": str(sl),
                "tp": None if tp is None else str(tp), "risk_pct": res["risk_pct"], "leverage": str(lev)}

    def _size(self, sig, bal, entry, sl, inst, lev, available):
        kw = dict(entry=entry, stop=sl, inst=inst, leverage=lev, available=available, fee_rate=self.cfg.fee_rate,
                  max_notional=self.cfg.max_notional or None, order_type=sig.order_type)
        try:
            if sig.qty is not None:
                return size_fixed(sig.qty, self.cfg.max_risk_pct, bal.equity, **kw)
            return size_by_risk(bal.equity, sig.risk or self.cfg.risk_pct, **kw)
        except SizingError as e:
            raise Rejected(str(e)) from None

    def _set_leverage(self, symbol, lev):
        if self._leverage.get(symbol) != lev:
            self.ex.set_leverage(symbol, lev)
            self._leverage[symbol] = lev

    def _place(self, symbol, side, type, qty, cid, **kw):
        """Send an order so that a lost answer never turns into two orders.
        The client id is unique per signal: if the exchange got the first request, a second one is
        refused as a duplicate, and the bot reads the existing order instead."""
        for attempt in range(1, 4):
            try:
                return self.ex.place_order(symbol, side, type, qty, cid, **kw)
            except DuplicateOrder:
                existing = self.ex.get_order(symbol, cid)
                if existing:
                    return existing
                raise
            except (NetworkError, RateLimitError) as e:
                log.warning("order %s: %s, checking whether it reached the exchange", cid, e)
                self.sleep(min(0.5 * 2 ** attempt, 5))
                try:
                    existing = self.ex.get_order(symbol, cid)
                except (NetworkError, RateLimitError):
                    existing = None  # sending again is still safe: same client id
                if existing:
                    return existing
                if attempt == 3:
                    raise

    def _await_fill(self, order, polls: int = 10):
        """Bybit confirms orders asynchronously: poll a market order until it is filled."""
        if order.type != "market" or order.status != "open":
            return order
        for _ in range(polls):
            self.sleep(0.3)
            o = self.ex.get_order(order.symbol, order.client_id)
            if o and o.status != "open":
                return o
        return order

    def _ensure_stops(self, symbol, sl, tp, row):
        pos = next(iter(self.ex.positions(symbol)), None)
        if not pos or pos.sl is not None:
            return
        log.warning("%s position has no stop loss, setting it again", symbol)
        try:
            self.ex.set_position_stops(symbol, sl, tp)
            self.store.update(row, reason="SL/TP were missing after the fill and were set again")
        except ExchangeError as e:
            log.error("could not set SL on %s (%s): closing the position", symbol, e)
            self._close_position(pos, client_id(f"{row}-nosl", "C"), f"SL could not be set ({e})")

    # ==== exits ==================================================================================
    def _close_position(self, pos, cid, why):
        side = "sell" if pos.side == "long" else "buy"
        order = self._await_fill(self._place(pos.symbol, side, "market", pos.qty, cid, reduce_only=True))
        if order.status != "filled":
            raise ExchangeError(f"close order for {pos.symbol} is {order.status}")
        self._mark_closed(pos.symbol, why)
        return order

    def _mark_closed(self, symbol, why):
        for r in self.store.rows("filled"):
            if r["symbol"] == symbol:
                pnl = self.ex.closed_pnl(symbol, int(r_ts(r) * 1000))
                self.store.update(r["id"], status="closed", realized_pnl=round(pnl, 4), reason=why)

    def _cancel_pending(self, symbol, why) -> int:
        n = 0
        for o in self.ex.open_orders(symbol):
            if o.client_id.startswith(PREFIX) and not o.reduce_only:
                try:
                    self.ex.cancel_order(o.symbol, o.client_id)
                except OrderNotFound:
                    pass  # filled or cancelled in the meantime; resync will see it
                for r in self.store.find_client(o.client_id):
                    if r["status"] in ("placed", "sending"):
                        self.store.update(r["id"], status="cancelled", reason=why)
                n += 1
        return n

    def _close_symbol(self, symbol, row, why) -> dict:
        cancelled = self._cancel_pending(symbol, why)
        pos = next(iter(self.ex.positions(symbol)), None)
        if pos:
            cid = client_id(f"{row}-{symbol}", "C")
            self.store.update(row, side=pos.side, qty=pos.qty, client_id=cid)
            self._close_position(pos, cid, why)
        msg = f"closed {pos.side} {pos.qty}" if pos else "no position"
        self.store.update(row, status="done", reason=f"{msg}, {cancelled} pending order(s) cancelled")
        return {"status": "done", "closed": bool(pos), "cancelled": cancelled}

    def _cancel_symbol(self, symbol, row, why) -> dict:
        n = self._cancel_pending(symbol, why)
        self.store.update(row, status="done", reason=f"{n} pending order(s) cancelled")
        return {"status": "done", "cancelled": n}

    def _flatten(self, why, row) -> dict:
        """Cancel all orders of this bot and close every position. Keeps going after an error."""
        errors, closed, cancelled = [], 0, 0
        for o in self.ex.open_orders():
            if o.client_id.startswith(PREFIX):
                try:
                    self.ex.cancel_order(o.symbol, o.client_id)
                    cancelled += 1
                    for r in self.store.find_client(o.client_id):
                        if r["status"] in ("placed", "sending"):
                            self.store.update(r["id"], status="cancelled", reason=why)
                except OrderNotFound:
                    pass
                except ExchangeError as e:
                    errors.append(f"cancel {o.client_id}: {e}")
        for pos in self.ex.positions():
            try:
                self._close_position(pos, client_id(f"{row}-{pos.symbol}", "C"), why)
                closed += 1
            except ExchangeError as e:
                errors.append(f"close {pos.symbol}: {e}")
        reason = f"{why}: {closed} position(s) closed, {cancelled} order(s) cancelled"
        if errors:
            reason += "; ERRORS: " + "; ".join(errors)
        self.store.update(row, status="error" if errors else "done", reason=reason)
        return {"status": "error" if errors else "done", "closed": closed, "cancelled": cancelled, "errors": errors}

    # ==== daily loss limit =======================================================================
    def _daily_check(self, equity: Decimal):
        """(allowed, loss % today). The anchor is the equity seen first after 00:00 UTC, stored on
        disk, so a restart in the middle of the day does not reset the limit."""
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        if self.store.get("day") != day:
            self.store.set("day", day)
            self.store.set("day_start_equity", equity)
        start = D(self.store.get("day_start_equity"))
        loss = (start - equity) / start * 100 if start > 0 else D(0)
        if self.store.get("halted_day") == day:
            return False, loss
        if self.cfg.daily_loss_pct > 0 and loss >= self.cfg.daily_loss_pct:
            self.store.set("halted_day", day)
            log.warning("daily loss limit hit: %.2f%% (limit %s%%)", loss, self.cfg.daily_loss_pct)
            row = self.store.add(action="daily_limit", source="bot", status="received",
                                 reason=f"loss {loss:.2f}% today")
            if self.cfg.flatten_on_daily_limit:
                self._flatten(f"daily loss limit {loss:.2f}%", row)
            else:
                self.store.update(row, status="done", reason=f"loss {loss:.2f}% today, entries blocked")
            return False, loss
        return True, loss

    # ==== restart recovery =======================================================================
    def resync(self) -> dict:
        """Rebuild the picture from the exchange after a restart (or any time):
          - an order call that was in flight when the bot stopped is looked up by its client id;
          - limit orders that filled or were cancelled while the bot was down are updated;
          - positions closed by SL/TP while the bot was down get their realized P/L;
          - an open position that lost its stop gets it back from the journal;
          - orders and positions the journal does not know are reported, not touched."""
        with self.lock:
            positions = {p.symbol: p for p in self.ex.positions()}
            bot_orders = {o.client_id: o for o in self.ex.open_orders() if o.client_id.startswith(PREFIX)}
            report = {"positions": len(positions), "bot_orders": len(bot_orders), "updated": [],
                      "protected": [], "unknown_orders": [], "unknown_positions": []}
            # a row still "received" here belongs to a process that died before it sent anything
            # (signals and resync share the lock, so no signal of this process is half done)
            for r in self.store.rows("received"):
                self.store.update(r["id"], status="error", reason="bot stopped before this was processed")
                report["updated"].append(f"#{r['id']} {r['symbol'] or r['action']}: received -> error")
            known_clients = set()
            for r in reversed(self.store.rows(OPEN_STATUSES)):
                cid, sym = r["client_id"], r["symbol"]
                known_clients.add(cid)
                status = r["status"]
                if status in ("sending", "placed"):
                    o = bot_orders.get(cid) or self.ex.get_order(sym, cid)
                    if o is None:
                        new = ("error", "order never reached the exchange (bot stopped before sending)") \
                            if status == "sending" else ("cancelled", "order is no longer on the exchange")
                        self.store.update(r["id"], status=new[0], reason=new[1])
                        report["updated"].append(f"#{r['id']} {sym}: {status} -> {new[0]}")
                        continue
                    if o.status == "open":
                        if status == "sending":
                            self.store.update(r["id"], status="placed", order_id=o.order_id, reason="found on resync")
                            report["updated"].append(f"#{r['id']} {sym}: sending -> placed")
                        continue
                    if o.status in ("cancelled", "rejected"):
                        self.store.update(r["id"], status="cancelled" if o.status == "cancelled" else "error",
                                          order_id=o.order_id, reason=f"order {o.status} on the exchange")
                        report["updated"].append(f"#{r['id']} {sym}: {status} -> {o.status}")
                        continue
                    self.store.update(r["id"], status="filled", order_id=o.order_id, fill_price=o.avg_price)
                    report["updated"].append(f"#{r['id']} {sym}: {status} -> filled")
                # status is "filled" here: is the position still there?
                pos = positions.get(sym)
                want = "long" if r["side"] == "buy" else "short"
                if pos is None or pos.side != want:
                    pnl = self.ex.closed_pnl(sym, int(r_ts(r) * 1000))
                    self.store.update(r["id"], status="closed", realized_pnl=round(pnl, 4),
                                      reason="closed on the exchange by SL/TP or by hand")
                    report["updated"].append(f"#{r['id']} {sym}: filled -> closed, P/L {pnl:.2f}")
                elif pos.sl is None and r["sl"]:
                    try:
                        self.ex.set_position_stops(sym, D(r["sl"]), D(r["tp"]) if r["tp"] else None)
                        report["protected"].append(f"{sym}: SL {r['sl']} set again")
                    except ExchangeError as e:
                        report["protected"].append(f"{sym}: could not set SL ({e}), position is UNPROTECTED")
            filled_syms = {r["symbol"] for r in self.store.rows("filled")}
            report["unknown_orders"] = [f"{o.symbol} {o.side} {o.qty} ({c})" for c, o in bot_orders.items()
                                        if c not in known_clients]
            report["unknown_positions"] = [f"{p.symbol} {p.side} {p.qty}" + ("" if p.sl else " NO STOP")
                                           for s, p in positions.items() if s not in filled_syms]
            log.info("resync: %s", report)
            return report

    def status(self) -> dict:
        bal = self.ex.balance()
        start = self.store.get("day_start_equity")
        loss = (D(start) - bal.equity) / D(start) * 100 if start and D(start) > 0 else D(0)
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        return {
            "equity": str(round(bal.equity, 2)), "available": str(round(bal.available, 2)),
            "day": self.store.get("day"), "day_start_equity": start, "loss_today_pct": str(round(loss, 2)),
            "daily_limit_hit": self.store.get("halted_day") == day, "kill_switch": self.store.get("kill") or "off",
            "positions": [f"{p.symbol} {p.side} {p.qty} @ {p.entry_price} SL {p.sl} TP {p.tp} uPnL {round(p.unrealized_pnl, 2)}"
                          for p in self.ex.positions()],
            "bot_orders": [f"{o.symbol} {o.side} {o.type} {o.qty} @ {o.price} ({o.client_id})"
                           for o in self.ex.open_orders() if o.client_id.startswith(PREFIX)],
        }


def r_ts(row) -> float:
    return float(row["ts"])
