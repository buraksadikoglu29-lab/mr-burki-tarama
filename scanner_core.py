"""Core scanner: filters, pre-computed metrics, scoring, action layer, market context."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickers import (
    Ticker, parse_file, benchmark_for, to_tv_symbol,
    STOCK_BIST, STOCK_US, CRYPTO, FUTURE, INDEX,
)
from data_fetch import (
    batch_download_daily, get_intraday, get_exchange, get_earnings_date, CACHE_DIR,
)
from indicators import sma, ema, rsi, atr, adx, bollinger_width, slope_pct, percentile_rank

log = logging.getLogger(__name__)

import os
POOL_PATH = CACHE_DIR / "candidate_pool.json"
MARKET_CTX_PATH = CACHE_DIR / "market_context.json"
SCORES_PATH = CACHE_DIR / "scored_results.json"
_BASE = Path(os.environ.get("SCANNER_BASE", "/Users/burak/Desktop/piyasalar"))
HISSELER_PATH = _BASE / "hisseler.txt"


# ---------- helpers ----------
def _series(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(float)


def _to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Convert daily OHLCV to weekly."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return daily.resample("W-FRI").agg(agg).dropna(how="any")


# ---------- Stage 2 / Trend Template ----------
def passes_trend_template(daily: pd.DataFrame, asset_type: str) -> Tuple[bool, dict]:
    """O'Neil/Minervini Trend Template + Stage 2 (Weinstein 30W MA)."""
    if len(daily) < 220:
        return False, {"reason": "insufficient_history"}
    c = _series(daily, "close")
    sma50 = sma(c, 50).iloc[-1]
    sma150 = sma(c, 150).iloc[-1]
    sma200 = sma(c, 200).iloc[-1]
    sma200_30d_ago = sma(c, 200).iloc[-22] if len(c) > 222 else float("nan")
    last = c.iloc[-1]

    # Stage 2: weekly 30W MA
    weekly = _to_weekly(daily)
    if len(weekly) < 32:
        return False, {"reason": "insufficient_weekly"}
    wc = _series(weekly, "close")
    w30 = sma(wc, 30)
    w30_now = w30.iloc[-1]
    w30_4w_ago = w30.iloc[-5] if len(w30.dropna()) >= 5 else float("nan")
    w30_slope_up = pd.notna(w30_4w_ago) and w30_now > w30_4w_ago

    # 52w high/low (loosened: near = within 40%, above-low = +15%)
    last_252 = c.tail(252)
    high52 = last_252.max()
    low52 = last_252.min()
    near_high_25 = (last >= high52 * 0.60)
    above_low_30 = (last >= low52 * 1.15)

    checks = {
        "price_gt_50": last > sma50 if pd.notna(sma50) else False,
        "50_gt_150": sma50 > sma150 if pd.notna(sma150) else False,
        "150_gt_200": sma150 > sma200 if pd.notna(sma200) else False,
        "200_up_1m": (sma200 > sma200_30d_ago) if pd.notna(sma200_30d_ago) else False,
        "stage2_above_30w": last > w30_now if pd.notna(w30_now) else False,
        "stage2_30w_up": bool(w30_slope_up),
        "near_52w_high": bool(near_high_25),
        "above_52w_low": bool(above_low_30),
    }
    # Indices/crypto/futures: relax stage2 weekly check (they don't always have clean weekly)
    if asset_type in (CRYPTO, FUTURE, INDEX):
        passed = checks["price_gt_50"] and checks["50_gt_150"] and checks["near_52w_high"] and checks["above_52w_low"]
    else:
        passed = all(checks.values())
    return passed, checks


def passes_liquidity(daily: pd.DataFrame, asset_type: str) -> Tuple[bool, dict]:
    last = float(_series(daily, "close").iloc[-1])
    avg_vol_50 = float(_series(daily, "volume").tail(50).mean())
    avg_dollar_vol = avg_vol_50 * last  # for stocks: TL/USD; for crypto: USD; futures: contract*price
    if asset_type == STOCK_BIST:
        ok = (avg_dollar_vol > 50_000_000) and (last > 5)
    elif asset_type == STOCK_US:
        ok = (avg_dollar_vol > 5_000_000) and (last > 10)
    elif asset_type == CRYPTO:
        ok = (avg_dollar_vol > 10_000_000) and (last > 0.0001)
    elif asset_type == FUTURE:
        ok = avg_vol_50 > 1000  # contract count
    elif asset_type == INDEX:
        ok = True  # indices always pass liquidity
    else:
        ok = avg_dollar_vol > 1_000_000
    return ok, {"avg_dollar_vol_50d": avg_dollar_vol, "last_price": last}


