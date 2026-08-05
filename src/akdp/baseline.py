"""Baseline handling: download the current factory release and extract it.

Primary source: the factory repo's latest Release (single release, three assets).
Fallback (first run only): the legacy fork repos, which have two separate releases.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from . import contract
from .publish import DIST_REPO


def _gh_download(repo: str, asset: str, dest: Path) -> None:
    cmd = ["gh", "release", "download", "-R", repo, "-p", asset, "--clobber", "-D", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # one retry
    if proc.returncode != 0:
        raise RuntimeError(f"baseline download failed: {' '.join(cmd)}\n{proc.stderr}")


def _factory_release_exists() -> bool:
    proc = subprocess.run(
        ["gh", "release", "view", "-R", DIST_REPO, "--json", "tagName"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def download_baseline(baseline: Path, *, clobber: bool = False) -> None:
    """Fetch the three release assets into `baseline` and unzip them."""
    if baseline.exists() and any(baseline.iterdir()):
        if not clobber:
            return
        shutil.rmtree(baseline)
    baseline.mkdir(parents=True)
    staging = baseline / ".zips"
    staging.mkdir()

    use_factory = _factory_release_exists()
    if use_factory:
        for asset in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
            _gh_download(DIST_REPO, asset, staging)
    else:
        # First-run fallback: legacy fork repos (two releases, same asset names)
        _gh_download(contract.PACKAGE_GAMEDATA_REPO, contract.EXCEL_ASSET, staging)
        _gh_download(contract.PACKAGE_GAMEDATA_REPO, contract.LEVELS_ASSET, staging)
        _gh_download(contract.PACKAGE_STORY_REPO, contract.STORY_ASSET, staging)

    for asset in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
        with zipfile.ZipFile(staging / asset) as zf:
            zf.extractall(baseline)
    shutil.rmtree(staging)
