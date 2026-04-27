"""Ticker parser + asset type classifier for Mr Burki Tarama."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

# Asset types
STOCK_BIST = "STOCK_BIST"
STOCK_US = "STOCK_US"
CRYPTO = "CRYPTO"
FUTURE = "FUTURE"
INDEX = "INDEX"

MARKET_BIST = "BIST"
MARKET_US = "US"
MARKET_CRYPTO = "CRYPTO"
MARKET_FUTURE = "FUTURE"
MARKET_INDEX = "INDEX"


@dataclass(frozen=True)
class Ticker:
    """Normalized ticker representation."""
    symbol: str          # yfinance symbol (e.g. "THYAO.IS", "AAPL", "BTC-USD")
    display: str         # short display (e.g. "THYAO", "AAPL", "BTC")
    asset_type: str      # STOCK_BIST | STOCK_US | CRYPTO | FUTURE | INDEX
    market: str          # BIST | US | CRYPTO | FUTURE | INDEX


def classify(raw: str) -> Ticker:
    """Classify a single raw symbol from hisseler.txt."""
    s = raw.strip()
    if not s:
        raise ValueError("empty symbol")
    # Strip common TV prefixes if present (BIST:, NASDAQ:, NYSE:, BINANCE:, TVC:, etc.)
    if ":" in s:
        prefix, sym = s.split(":", 1)
        prefix = prefix.upper()
        sym = sym.strip()
        if prefix == "BIST":
            return Ticker(f"{sym}.IS", sym, STOCK_BIST, MARKET_BIST)
        if prefix in ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"):
            return Ticker(sym, sym, STOCK_US, MARKET_US)
        if prefix in ("BINANCE", "COINBASE", "BITSTAMP", "KRAKEN", "CRYPTO"):
            # e.g. BINANCE:SHIBUSDT -> SHIB-USD
            base = sym.upper()
            if base.endswith("USDT"):
                base = base[:-4]
            elif base.endswith("USD"):
                base = base[:-3]
            return Ticker(f"{base}-USD", base, CRYPTO, MARKET_CRYPTO)
        if prefix == "TVC":
            return Ticker(sym, sym, INDEX, MARKET_INDEX)
        if prefix in ("COMEX", "NYMEX", "CBOT", "CME", "ICE", "FX"):
            return Ticker(sym, sym, FUTURE, MARKET_FUTURE)
        # Unknown prefix - best effort: treat as US stock
        return Ticker(sym, sym, STOCK_US, MARKET_US)

    # No prefix - infer from suffix/shape
    if s.endswith(".IS"):
        return Ticker(s, s[:-3], STOCK_BIST, MARKET_BIST)
    if s.startswith("^"):
        return Ticker(s, s[1:], INDEX, MARKET_INDEX)
    if s.endswith("=F"):
        return Ticker(s, s[:-2], FUTURE, MARKET_FUTURE)
    if s.endswith("-USD") or s.endswith("-USDT"):
        base = s.rsplit("-", 1)[0]
        return Ticker(s if s.endswith("-USD") else f"{base}-USD", base, CRYPTO, MARKET_CRYPTO)
    # Default: bare symbol = US stock
    return Ticker(s, s, STOCK_US, MARKET_US)


def parse_file(path: str | Path) -> List[Ticker]:
    """Read hisseler.txt and return list of unique Tickers."""
    path = Path(path).expanduser()
    seen: set[str] = set()
    out: List[Ticker] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            t = classify(line)
        except ValueError:
            continue
        if t.symbol in seen:
            continue
        seen.add(t.symbol)
        out.append(t)
    return out


def to_tv_symbol(t: Ticker) -> str:
    """Convert Ticker -> TradingView symbol string for export."""
    if t.asset_type == STOCK_BIST:
        return f"BIST:{t.display}"
    if t.asset_type == INDEX:
        # Common index TV mapping
        idx_map = {
            "GSPC": "TVC:SPX", "DJI": "TVC:DJI", "IXIC": "TVC:NDX",
            "RUT": "TVC:RUT", "FTSE": "TVC:UKX", "GDAXI": "XETR:DAX",
            "FCHI": "EURONEXT:PX1", "N225": "TVC:NI225",
            "STOXX50E": "TVC:SX5E",
            "XU100": "BIST:XU100", "XU030": "BIST:XU030",
        }
        return idx_map.get(t.display, f"TVC:{t.display}")
    if t.asset_type == CRYPTO:
        return f"BINANCE:{t.display}USDT"
    if t.asset_type == FUTURE:
        return f"COMEX:{t.display}"
    # STOCK_US -> NASDAQ default; will be overridden later if exchange info known
    return f"NASDAQ:{t.display}"


def benchmark_for(t: Ticker) -> str:
    """Return yfinance benchmark symbol for relative strength."""
    if t.asset_type == STOCK_BIST:
        return "XU030.IS"
    if t.asset_type == CRYPTO:
        return "BTC-USD"
    # US stocks, futures, indices -> S&P 500
    return "^GSPC"
