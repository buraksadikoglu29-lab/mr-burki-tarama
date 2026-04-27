"""Hourly scan entrypoint for launchd. Sends notification on new STRONG_BUY."""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

import os
LOG_FILE = Path(os.environ.get("SCANNER_BASE", "/Users/burak/Desktop/piyasalar")) / "logs" / "hourly.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)

from scanner_core import run_hourly_scan, SCORES_PATH  # noqa: E402
from notify import notify  # noqa: E402

LAST_STRONG_PATH = Path(os.environ.get("SCANNER_BASE", "/Users/burak/Desktop/piyasalar")) / "cache" / "last_strong_set.json"


def _load_prev() -> set[str]:
    if not LAST_STRONG_PATH.exists():
        return set()
    try:
        return set(json.loads(LAST_STRONG_PATH.read_text()))
    except Exception:
        return set()


if __name__ == "__main__":
    # Trading hours guard: only run on weekdays between 10:00-22:00 (covers BIST + US sessions)
    # Skip if --force not given
    from datetime import datetime as _dt
    now = _dt.now()
    if "--force" not in sys.argv:
        if now.weekday() >= 5 or not (10 <= now.hour <= 22):
            print(f"Outside trading hours ({now}); skipping. Use --force to override.")
            sys.exit(0)
    res = run_hourly_scan()
    if "error" in res:
        print(f"ERROR: {res['error']}")
        sys.exit(1)
    rows = res.get("rows", [])
    strong = [r for r in rows if r.get("action") == "STRONG_BUY"]
    cur = {r["symbol"] for r in strong}
    prev = _load_prev()
    new_ones = cur - prev
    if new_ones:
        names = ", ".join(sorted([r["display"] for r in strong if r["symbol"] in new_ones])[:5])
        notify("Mr Burki Tarama", f"{len(new_ones)} new STRONG BUY: {names}",
               f"{len(strong)} total")
    LAST_STRONG_PATH.write_text(json.dumps(list(cur)))
    # Rebuild Cowork artifact HTML with fresh data (no Claude session needed)
    try:
        import build_artifact
        build_artifact.main()
    except Exception as e:
        logging.warning("artifact rebuild failed: %s", e)
    print(f"Scored {len(rows)} | STRONG: {len(strong)} (new: {len(new_ones)})")