def find_base(daily: pd.DataFrame) -> dict:
    """Find current base: pivot, depth, length, contractions count."""
    if len(daily) < 60:
        return {"pivot": None, "length_weeks": None, "depth_pct": None,
                "contractions": 0, "pattern": "unknown"}
    c = _series(daily, "close")
    h = _series(daily, "high")
    l = _series(daily, "low")
    last = float(c.iloc[-1])

    # Look back up to 65 weeks (325 trading days)
    lookback = min(len(c) - 1, 325)
    window = c.iloc[-lookback:]
    high_window = h.iloc[-lookback:]
    low_window = l.iloc[-lookback:]

    # Pivot = max high in lookback (recent base ceiling)
    # but exclude last 3 days to allow today's breakout
    if lookback > 5:
        pivot = float(high_window.iloc[:-3].max())
    else:
        pivot = float(high_window.max())

    # Find when pivot was set (start of base)
    try:
        pivot_idx = high_window[high_window == pivot].index[-1]
        days_since_pivot = (high_window.index[-1] - pivot_idx).days
        length_weeks = max(1, days_since_pivot // 7)
    except Exception:
        length_weeks = None

    # Depth: max drawdown from pivot during base
    base_window = low_window.loc[high_window.index[-min(lookback, days_since_pivot + 5):]] \
        if length_weeks else low_window
    base_low = float(base_window.min()) if len(base_window) else float(low_window.min())
    depth_pct = (pivot - base_low) / pivot * 100 if pivot > 0 else None

    # VCP contraction count: simple version
    # Look at last ~50 trading days, find rolling 5-day highs and count distinct local peaks
    contractions = _count_contractions(high_window.tail(min(50, len(high_window))),
                                        low_window.tail(min(50, len(low_window))))

    # Pattern guess
    pattern = _guess_pattern(c.tail(min(60, len(c))), pivot, depth_pct or 0, contractions)

    return {
        "pivot": pivot, "length_weeks": length_weeks, "depth_pct": depth_pct,
        "contractions": contractions, "pattern": pattern, "last": last,
    }


def _count_contractions(highs: pd.Series, lows: pd.Series) -> int:
    """Count successive contractions: each one tighter than previous (high-low range shrinks)."""
    if len(highs) < 15:
        return 0
    # Split into ~5-day windows, compute range
    arr = []
    step = 5
    for i in range(0, len(highs) - step + 1, step):
        rng = highs.iloc[i:i + step].max() - lows.iloc[i:i + step].min()
        arr.append(rng)
    if not arr:
        return 0
    cnt = 0
    for i in range(1, len(arr)):
        if arr[i] < arr[i - 1] * 0.85:
            cnt += 1
    return cnt


def _guess_pattern(close: pd.Series, pivot: float, depth: float, contractions: int) -> str:
    if contractions >= 3 and depth < 25:
        return "VCP"
    if depth > 25 and depth < 35:
        return "cup-handle"
    if depth < 12:
        return "flat"
    if contractions >= 2 and depth < 20:
        return "asc-triangle"
    return "other"


def passes_base_quality(daily: pd.DataFrame) -> Tuple[bool, dict]:
    base = find_base(daily)
    length = base.get("length_weeks") or 0
    depth = base.get("depth_pct") or 100.0
    # Loosened: depth max 35 -> 50
    ok = (5 <= length <= 65) and (8 <= depth <= 50)
    return ok, base


# ---------- Disqualifiers ----------
def check_disqualifiers(daily: pd.DataFrame, asset_type: str,
                         earnings_date: Optional[date]) -> Tuple[bool, List[str]]:
    """Returns (passes_all, list_of_failed_disqualifiers)."""
    fails: List[str] = []
    c = _series(daily, "close")
    v = _series(daily, "volume")
    h = _series(daily, "high")
    l = _series(daily, "low")

    # (climax_run check removed per user request)
    # (wide_loose_base check removed per user request — base depth already enforced in passes_base_quality)

    # Earnings within 5 trading days (skip for crypto/futures/indices)
    if earnings_date and asset_type == STOCK_US or asset_type == STOCK_BIST:
        if earnings_date and (earnings_date - date.today()).days <= 5 and (earnings_date - date.today()).days >= 0:
            fails.append("earnings_5d")

    # High-volume gap-down >5% in last 20 days
    if len(c) > 21:
        for i in range(-20, 0):
            try:
                gap = (c.iloc[i] / c.iloc[i - 1] - 1) * 100
                if gap < -5 and v.iloc[i] > v.iloc[i - 1] * 1.5:
                    fails.append("hv_gap_down")
                    break
            except Exception:
                continue

    return (len(fails) == 0), fails


# ---------- Pre-computed metrics ----------
def compute_metrics(t: Ticker, daily: pd.DataFrame,
                     bench_daily: Optional[pd.DataFrame],
                     earnings_date: Optional[date]) -> dict:
    """Compute all per-ticker metrics for the candidate pool."""
    base = find_base(daily)
    c = _series(daily, "close")
    v = _series(daily, "volume")
    h = _series(daily, "high")
    l = _series(daily, "low")
    last = float(c.iloc[-1])

    # RS percentiles vs benchmark
    rs_3m = rs_6m = float("nan")
    if bench_daily is not None and len(bench_daily) > 130:
        bc = _series(bench_daily, "close")
        # Align dates
        joined = pd.concat([c.rename("p"), bc.rename("b")], axis=1).dropna()
        if len(joined) > 130:
            ret_p_3m = joined["p"].pct_change(63)
            ret_b_3m = joined["b"].pct_change(63)
            rs_series_3m = (1 + ret_p_3m) / (1 + ret_b_3m) - 1
            rs_3m = percentile_rank(rs_series_3m.tail(252).dropna(), float(rs_series_3m.iloc[-1]))
            ret_p_6m = joined["p"].pct_change(126)
            ret_b_6m = joined["b"].pct_change(126)
            rs_series_6m = (1 + ret_p_6m) / (1 + ret_b_6m) - 1
            rs_6m = percentile_rank(rs_series_6m.tail(252).dropna(), float(rs_series_6m.iloc[-1]))

    avg_vol_50 = float(v.tail(50).mean())
    tight_5d = float((h.tail(5).max() - l.tail(5).min()) / last * 100) if last > 0 else None
    bb_w = bollinger_width(c, 20)
    bb_recent = bb_w.tail(20)
    bb_squeeze = bool((bb_recent.iloc[-1] < bb_recent.median()) if len(bb_recent.dropna()) > 5 else False)

    # 50DMA slope last 3 months
    sma50 = sma(c, 50)
    slope_50_3m = slope_pct(sma50, 63) if len(sma50.dropna()) > 64 else float("nan")

    # ATR(20)
    atr20 = float(atr(h, l, c, 20).iloc[-1]) if len(c) > 20 else float("nan")

    return {
        "symbol": t.symbol,
        "display": t.display,
        "asset_type": t.asset_type,
        "market": t.market,
        "tv_symbol": to_tv_symbol(t),
        "last_price": last,
        "pivot_price": base["pivot"],
        "base_pattern": base["pattern"],
        "base_length_weeks": base["length_weeks"],
        "base_depth_pct": base["depth_pct"],
        "vcp_contractions": base["contractions"],
        "rs_3m_percentile": rs_3m,
        "rs_6m_percentile": rs_6m,
        "avg_volume_50d": avg_vol_50,
        "tight_5d_range_pct": tight_5d,
        "bb_squeeze_recent": bb_squeeze,
        "earnings_next_date": earnings_date.isoformat() if earnings_date else None,
        "atr_20": atr20,
        "slope_50dma_3m_pct": slope_50_3m,
    }


# ---------- Mode A: Daily Refresh ----------
def run_daily_refresh(hisseler_path: Path = HISSELER_PATH, use_cache: bool = True) -> dict:
    """Read tickers, download data, apply hard filters + disqualifiers, persist pool."""
    log.info("=== MODE A: Daily Refresh start ===")
    tickers = parse_file(hisseler_path)
    log.info("parsed %d tickers", len(tickers))

    # Group by needed benchmark
    symbols = [t.symbol for t in tickers]
    benchmarks = sorted(set(benchmark_for(t) for t in tickers))
    all_syms = list(set(symbols + benchmarks))

    log.info("downloading %d unique symbols (incl benchmarks)...", len(all_syms))
    daily_map = batch_download_daily(all_syms, period="1y", interval="1d",
                                       batch_size=50, use_cache=use_cache)
    log.info("got daily data for %d / %d symbols", len(daily_map), len(all_syms))

    pool = []
    rejected = []
    for t in tickers:
        df = daily_map.get(t.symbol)
        if df is None or len(df) < 60:
            rejected.append({"symbol": t.symbol, "reason": "no_data"})
            continue

        # Hard filters
        tt_ok, tt_checks = passes_trend_template(df, t.asset_type)
        if not tt_ok:
            rejected.append({"symbol": t.symbol, "reason": "trend_template", "checks": tt_checks})
            continue

        liq_ok, liq_info = passes_liquidity(df, t.asset_type)
        if not liq_ok:
            rejected.append({"symbol": t.symbol, "reason": "liquidity", "info": liq_info})
            continue

        bq_ok, base_info = passes_base_quality(df)
        if not bq_ok:
            rejected.append({"symbol": t.symbol, "reason": "base_quality", "base": base_info})
            continue

        # Earnings check (only US/BIST stocks; expensive call - skip if not needed)
        earnings = None
        if t.asset_type in (STOCK_BIST, STOCK_US):
            try:
                earnings = get_earnings_date(t.symbol)
            except Exception:
                earnings = None

        # Disqualifiers
        dq_ok, dq_fails = check_disqualifiers(df, t.asset_type, earnings)
        if not dq_ok:
            rejected.append({"symbol": t.symbol, "reason": "disqualified", "fails": dq_fails})
            continue

        # Compute metrics
        bench_sym = benchmark_for(t)
        bench_df = daily_map.get(bench_sym)
        try:
            metrics = compute_metrics(t, df, bench_df, earnings)
            # For US stocks, look up exchange for correct TV prefix
            if t.asset_type == STOCK_US:
                try:
                    ex = get_exchange(t.symbol)
                    metrics["tv_symbol"] = f"{ex}:{t.display}"
                except Exception:
                    pass
            pool.append(metrics)
        except Exception as e:
            log.warning("metrics fail %s: %s", t.symbol, e)
            rejected.append({"symbol": t.symbol, "reason": "metrics_error", "err": str(e)})

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tickers": len(tickers),
        "pool_size": len(pool),
        "rejected_count": len(rejected),
        "pool": pool,
    }
    POOL_PATH.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    log.info("=== Daily Refresh done: pool=%d rejected=%d ===", len(pool), len(rejected))
    return result


