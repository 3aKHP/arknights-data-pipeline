"""Variant generation: produce large/preview PNGs from originals + update index.

For each artwork in the index, generates two additional resolution tiers:

- ``large``   — longest side capped at 1024 px (for MCP transport / display)
- ``preview`` — longest side capped at 256 px (for list / thumbnail views)

Both use Lanczos resampling and never upscale.  The index is updated in-place
to include metadata (dimensions, bytes, SHA-256) for each variant.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

_logger = logging.getLogger(__name__)

LARGE_MAX = 1024
PREVIEW_MAX = 256


@dataclass
class VariantStats:
    generated: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated": self.generated,
            "skipped": self.skipped,
            "failed": len(self.failed),
            "failed_details": self.failed[:20],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_variant(src: Path, dst: Path, max_side: int) -> bool:
    """Resize *src* so the longest side ≤ *max_side* and save to *dst*.

    Never upscales — if the original is already smaller, it is copied as-is.
    Returns True on success, False on failure.
    """
    try:
        with Image.open(src) as img:
            w, h = img.size
            if max(w, h) <= max_side:
                dst.write_bytes(src.read_bytes())
            else:
                thumb = img.copy()
                thumb.thumbnail((max_side, max_side), Image.LANCZOS)
                thumb.save(dst, format="PNG")
        return True
    except Exception:
        return False


def _measure(path: Path) -> dict:
    """Return file/w/h/bytes/sha256 for a PNG file."""
    with Image.open(path) as img:
        w, h = img.size
    return {
        "file": path.name,
        "w": w,
        "h": h,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def generate_variants(images_dir: Path, index_path: Path) -> VariantStats:
    """Generate large/preview PNGs and update the index on disk.

    Reads ``index.json`` from *index_path*, generates variants for each
    artwork's ``original`` file in *images_dir*, writes variant metadata back
    into the index, and saves the updated index.
    """
    index = json.loads(index_path.read_text(encoding="utf-8"))
    artworks = index.get("artworks", {})
    stats = VariantStats()

    for skin_id, entry in artworks.items():
        orig_file = entry.get("original", {}).get("file")
        if not orig_file:
            stats.skipped += 1
            continue
        orig_path = images_dir / orig_file
        if not orig_path.exists():
            stats.skipped += 1
            continue

        for tier, max_side in (("large", LARGE_MAX), ("preview", PREVIEW_MAX)):
            dst = images_dir / f"{skin_id}.{tier}.png"
            if not _make_variant(orig_path, dst, max_side):
                stats.failed.append(f"{skin_id}.{tier}")
                continue
            entry[tier] = _measure(dst)

        stats.generated += 1

    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _logger.info(
        "images-variants: %d generated, %d skipped, %d failed",
        stats.generated, stats.skipped, len(stats.failed),
    )
    return stats


def compute_delta(
    current: dict, previous: dict | None,
) -> dict[str, set[str]]:
    """Compare two indexes and return the delta set.

    Returns ``{"added": {...}, "changed": {...}, "removed": {...}}`` where each
    set contains skin IDs.  A skin is *changed* if its ``original.sha256``
    differs between the two indexes.
    """
    prev_artworks = (previous or {}).get("artworks", {})
    curr_artworks = current.get("artworks", {})

    prev_ids = set(prev_artworks)
    curr_ids = set(curr_artworks)

    added = curr_ids - prev_ids
    removed = prev_ids - curr_ids

    changed: set[str] = set()
    for sid in curr_ids & prev_ids:
        prev_hash = prev_artworks[sid].get("original", {}).get("sha256")
        curr_hash = curr_artworks[sid].get("original", {}).get("sha256")
        if prev_hash != curr_hash:
            changed.add(sid)

    return {"added": added, "changed": changed, "removed": removed}
