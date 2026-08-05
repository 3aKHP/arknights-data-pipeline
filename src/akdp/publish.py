"""Publish step: upload the three assets as a single release on this repo.

The factory repo (3aKHP/arknights-data-pipeline) is its own distribution
point — one Release per game version, carrying all three zips as assets.

Tag convention: data-<versionId> (e.g. data-26-08-03-23-34-20_a745fc)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import contract

#: this repo is the distribution repo
DIST_REPO = "3aKHP/arknights-data-pipeline"
TAG_PREFIX = "data-"

#: all three assets go into a single release
RELEASE_ASSETS = [
    contract.EXCEL_ASSET,
    contract.LEVELS_ASSET,
    contract.STORY_ASSET,
]


def _release(tag: str, title: str, assets: list[Path], notes: str, repo: str) -> None:
    cmd = [
        "gh", "release", "create", tag,
        "-R", repo,
        "--title", title,
        "--notes", notes,
        *[str(a) for a in assets],
    ]
    subprocess.run(cmd, check=True)


def publish(dist: Path, *, source_id: str, title_suffix: str, dry_run: bool = True) -> None:
    """Publish dist assets. dry_run=True only prints what would happen."""
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    notes = "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```"

    tag = f"{TAG_PREFIX}{source_id}"
    asset_paths = [dist / name for name in RELEASE_ASSETS]
    missing = [p.name for p in asset_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing dist assets: {missing}")

    if dry_run:
        print(f"[publish:dry-run] {DIST_REPO} tag={tag}")
        print(f"  assets: {[p.name for p in asset_paths]}")
        print(f"  title:  Game Data {title_suffix}")
    else:
        _release(tag, f"Game Data {title_suffix}", asset_paths, notes, DIST_REPO)
        print(f"[publish] created {tag} on {DIST_REPO}")