# ---------- Mode B: Hourly Scoring ----------
def score_one(metrics: dict, intraday: Optional[pd.DataFrame],
               daily: Optional[pd.DataFrame]) -> dict:
    """Apply scoring rubric to one candidate. Returns dict with score + breakdown + action."""
    breakdown = {"volume": 0, "rs": 0, "base": 0, "trend": 0, "breakout": 0, "momentum": 0}
    last_price = metrics["last_price"]
    pivot = metrics.get("pivot_price")
    avg_vol_50 = metrics.get("avg_volume_50d") or 0

    # Use latest intraday if available, else daily
    if intraday is not None and not intraday.empty:
        today_close = float(intraday["close"].iloc[-1])
        today_vol = float(intraday["volume"].sum())
        day_high = float(intraday["high"].max())
        day_low = float(intraday["low"].min())
    elif daily is not None and not daily.empty:
        today_close = float(daily["close"].iloc[-1])
        today_vol = float(daily["volume"].iloc[-1])
        day_high = float(daily["high"].iloc[-1])
        day_low = float(daily["low"].iloc[-1])
    else:
        today_close = last_price
        today_vol = avg_vol_50
        day_high = last_price
        day_low = last_price

    last_price = today_close

    # 1) VOLUME QUALITY (25)
    vol_mult = today_vol / avg_vol_50 if avg_vol_50 > 0 else 0
    if vol_mult >= 3.0:
        breakdown["volume"] = 25
    elif vol_mult >= 2.0:
        breakdown["volume"] = 18
    elif vol_mult >= 1.5:
        breakdown["volume"] = 10
    else:
        breakdown["volume"] = 0

    # 2) RELATIVE STRENGTH (20)
    rs3 = metrics.get("rs_3m_percentile") or 0
    rs6 = metrics.get("rs_6m_percentile") or 0
    rs_pts = 0
    if rs3 >= 80:
        rs_pts += 12
    if rs6 >= 70:
        rs_pts += 8
    breakdown["rs"] = min(20, rs_pts)

    # 3) BASE & PATTERN (15)
    base_pts = 0
    if (metrics.get("vcp_contractions") or 0) >= 3:
        base_pts += 8
    if metrics.get("base_pattern") in ("VCP", "cup-handle", "flat", "asc-triangle", "double-bottom"):
        base_pts += 4
    if (metrics.get("tight_5d_range_pct") or 100) < 10:
        base_pts += 3
    breakdown["base"] = min(15, base_pts)

    # 4) TREND (15)
    trend_pts = 5  # Stage 2 was already verified in pool
    if (metrics.get("slope_50dma_3m_pct") or 0) > 5:
        trend_pts += 5
    if metrics.get("bb_squeeze_recent"):
        trend_pts += 5
    breakdown["trend"] = trend_pts


    # 5) BREAKOUT (15)
    bo_pts = 0
    pivot_dist = ((last_price - pivot) / pivot * 100) if pivot else None
    fresh_breakout = pivot is not None and last_price >= pivot * 0.998
    near_pivot = pivot is not None and pivot_dist is not None and -3 <= pivot_dist <= 3
    if fresh_breakout or near_pivot:
        bo_pts += 5
    if day_high > day_low:
        close_in_top_quarter = (today_close - day_low) / (day_high - day_low) >= 0.75
        if close_in_top_quarter:
            bo_pts += 5
    atr20 = metrics.get("atr_20") or 0
    day_range = day_high - day_low
    if atr20 > 0 and day_range >= 1.5 * atr20:
        bo_pts += 5
    breakdown["breakout"] = bo_pts

    # 6) MOMENTUM (10)
    mo_pts = 0
    if daily is not None and len(daily) > 14:
        last_rsi = float(rsi(daily["close"].astype(float), 14).iloc[-1])
        if 55 <= last_rsi <= 70:
            mo_pts += 6
        last_adx = float(adx(daily["high"].astype(float), daily["low"].astype(float),
                              daily["close"].astype(float), 14).iloc[-1])
        if last_adx > 25:
            mo_pts += 4
    breakdown["momentum"] = mo_pts

    # Volume gate: <1.5x AND fresh => SCORE = 0 (anticipation only)
    score = sum(breakdown.values())
    if vol_mult < 1.5 and fresh_breakout:
        score = 0
        breakdown["volume_gate_failed"] = True

    # Action layer
    action = classify_action(score, fresh_breakout, near_pivot, pivot_dist)

    # Stops/targets (simple ATR-based)
    stop = None
    t1 = None
    t2 = None
    if pivot and atr20 > 0:
        stop = round(pivot - 2 * atr20, 2)
        t1 = round(pivot + 2 * atr20, 2)
        t2 = round(pivot + 4 * atr20, 2)

    # Notes
    notes = []
    if fresh_breakout and vol_mult >= 1.5:
        notes.append("FRESH")
    elif fresh_breakout:
        notes.append("ANTICIPATE")
    if pivot_dist is not None and pivot_dist > 5:
        notes.append("EXTENDED")
    edate = metrics.get("earnings_next_date")
    if edate:
        try:
            dd = (date.fromisoformat(edate) - date.today()).days
            if 6 <= dd <= 10:
                notes.append("EARN-SOON")
        except Exception:
            pass

    return {
        **metrics,
        "score": score,
        "breakdown": breakdown,
        "action": action,
        "vol_multiplier": round(vol_mult, 2),
        "today_close": today_close,
        "today_vol": today_vol,
        "pct_to_pivot": round(pivot_dist, 2) if pivot_dist is not None else None,
        "stop": stop, "t1": t1, "t2": t2,
        "notes": notes,
        "fresh_breakout": fresh_breakout,
    }


