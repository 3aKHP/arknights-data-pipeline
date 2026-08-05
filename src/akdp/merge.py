"""Merge baseline release tree with a fresh extraction into a candidate tree.

Policy: file-level overlay accumulation.
  - Files present in the new extraction overwrite the baseline (new wins).
  - Files only in the baseline are kept (client removed them, we accumulate).
  - Files only in the extraction are added.
Deletions never happen automatically; that is the whole point of the
cumulative tree (HG removes old event content from the hot-update list).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MergeStats:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: int = 0
    kept_from_baseline: int = 0  # baseline-only files (client-removed content)

    def to_dict(self) -> dict:
        return {
            "added": len(self.added),
            "changed": len(self.changed),
            "unchanged": self.unchanged,
            "kept_from_baseline": self.kept_from_baseline,
            "added_files": self.added[:50],
            "changed_files": self.changed[:50],
        }


def _files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()}


def _same_file(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca, cb = fa.read(1 << 20), fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def merge_trees(baseline: Path, extraction: Path, candidate: Path) -> MergeStats:
    """Overlay `extraction` onto `baseline`, writing the result to `candidate`."""
    stats = MergeStats()
    if candidate.exists():
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True)

    base_files = _files(baseline) if baseline.exists() else {}
    new_files = _files(extraction)

    for rel, src in new_files.items():
        dst = candidate / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        old = base_files.get(rel)
        if old is None:
            stats.added.append(rel)
        elif _same_file(old, src):
            stats.unchanged += 1
        else:
            stats.changed.append(rel)

    for rel, src in base_files.items():
        if rel not in new_files:
            dst = candidate / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            stats.kept_from_baseline += 1

    stats.added.sort()
    stats.changed.sort()
    return stats


def load_hot_update_version(extract_root: Path) -> dict:
    """Extract version info from arkprts' hot_update_list.json for the manifest."""
    for hul in extract_root.rglob("hot_update_list.json"):
        try:
            data = json.loads(hul.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        return {
            "path": hul.relative_to(extract_root).as_posix(),
            "versionId": data.get("versionId"),
            "manifestVersion": data.get("manifestVersion"),
            "abCount": len(data.get("abInfos") or []),
        }
    return {}
