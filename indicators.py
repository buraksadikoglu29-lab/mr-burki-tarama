"""Lightweight technical indicators (no pandas-ta dependency)."""
from __future__ import annotations
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    prev_c = c.shift(1)
    return pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    tr = true_range(h, l, c)
    return tr.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()


def adx(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    """Average Directional Index (Wilder)."""
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    tr = true_range(h, l, c)
    atr_n = tr.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean() / atr_n
    mdi = 100 * minus_dm.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean() / atr_n
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()


def bollinger_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    mid = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return (upper - lower) / mid


def slope_pct(s: pd.Series, n: int) -> float:
    """Percent change of series over last n bars (vs n bars ago)."""
    if len(s.dropna()) < n + 1:
        return float("nan")
    cur = s.iloc[-1]
    prev = s.iloc[-1 - n]
    if prev == 0 or pd.isna(prev) or pd.isna(cur):
        return float("nan")
    return float((cur - prev) / abs(prev) * 100.0)


def percentile_rank(values: pd.Series, value: float) -> float:
    """Percentile of `value` within `values` (0-100)."""
    s = values.dropna()
    if len(s) == 0 or pd.isna(value):
        return float("nan")
    return float((s < value).mean() * 100.0)
