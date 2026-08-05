"""Package the candidate tree into the three distribution zips + manifest."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from . import __version__, contract


def _zip_tree(src_root: Path, arc_prefix: str, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(src_root.rglob("*")):
            if p.is_file():
                zf.write(p, f"{arc_prefix}/{p.relative_to(src_root)}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_candidate(
    candidate: Path,
    dist: Path,
    *,
    source_info: dict | None = None,
    merge_stats: dict | None = None,
    validation: dict | None = None,
    story_stats: dict | None = None,
    summarize_stats: dict | None = None,
    tool_versions: dict | None = None,
) -> dict:
    """Build zh_CN-excel.zip / zh_CN-levels.zip / zh_CN.zip + manifest.json.

    Returns the manifest dict.
    """
    zh = candidate / "zh_CN"
    dist.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Path] = {
        contract.EXCEL_ASSET: dist / contract.EXCEL_ASSET,
        contract.LEVELS_ASSET: dist / contract.LEVELS_ASSET,
        contract.STORY_ASSET: dist / contract.STORY_ASSET,
    }

    _zip_tree(zh / "gamedata/excel", "zh_CN/gamedata/excel", assets[contract.EXCEL_ASSET])
    _zip_tree(zh / "gamedata/levels", "zh_CN/gamedata/levels", assets[contract.LEVELS_ASSET])

    # story zip = full excel + story JSONs + ASTR index files
    with zipfile.ZipFile(assets[contract.STORY_ASSET], "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted((zh / "gamedata/excel").rglob("*")):
            if p.is_file():
                zf.write(p, f"zh_CN/gamedata/excel/{p.relative_to(zh / 'gamedata/excel')}")
        story_dir = zh / "gamedata/story"
        if story_dir.exists():
            for p in sorted(story_dir.rglob("*.json")):
                zf.write(p, f"zh_CN/gamedata/story/{p.relative_to(story_dir)}")
        for name in contract.STORY_INDEX_FILES:
            p = zh / name
            if p.exists():
                zf.write(p, f"zh_CN/{name}")

    manifest = {
        "pipelineVersion": __version__,
        "source": source_info or {},
        "tools": tool_versions or {},
        "merge": merge_stats or {},
        "story": story_stats or {},
        "summarize": summarize_stats or {},
        "validation": validation or {},
        "assets": {
            name: {"sha256": _sha256(p), "size": p.stat().st_size}
            for name, p in assets.items()
        },
    }
    (dist / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
