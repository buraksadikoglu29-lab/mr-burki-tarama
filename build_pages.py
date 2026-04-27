"""Build the static GitHub Pages dashboard from current scan data.

Reads scored_results.json + candidate_pool.json from $SCANNER_BASE/cache/,
embeds them into the HTML template, writes to $SCANNER_BASE/docs/index.html.
This file is committed back to the repo and served by GitHub Pages.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

BASE = Path(os.environ.get("SCANNER_BASE", Path(__file__).parent))
CACHE = BASE / "cache"
DOCS = BASE / "docs"
TEMPLATE = Path(__file__).parent / "artifact_template.html"
OUT = DOCS / "index.html"

KEEP = {
    "symbol", "display", "asset_type", "market", "tv_symbol", "last_price",
    "pivot_price", "base_pattern", "base_length_weeks", "base_depth_pct",
    "vcp_contractions", "rs_3m_percentile", "rs_6m_percentile",
    "avg_volume_50d", "bb_squeeze_recent", "earnings_next_date", "atr_20",
    "slope_50dma_3m_pct", "score", "breakdown", "action", "vol_multiplier",
    "today_close", "pct_to_pivot", "stop", "t1", "t2", "notes", "fresh_breakout",
}


def main():
    scores_p = CACHE / "scored_results.json"
    pool_p = CACHE / "candidate_pool.json"
    if not scores_p.exists():
        print(f"ERROR: {scores_p} missing — run scanner first", file=sys.stderr)
        sys.exit(1)
    d = json.loads(scores_p.read_text(encoding="utf-8"))
    pool_meta = {}
    if pool_p.exists():
        p = json.loads(pool_p.read_text(encoding="utf-8"))
        pool_meta = {
            "size": p.get("pool_size"), "rejected": p.get("rejected_count"),
            "generated_at": p.get("generated_at"), "total_tickers": p.get("total_tickers"),
        }
    snap = {
        "scored_at": d.get("scored_at"),
        "pool_refreshed_at": d.get("pool_refreshed_at"),
        "pool_size": d.get("pool_size"),
        "market_context": d.get("market_context"),
        "rows": [{k: r.get(k) for k in KEEP if k in r} for r in d.get("rows", [])],
        "pool_meta": pool_meta,
    }
    if not TEMPLATE.exists():
        print(f"ERROR: template missing at {TEMPLATE}", file=sys.stderr)
        sys.exit(1)
    html = TEMPLATE.read_text(encoding="utf-8")
    out = html.replace("__SNAPSHOT_JSON__", json.dumps(snap, separators=(",", ":"), default=str))
    DOCS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    print(f"Wrote {OUT} ({len(out):,} bytes, {len(snap['rows'])} rows)")


if __name__ == "__main__":
    main()
