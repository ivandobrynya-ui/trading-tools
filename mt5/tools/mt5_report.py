"""Reads an MT5 Strategy Tester HTML report (any terminal language: Russian or English labels)."""
import html
import re

LABELS = {
    "trades": ("Всего трейдов", "Total Trades"),
    "pf": ("Прибыльность", "Profit Factor"),
    "net": ("Чистая прибыль", "Total Net Profit"),
    "dd": ("Максимальная просадка по средствам", "Equity Drawdown Maximal"),
    "quality": ("Качество истории", "History Quality"),
    "bars": ("Бары", "Bars"),
    "win": ("Прибыльные трейды (% от всех)", "Profit Trades (% of total)"),
}


def _load(path):
    raw = open(path, "rb").read()
    return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="replace")


def _cells(fragment):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", fragment, flags=re.S)]


def summary(path):
    cells = [c for c in _cells(_load(path)[:200000]) if c]
    out = {}
    for key, names in LABELS.items():
        for i, c in enumerate(cells[:-1]):
            if c.rstrip(":") in names:
                out[key] = cells[i + 1]
                break
    return out


def deals(path):
    """List of deals: dict(time, type, direction, volume, price, profit, comment)
    plus deal, symbol, commission, swap, balance when the report has those columns."""
    s = _load(path)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", s, flags=re.S)
    start = next(i for i, r in enumerate(rows) if ("Направление" in r or "Direction" in r) and ("Сделка" in r or "Deal" in r))
    hdr = _cells(rows[start])
    idx = {name: k for k, name in enumerate(hdr)}

    def col(*names):
        for n in names:
            if n in idx:
                return idx[n]
        raise KeyError(names)

    c_time, c_type, c_dir = col("Время", "Time"), col("Тип", "Type"), col("Направление", "Direction")
    c_vol, c_price, c_profit, c_comment = col("Объем", "Volume"), col("Цена", "Price"), col("Прибыль", "Profit"), col("Комментарий", "Comment")
    extra = {k: col(*names) for k, names in (("deal", ("Сделка", "Deal")), ("symbol", ("Символ", "Symbol")),
                                             ("commission", ("Комиссия", "Commission")), ("swap", ("Своп", "Swap")),
                                             ("balance", ("Баланс", "Balance"))) if any(n in idx for n in names)}
    out = []
    for r in rows[start + 1:]:
        c = _cells(r)
        if len(c) < len(hdr) - 1 or not re.match(r"\d{4}\.\d\d\.\d\d", c[0] or ""):
            continue
        def num(v):  # "10 000.00", and large volumes shown as "1.294K" / "2.5M"
            v = v.replace(" ", "").replace(" ", "")
            mult = {"K": 1e3, "M": 1e6}.get(v[-1:], 1)
            return float((v[:-1] if mult != 1 else v) or 0) * mult
        out.append({"time": c[c_time], "type": c[c_type], "direction": c[c_dir], "volume": num(c[c_vol]) if c[c_vol] else 0,
                    "price": num(c[c_price]) if c[c_price] else 0, "profit": num(c[c_profit]) if c[c_profit] else 0,
                    "comment": c[c_comment] if c_comment < len(c) else ""})
        for key, k in extra.items():
            v = c[k] if k < len(c) else ""
            out[-1][key] = v if key == "symbol" else (num(v) if v else 0.0)
    return out
