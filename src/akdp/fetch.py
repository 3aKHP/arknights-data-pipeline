"""Fetch step: run arkprts with retries and post-run integrity checks.

arkprts has no retry or integrity verification of its own (truncated CDN
downloads surface as ContentLengthError / AES padding errors), so we wrap it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def run_arkprts(
    extract_root: Path,
    *,
    server: str = "cn",
    attempts: int = 5,
    backoff_seconds: int = 30,
    extra_env: dict | None = None,
) -> None:
    """Run `python -m arkprts.assets` until it succeeds or attempts run out."""
    extract_root.mkdir(parents=True, exist_ok=True)
    last_log = ""
    for i in range(1, attempts + 1):
        proc = subprocess.run(
            [sys.executable, "-m", "arkprts.assets", str(extract_root),
             "--server", server, "--log-level", "INFO"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return
        last_log = (proc.stdout + proc.stderr)[-2000:]
        print(f"[fetch] attempt {i}/{attempts} failed (rc={proc.returncode})", file=sys.stderr)
        if i < attempts:
            time.sleep(backoff_seconds * i)
    raise RuntimeError(f"arkprts failed after {attempts} attempts. Tail:\n{last_log}")


def check_extraction(extract_root: Path, server: str = "cn") -> list[str]:
    """Sanity-check arkprts output; returns a list of problems (empty = OK)."""
    problems: list[str] = []
    gamedata = extract_root / server / "gamedata"
    if not gamedata.is_dir():
        return [f"missing extraction root: {gamedata}"]
    excel = gamedata / "excel"
    if not excel.is_dir() or not any(excel.glob("*.json")):
        problems.append(f"no excel JSON extracted under {excel}")
    if not (extract_root / server / "hot_update_list.json").exists():
        problems.append("hot_update_list.json missing (version provenance unavailable)")
    return problems
