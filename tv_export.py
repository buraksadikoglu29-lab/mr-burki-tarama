"""TradingView watchlist .txt export."""
from __future__ import annotations
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

EXPORT_DIR = Path("/Users/burak/Desktop/piyasalar/exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LATEST = EXPORT_DIR / "mr_burki_tarama_LATEST.txt"

ACTION_ORDER = ["STRONG_BUY", "BUY", "PRIME_WATCH", "WATCH", "MONITOR"]
ACTION_LABELS = {
    "STRONG_BUY": "STRONG BUY",
    "BUY": "BUY",
    "PRIME_WATCH": "PRIME WATCH",
    "WATCH": "WATCH",
    "MONITOR": "MONITOR",
}


def build_tv_text(rows: List[dict], include_actions: List[str] | None = None) -> str:
    """Group rows by action (in defined order), output ###SECTION + comma-sep tickers."""
    if include_actions is None:
        include_actions = ["STRONG_BUY", "BUY", "PRIME_WATCH", "WATCH"]
    # Already sorted by score desc upstream
    by_action: dict[str, List[str]] = {a: [] for a in ACTION_ORDER}
    for r in rows:
        a = r.get("action")
        if a in by_action and a in include_actions:
            by_action[a].append(r.get("tv_symbol") or r.get("display") or r.get("symbol"))

    lines: List[str] = []
    for a in ACTION_ORDER:
        if a not in include_actions:
            continue
        tickers = by_action.get(a, [])
        if not tickers:
            continue
        lines.append(f"###{ACTION_LABELS[a]}")
        lines.append(",".join(tickers))
    return "\n".join(lines) + ("\n" if lines else "")


def export_to_files(rows: List[dict], include_actions: List[str] | None = None,
                     reveal_in_finder: bool = True) -> dict:
    """Write timestamped + LATEST .txt; optionally reveal in Finder. Returns paths + count."""
    text = build_tv_text(rows, include_actions)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    timestamped = EXPORT_DIR / f"mr_burki_tarama_{ts}.txt"
    timestamped.write_text(text, encoding="utf-8")
    LATEST.write_text(text, encoding="utf-8")

    if reveal_in_finder:
        try:
            subprocess.run(["open", "-R", str(timestamped)], check=False)
        except Exception:
            pass

    # Count
    count = sum(1 for line in text.splitlines() if not line.startswith("###") and line.strip())
    ticker_count = sum(len(line.split(",")) for line in text.splitlines()
                        if not line.startswith("###") and line.strip())
    return {
        "timestamped_path": str(timestamped),
        "latest_path": str(LATEST),
        "ticker_count": ticker_count,
        "text": text,
    }


def copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS clipboard via pbcopy."""
    try:
        proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return proc.returncode == 0
    except Exception:
        return False
