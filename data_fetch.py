"""yfinance wrapper with disk cache for daily data."""
from __future__ import annotations
import logging
import os
import pickle
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from tickers import Ticker, STOCK_BIST, STOCK_US, CRYPTO, FUTURE, INDEX

# SCANNER_BASE env points to the project root (cache/, hisseler.txt sit here).
# Defaults to local Mac path for backward compat with launchd jobs.
BASE_DIR = Path(os.environ.get("SCANNER_BASE", "/Users/burak/Desktop/piyasalar"))
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger(__name__)


def _safe_filename(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_").replace("=", "_eq_").replace("^", "idx_")


def _cache_path(symbol: str, kind: str) -> Path:
    return CACHE_DIR / f"{_safe_filename(symbol)}__{kind}.pkl"


def _is_fresh_today(p: Path) -> bool:
    if not p.exists():
        return False
    mtime = datetime.fromtimestamp(p.stat().st_mtime).date()
    return mtime == date.today()


def _load_cached(symbol: str, kind: str) -> Optional[pd.DataFrame]:
    p = _cache_path(symbol, kind)
    if not _is_fresh_today(p):
        return None
    try:
        return pickle.loads(p.read_bytes())
    except Exception:
        return None


def _save_cached(symbol: str, kind: str, df: pd.DataFrame) -> None:
    p = _cache_path(symbol, kind)
    try:
        p.write_bytes(pickle.dumps(df))
    except Exception as e:
        log.warning("cache save failed for %s: %s", symbol, e)


def batch_download_daily(
    symbols: List[str], period: str = "1y", interval: str = "1d",
    batch_size: int = 50, use_cache: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Download daily OHLCV for many symbols in batches. Returns dict[symbol -> DataFrame]."""
    out: Dict[str, pd.DataFrame] = {}
    to_fetch: List[str] = []
    if use_cache:
        for s in symbols:
            cached = _load_cached(s, f"{interval}_{period}")
            if cached is not None and not cached.empty:
                out[s] = cached
            else:
                to_fetch.append(s)
    else:
        to_fetch = list(symbols)

    for i in range(0, len(to_fetch), batch_size):
        chunk = to_fetch[i:i + batch_size]
        log.info("yf batch %d-%d / %d", i + 1, i + len(chunk), len(to_fetch))
        try:
            df = yf.download(
                chunk, period=period, interval=interval,
                group_by="ticker", auto_adjust=True, progress=False,
                threads=True, repair=False,
            )
        except Exception as e:
            log.warning("batch download failed: %s", e)
            time.sleep(2)
            continue


        for sym in chunk:
            try:
                if len(chunk) == 1:
                    sub = df.copy()
                else:
                    if sym not in df.columns.get_level_values(0):
                        continue
                    sub = df[sym].copy()
                sub = sub.dropna(how="all")
                if sub.empty or len(sub) < 30:
                    continue
                # Standardize columns to lowercase
                sub.columns = [str(c).lower() for c in sub.columns]
                needed = {"open", "high", "low", "close", "volume"}
                if not needed.issubset(set(sub.columns)):
                    continue
                out[sym] = sub
                _save_cached(sym, f"{interval}_{period}", sub)
            except Exception as e:
                log.debug("parse fail %s: %s", sym, e)
                continue
    return out


def get_intraday(symbol: str, period: str = "5d", interval: str = "1h") -> Optional[pd.DataFrame]:
    """Fetch intraday data (no cache - always fresh)."""
    try:
        df = yf.download(
            symbol, period=period, interval=interval,
            auto_adjust=True, progress=False, threads=False,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        return df.dropna(how="all")
    except Exception as e:
        log.debug("intraday fail %s: %s", symbol, e)
        return None


def get_exchange(symbol: str) -> str:
    """Best-effort exchange lookup for US stocks (NASDAQ/NYSE). Returns 'NASDAQ' default."""
    try:
        info = yf.Ticker(symbol).get_info()
        ex = (info.get("exchange") or info.get("fullExchangeName") or "").upper()
        if "NYS" in ex or "NEW YORK" in ex:
            return "NYSE"
        if "NMS" in ex or "NASDAQ" in ex or "NCM" in ex or "NGM" in ex:
            return "NASDAQ"
        if "AMS" in ex or "AMEX" in ex:
            return "AMEX"
    except Exception:
        pass
    return "NASDAQ"


def get_earnings_date(symbol: str) -> Optional[date]:
    """Next earnings date if available."""
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed and isinstance(ed, list) and len(ed) > 0:
                d = ed[0]
                if isinstance(d, datetime):
                    return d.date()
                if isinstance(d, date):
                    return d
    except Exception:
        pass
    return None
