"""Generate the Cowork artifact HTML with current scan data embedded.

Reads scored_results.json + candidate_pool.json, builds the full self-contained
HTML, writes to the Cowork artifact path. Idempotent — safe to call hourly from
launchd. No Claude session needed.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

CACHE = Path("/Users/burak/Desktop/piyasalar/cache")
SCORES = CACHE / "scored_results.json"
POOL = CACHE / "candidate_pool.json"
ARTIFACT_DIR = Path("/Users/burak/Documents/Claude/Artifacts/scanner_top50")
ARTIFACT_HTML = ARTIFACT_DIR / "index.html"
TEMPLATE = Path("/Users/burak/Desktop/piyasalar/app/artifact_template.html")

KEEP = {
    "symbol", "display", "asset_type", "market", "tv_symbol", "last_price",
    "pivot_price", "base_pattern", "base_length_weeks", "base_depth_pct",
    "vcp_contractions", "rs_3m_percentile", "rs_6m_percentile",
    "avg_volume_50d", "bb_squeeze_recent", "earnings_next_date", "atr_20",
    "slope_50dma_3m_pct", "score", "breakdown", "action", "vol_multiplier",
    "today_close", "pct_to_pivot", "stop", "t1", "t2", "notes", "fresh_breakout",
}


def build_snapshot() -> dict:
    if not SCORES.exists():
        raise FileNotFoundError(f"scored_results not found: {SCORES}")
    d = json.loads(SCORES.read_text(encoding="utf-8"))
    pool_meta = {}
    if POOL.exists():
        p = json.loads(POOL.read_text(encoding="utf-8"))
        pool_meta = {
            "size": p.get("pool_size"),
            "rejected": p.get("rejected_count"),
            "generated_at": p.get("generated_at"),
            "total_tickers": p.get("total_tickers"),
        }
    slim_rows = [{k: r.get(k) for k in KEEP if k in r} for r in d.get("rows", [])]
    return {
        "scored_at": d.get("scored_at"),
        "pool_refreshed_at": d.get("pool_refreshed_at"),
        "pool_size": d.get("pool_size"),
        "market_context": d.get("market_context"),
        "rows": slim_rows,
        "pool_meta": pool_meta,
    }


def main():
    # Skip on non-Mac (e.g. GitHub Actions Linux) — there's no Cowork artifact path
    if sys.platform != "darwin":
        print("build_artifact: skipping (non-Mac platform)")
        return
    if not TEMPLATE.exists():
        print(f"ERROR: template missing at {TEMPLATE}", file=sys.stderr)
        return
    snap = build_snapshot()
    snap_json = json.dumps(snap, separators=(",", ":"), default=str)
    html = TEMPLATE.read_text(encoding="utf-8")
    if "__SNAPSHOT_JSON__" not in html:
        print("ERROR: template has no __SNAPSHOT_JSON__ placeholder", file=sys.stderr)
        sys.exit(1)
    out = html.replace("__SNAPSHOT_JSON__", snap_json)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_HTML.write_text(out, encoding="utf-8")
    print(f"Wrote {ARTIFACT_HTML} ({len(out):,} bytes, {len(snap['rows'])} rows)")


if __name__ == "__main__":
    main()
