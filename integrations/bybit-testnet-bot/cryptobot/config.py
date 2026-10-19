"""Settings from the .env file next to this package (git-ignored) or from environment variables.

Real environment variables win over .env. API keys are only read here and handed to the adapter;
they are never printed, logged or written anywhere else. No keys -> the mock exchange is used.
"""
import os
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT / ".env"


def read_env_file(path: Path) -> dict:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes. No extra package needed."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


@dataclass
class Settings:
    exchange: str = "bybit"                 # bybit | mock
    bybit_env: str = "testnet"              # testnet | demo   (no mainnet in this build)
    bybit_api_key: str = field(default="", repr=False)
    bybit_api_secret: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    risk_pct: Decimal = Decimal("0.5")      # default risk per trade, % of equity
    max_risk_pct: Decimal = Decimal("2")    # a signal asking for more is refused
    leverage: Decimal = Decimal("3")
    max_leverage: Decimal = Decimal("10")
    daily_loss_pct: Decimal = Decimal("3")  # 0 = off; measured from equity at 00:00 UTC
    flatten_on_daily_limit: bool = True     # close positions when the daily limit is hit
    default_sl_pct: Decimal = Decimal("0")  # 0 = a signal without a stop is refused
    default_tp_r: Decimal = Decimal("0")    # 0 = no default take profit
    reverse: bool = True                    # opposite signal closes the position and opens the other way
    symbols: tuple = ()                     # allowed symbols; empty = any
    max_notional: Decimal = Decimal("0")    # 0 = no cap; position value cap in USDT
    fee_rate: Decimal = Decimal("0.00055")  # taker fee used in sizing (Bybit linear taker 0.055%)
    data_dir: Path = ROOT / "data"
    resync_seconds: int = 60
    mock_equity: Decimal = Decimal("10000")

    @property
    def has_keys(self) -> bool:
        return bool(self.bybit_api_key and self.bybit_api_secret)


def _convert(kind, value: str):
    if kind is bool:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if kind is Decimal:
        return Decimal(value.strip())
    if kind is int:
        return int(value)
    if kind is tuple:
        return tuple(s.strip().upper() for s in value.split(",") if s.strip())
    if kind is Path:
        p = Path(value)
        return p if p.is_absolute() else ROOT / p
    return value.strip()


def load_settings(env_file: Path = DEFAULT_ENV_FILE, environ=None) -> Settings:
    values = read_env_file(Path(env_file)) if env_file else {}
    values.update({k: v for k, v in (os.environ if environ is None else environ).items()})
    s = Settings()
    kinds = {"bool": bool, "Decimal": Decimal, "int": int, "tuple": tuple, "Path": Path, "str": str}
    for f in fields(Settings):
        raw = values.get(f.name.upper())
        if raw is not None and raw != "":
            setattr(s, f.name, _convert(kinds[f.type if isinstance(f.type, str) else f.type.__name__], raw))
    if s.bybit_env not in ("testnet", "demo"):
        raise SystemExit("BYBIT_ENV must be testnet or demo. This practice build does not trade real money.")
    return s


def make_exchange(s: Settings):
    """Returns (exchange, note). Bybit when keys are set, else the mock with its state in data/."""
    if s.exchange == "bybit" and s.has_keys:
        from .exchange.bybit import BybitV5
        return BybitV5(s.bybit_api_key, s.bybit_api_secret, env=s.bybit_env), f"Bybit {s.bybit_env}"
    from .exchange.mock import MockExchange
    s.data_dir.mkdir(parents=True, exist_ok=True)
    note = "mock exchange" if s.exchange == "mock" else "mock exchange (no Bybit keys in .env)"
    return MockExchange(equity=s.mock_equity, fee_rate=s.fee_rate, path=s.data_dir / "mock_exchange.json"), note
