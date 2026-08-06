"""Package extracted images into baseline/delta zips + final index.json.

Reads the Phase B/C index (original + large + preview), injects frozen-schema
fields (schemaVersion, baselineVersion, sinceVersion, shards), and produces:

- **Baseline** (first run or re-baseline): 6 shard zips (shard × variant)
- **Delta** (subsequent runs): 1 zip with only new/changed PNGs
- **Sentinel delta** (after baseline): 0-PNG delta so every version has a Release

The final ``index.json`` is written to *dist_dir* with all injected fields.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .images_diff import compute_delta
from .images_index import write_index_atomic

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = "akdp-images/v1"
_VARIANTS: tuple[str, ...] = ("original", "large", "preview")
_SHARDS: tuple[str, ...] = ("chararts", "skinpack")

#: Fixed ZIP timestamp for reproducible archives (same convention as package.py).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass
class PackageStats:
    mode: str = ""  # "baseline" or "delta"
    baseline_version: str = ""
    delta_added: int = 0
    delta_changed: int = 0
    delta_removed: int = 0
    delta_empty: bool = False
    shards: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "baselineVersion": self.baseline_version,
            "delta": {
                "added": self.delta_added,
                "changed": self.delta_changed,
                "removed": self.delta_removed,
                "empty": self.delta_empty,
            },
            "shards": self.shards,
            "totalBytes": self.total_bytes,
        }


def _write_deterministic(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    """Write a file with stable ZIP metadata so identical inputs hash alike."""
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    zf.writestr(info, src.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9)


def _zip_files(files: list[Path], out: Path, arc_prefix: str = "") -> int:
    """Write *files* into a reproducible zip. Returns total bytes."""
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src in sorted(files):
            arcname = f"{arc_prefix}/{src.name}" if arc_prefix else src.name
            _write_deterministic(zf, src, arcname)
            total += src.stat().st_size
    return total


def _build_shard_zips(
    images_dir: Path, dist_dir: Path, index: dict, version_id: str,
) -> tuple[dict, int]:
    """Create 6 baseline shard zips. Returns (shards_map, total_bytes).

    Raises FileNotFoundError if any PNG referenced by the index is missing.
    """
    shards: dict[str, str] = {}
    total = 0
    artworks = index["artworks"]

    for shard in _SHARDS:
        for variant in _VARIANTS:
            entries = {
                sid: entry for sid, entry in artworks.items()
                if entry.get("shard") == shard and variant in entry
            }
            if not entries:
                continue
            files: list[Path] = []
            for entry in entries.values():
                p = images_dir / entry[variant]["file"]
                if not p.exists():
                    raise FileNotFoundError(
                        f"missing {variant} PNG referenced by index: {p}"
                    )
                files.append(p)
            name = f"images-baseline-{shard}-{variant}-{version_id}.zip"
            shard_key = f"{shard}-{variant}"
            shards[shard_key] = name
            total += _zip_files(files, dist_dir / name)
            _logger.info("  shard %s: %d files → %s", shard_key, len(files), name)

    return shards, total


def _build_delta_zip(
    images_dir: Path, dist_dir: Path, index: dict,
    changed_ids: set[str], version_id: str,
) -> int:
    """Create a single delta zip with new/changed PNGs. Returns total bytes."""
    files: list[Path] = []
    artworks = index["artworks"]
    for sid in sorted(changed_ids):
        entry = artworks.get(sid, {})
        for variant in _VARIANTS:
            file_name = entry.get(variant, {}).get("file")
            if file_name:
                p = images_dir / file_name
                if p.exists():
                    files.append(p)

    name = f"images-delta-{version_id}.zip"
    total = _zip_files(files, dist_dir / name)
    _logger.info("  delta: %d files (%.1f MB) → %s", len(files), total / 1e6, name)
    return total


def _inject_since_versions(
    index: dict, prev_index: dict | None, version_id: str,
) -> None:
    """Inject sinceVersion into each artwork entry in-place."""
    prev_artworks = (prev_index or {}).get("artworks", {})
    for sid, entry in index["artworks"].items():
        if sid in prev_artworks:
            entry["sinceVersion"] = prev_artworks[sid].get("sinceVersion", version_id)
        else:
            entry["sinceVersion"] = version_id


def package_images(
    images_dir: Path,
    dist_dir: Path,
    prev_index_path: Path | None,
    version_id: str,
) -> tuple[dict, PackageStats]:
    """Package images for publication.

    Returns (final_index, stats). The final_index is also written to
    ``dist_dir / index.json`` atomically.
    """
    dist_dir.mkdir(parents=True, exist_ok=True)
    raw_index = json.loads((images_dir / "index.json").read_text(encoding="utf-8"))

    prev_index = None
    if prev_index_path and prev_index_path.exists():
        prev_index = json.loads(prev_index_path.read_text(encoding="utf-8"))

    is_baseline = prev_index is None
    stats = PackageStats()

    # Inject sinceVersion.
    _inject_since_versions(raw_index, prev_index, version_id)

    if is_baseline:
        # --- Baseline mode: 6 shard zips + sentinel delta ---
        stats.mode = "baseline"
        stats.baseline_version = version_id
        shards, total = _build_shard_zips(images_dir, dist_dir, raw_index, version_id)
        stats.shards = sorted(shards.keys())
        stats.total_bytes = total

        # Sentinel delta: 0 PNGs, just the index.
        sentinel_name = f"images-delta-{version_id}.zip"
        _zip_files([], dist_dir / sentinel_name)

        raw_index["schemaVersion"] = SCHEMA_VERSION
        raw_index["baselineVersion"] = version_id
        raw_index["shards"] = shards
        raw_index["currentVersion"] = version_id

    else:
        # --- Delta mode ---
        stats.mode = "delta"
        stats.baseline_version = prev_index.get("baselineVersion", version_id)

        delta = compute_delta(raw_index, prev_index)
        changed_ids = delta["added"] | delta["changed"]
        stats.delta_added = len(delta["added"])
        stats.delta_changed = len(delta["changed"])
        stats.delta_removed = len(delta["removed"])
        stats.delta_empty = len(changed_ids) == 0

        total = _build_delta_zip(images_dir, dist_dir, raw_index, changed_ids, version_id)
        stats.total_bytes = total

        raw_index["schemaVersion"] = SCHEMA_VERSION
        raw_index["baselineVersion"] = stats.baseline_version
        raw_index["shards"] = prev_index.get("shards", {})
        raw_index["currentVersion"] = version_id

    # Write final index atomically.
    write_index_atomic(dist_dir / "index.json", raw_index)

    _logger.info(
        "images-package: mode=%s, baseline=%s, %d artworks",
        stats.mode, stats.baseline_version, len(raw_index.get("artworks", {})),
    )
    return raw_index, stats
