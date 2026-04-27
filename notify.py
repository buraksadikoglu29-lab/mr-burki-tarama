"""macOS notification helper via osascript."""
from __future__ import annotations
import subprocess
from typing import Optional


def notify(title: str, message: str, subtitle: Optional[str] = None) -> bool:
    """Send a macOS notification."""
    try:
        parts = [f'display notification "{message}"', f'with title "{title}"']
        if subtitle:
            parts.append(f'subtitle "{subtitle}"')
        script = " ".join(parts)
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        return True
    except Exception:
        return False
