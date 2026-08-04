"""Publish step: upload the three assets as new releases on the data repos.

Tag conventions (kept compatible with PRTS-MCP sync expectations):
  - 3aKHP/ArknightsGameData:  upstream-<source-sha-or-version>[-vN]
  - 3aKHP/ArknightsStoryJson: gamedata-<source-sha-or-version>[-vN]
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import contract


def _release(repo: str, tag: str, title: str, assets: list[Path], notes: str) -> None:
    subprocess.run(
        ["gh", "release", "create", tag, "-R", repo, "--title", title,
         "--notes", notes, *[str(a) for a in assets]],
        check=True,
    )


def publish(dist: Path, *, source_id: str, title_suffix: str, dry_run: bool = True) -> None:
    """Publish dist assets. dry_run=True only prints what would happen."""
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    notes = "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```"

    plan = [
        (contract.PACKAGE_GAMEDATA_REPO, f"upstream-{source_id}",
         [dist / contract.EXCEL_ASSET, dist / contract.LEVELS_ASSET]),
        (contract.PACKAGE_STORY_REPO, f"gamedata-{source_id}",
         [dist / contract.STORY_ASSET]),
    ]
    for repo, tag, assets in plan:
        if dry_run:
            print(f"[publish:dry-run] {repo} tag={tag} assets={[a.name for a in assets]}")
        else:
            _release(repo, tag, f"Game Data {title_suffix}", assets, notes)
