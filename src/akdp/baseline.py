"""Baseline handling: download the current distribution releases and extract
them into a single baseline tree (rooted at the dir containing zh_CN/)."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from . import contract


def download_baseline(baseline: Path, *, clobber: bool = False) -> None:
    """Fetch the three current release assets into `baseline` and unzip them."""
    if baseline.exists() and any(baseline.iterdir()):
        if not clobber:
            return
        shutil.rmtree(baseline)
    baseline.mkdir(parents=True)
    staging = baseline / ".zips"
    staging.mkdir()

    for repo, asset in (
        (contract.PACKAGE_GAMEDATA_REPO, contract.EXCEL_ASSET),
        (contract.PACKAGE_GAMEDATA_REPO, contract.LEVELS_ASSET),
        (contract.PACKAGE_STORY_REPO, contract.STORY_ASSET),
    ):
        cmd = ["gh", "release", "download", "-R", repo, "-p", asset,
               "--clobber", "-D", str(staging)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:  # one retry for transient network failures
            proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"baseline download failed: {' '.join(cmd)}\n{proc.stderr}")

    for asset in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
        with zipfile.ZipFile(staging / asset) as zf:
            zf.extractall(baseline)
    shutil.rmtree(staging)
