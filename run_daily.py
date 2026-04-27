"""Daily refresh entrypoint for launchd."""
from __future__ import annotations
import logging
import sys
from pathlib import Path

import os
LOG_FILE = Path(os.environ.get("SCANNER_BASE", "/Users/burak/Desktop/piyasalar")) / "logs" / "daily.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)

from scanner_core import run_daily_refresh  # noqa: E402

if __name__ == "__main__":
    use_cache = "--no-cache" not in sys.argv
    res = run_daily_refresh(use_cache=use_cache)
    print(f"Pool size: {res.get('pool_size')}, rejected: {res.get('rejected_count')}")
    # After daily refresh, also rerun hourly + rebuild artifact
    try:
        from scanner_core import run_hourly_scan
        import build_artifact
        run_hourly_scan()
        build_artifact.main()
    except Exception as e:
        logging.warning("post-daily rebuild failed: %s", e)