def classify_action(score: float, fresh: bool, near_pivot: bool,
                     pivot_dist: Optional[float]) -> str:
    near5 = pivot_dist is not None and -5 <= pivot_dist <= 5
    if score >= 80 and fresh:
        return "STRONG_BUY"
    if score >= 80 and near_pivot and not fresh:
        return "PRIME_WATCH"
    if score >= 65 and fresh:
        return "BUY"
    if score >= 65 and near5:
        return "WATCH"
    if score >= 50:
        return "MONITOR"
    return "PASS"


# ---------- Market Context ----------
def compute_market_context() -> dict:
    """Compute BIST + US market context (50DMA + distribution day count)."""
    out = {}
    for label, sym in [("BIST", "XU030.IS"), ("US", "^GSPC")]:
        try:
            df = batch_download_daily([sym], period="1y", interval="1d", batch_size=1, use_cache=True).get(sym)
            if df is None or len(df) < 60:
                out[label] = {"status": "NO_DATA"}
                continue
            c = _series(df, "close")
            v = _series(df, "volume")
            sma50 = sma(c, 50).iloc[-1]
            healthy = c.iloc[-1] > sma50 if pd.notna(sma50) else False
            # Distribution days in last 25 sessions
            dd_count = 0
            for i in range(-25, 0):
                try:
                    pct = (c.iloc[i] / c.iloc[i - 1] - 1) * 100
                    if pct < -0.2 and v.iloc[i] > v.iloc[i - 1]:
                        dd_count += 1
                except Exception:
                    continue
            out[label] = {
                "status": "HEALTHY" if healthy else "DEFENSIVE",
                "distribution_days_25d": dd_count,
                "under_pressure": dd_count > 5,
                "last": float(c.iloc[-1]),
                "sma50": float(sma50) if pd.notna(sma50) else None,
            }
        except Exception as e:
            out[label] = {"status": "ERROR", "err": str(e)}
    out["computed_at"] = datetime.now().isoformat(timespec="seconds")
    MARKET_CTX_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def run_hourly_scan() -> dict:
    """Mode B: load pool, fetch intraday for each, score, persist results."""
    log.info("=== MODE B: Hourly Scan start ===")
    if not POOL_PATH.exists():
        log.warning("no pool found, run daily refresh first")
        return {"error": "no_pool"}
    pool_data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    pool = pool_data.get("pool", [])
    if not pool:
        return {"scored_at": datetime.now().isoformat(), "rows": [], "market_context": {}}

    # Bulk download today's intraday + ensure fresh daily
    syms = [m["symbol"] for m in pool]
    log.info("fetching intraday for %d candidates...", len(syms))
    intraday_map = batch_download_daily(syms, period="5d", interval="1h",
                                          batch_size=50, use_cache=False)
    daily_map = batch_download_daily(syms, period="1y", interval="1d",
                                       batch_size=50, use_cache=True)

    rows = []
    for m in pool:
        sym = m["symbol"]
        intra = intraday_map.get(sym)
        daily = daily_map.get(sym)
        try:
            scored = score_one(m, intra, daily)
            rows.append(scored)
        except Exception as e:
            log.warning("score fail %s: %s", sym, e)

    # Sort by score desc
    rows.sort(key=lambda r: r["score"], reverse=True)
    mkt = compute_market_context()

    out = {
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "pool_refreshed_at": pool_data.get("generated_at"),
        "pool_size": len(pool),
        "market_context": mkt,
        "rows": rows,
    }
    SCORES_PATH.write_text(json.dumps(out, default=str, indent=2), encoding="utf-8")
    log.info("=== Hourly Scan done: %d scored ===", len(rows))
    return out
