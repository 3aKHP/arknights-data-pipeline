"""Index generation: scan extracted PNGs → structured index.json.

Reads ``<skinId>.original.png`` files from the extraction output directory,
measures each image (dimensions, file size, SHA-256), cross-references with
``skin_table.json`` for kind/charId classification, and produces a complete
``index.json`` that downstream phases (variants, diff, package) consume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .images_extract import _load_skin_ids

_logger = logging.getLogger(__name__)


def write_index_atomic(path: Path, index: dict) -> None:
    """Write index.json atomically: temp file in same directory, then os.replace.

    A crash during write leaves either the old or the new file, never a
    truncated half-written JSON.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


@dataclass
class IndexStats:
    indexed: int = 0
    skipped: int = 0
    missing_from_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "indexed": self.indexed,
            "skipped": self.skipped,
            "missing_from_tables": len(self.missing_from_tables),
            "missing_samples": self.missing_from_tables[:20],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_skin_id(skin_id: str) -> tuple[str, str]:
    """Determine (kind, shard) from skinId format.

    - ``char_X#N`` (no ``@``) → ("base", "chararts")
    - ``char_X@group#N``      → ("skin", "skinpack")
    """
    if "@" in skin_id:
        return "skin", "skinpack"
    return "base", "chararts"


def generate_index(
    images_dir: Path,
    excel_path: Path,
    *,
    version_id: str = "",
) -> tuple[dict, IndexStats]:
    """Scan *images_dir* for PNGs and produce an index dict.

    Returns (index_dict, stats).  The index follows the schema agreed in
    issue #1: artworks keyed by skinId, each with kind/shard/original metadata.
    """
    valid_skin_ids = _load_skin_ids(excel_path)
    stats = IndexStats()
    artworks: dict[str, dict] = {}

    for png in sorted(images_dir.glob("*.original.png")):
        skin_id = png.stem.removesuffix(".original")
        if skin_id not in valid_skin_ids:
            stats.skipped += 1
            stats.missing_from_tables.append(skin_id)
            continue

        kind, shard = _classify_skin_id(skin_id)
        with Image.open(png) as img:
            w, h = img.size

        artworks[skin_id] = {
            "kind": kind,
            "shard": shard,
            "original": {
                "file": png.name,
                "w": w,
                "h": h,
                "bytes": png.stat().st_size,
                "sha256": _sha256(png),
            },
        }
        stats.indexed += 1

    index = {
        "currentVersion": version_id,
        "artworks": artworks,
    }

    # Coverage check (only meaningful on full runs; incremental runs
    # only extract changed bundles, so gaps are expected).
    missing_coverage = valid_skin_ids - set(artworks)
    if missing_coverage:
        _logger.debug(
            "images-index: %d valid skin IDs have no extracted PNG (expected on incremental runs)",
            len(missing_coverage),
        )

    if not version_id:
        _logger.warning("images-index: versionId is empty (hashes.json not found)")

    _logger.info(
        "images-index: %d indexed, %d skipped, %d missing coverage",
        stats.indexed, stats.skipped, len(missing_coverage),
    )
    return index, stats
