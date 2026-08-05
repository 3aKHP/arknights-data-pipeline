"""Variant generation: produce large/preview PNGs from originals + update index.

For each artwork in the index, generates two additional resolution tiers:

- ``large``   — longest side capped at 1024 px (for MCP transport / display)
- ``preview`` — longest side capped at 256 px (for list / thumbnail views)

Both use Lanczos resampling and never upscale.  The index is updated
atomically — the on-disk index is replaced only when *every* artwork has
complete large + preview metadata.  Any failure preserves the previous
valid index.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .images_index import write_index_atomic

_logger = logging.getLogger(__name__)

LARGE_MAX = 1024
PREVIEW_MAX = 256


@dataclass
class VariantStats:
    #: artworks where BOTH large and preview were generated successfully
    generated: int = 0
    #: list of ``{"skin_id": …, "tier": …, "error": …}``
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated": self.generated,
            "failed": len(self.failed),
            "failed_details": self.failed[:20],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_variant(src: Path, dst: Path, max_side: int) -> None:
    """Resize *src* so the longest side ≤ *max_side* and save to *dst*.

    Never upscales.  Raises on any I/O or decode error — callers must catch.
    """
    with Image.open(src) as img:
        w, h = img.size
        if max(w, h) <= max_side:
            dst.write_bytes(src.read_bytes())
        else:
            thumb = img.copy()
            thumb.thumbnail((max_side, max_side), Image.LANCZOS)
            thumb.save(dst, format="PNG")


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
    """Generate large/preview PNGs and atomically update the index.

    The index is replaced only if **every** artwork produces complete large +
    preview.  Any failure leaves the previous index untouched.

    Returns stats; ``stats.failed`` non-empty means the index was not advanced.
    """
    index = json.loads(index_path.read_text(encoding="utf-8"))
    artworks = index.get("artworks", {})
    stats = VariantStats()

    # Build updated entries in a staging dict — don't mutate *index* until
    # we know every artwork succeeded.
    staged: dict[str, dict] = {}

    for skin_id, entry in artworks.items():
        orig_file = entry.get("original", {}).get("file")
        if not orig_file:
            stats.failed.append({"skin_id": skin_id, "tier": "original", "error": "missing original.file in index"})
            continue
        orig_path = images_dir / orig_file
        if not orig_path.exists():
            stats.failed.append({"skin_id": skin_id, "tier": "original", "error": f"file not found: {orig_path}"})
            continue

        new_entry = dict(entry)  # shallow copy
        tier_failed = False
        for tier, max_side in (("large", LARGE_MAX), ("preview", PREVIEW_MAX)):
            dst = images_dir / f"{skin_id}.{tier}.png"
            try:
                _make_variant(orig_path, dst, max_side)
                new_entry[tier] = _measure(dst)
            except Exception as exc:  # noqa: BLE001
                stats.failed.append({
                    "skin_id": skin_id,
                    "tier": tier,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                tier_failed = True
                break

        if not tier_failed:
            staged[skin_id] = new_entry
            stats.generated += 1

    # Only advance the index if there were zero failures.
    if stats.failed:
        _logger.warning(
            "images-variants: %d failures — index NOT advanced (preserving previous valid index)",
            len(stats.failed),
        )
        return stats

    # All succeeded — merge staged entries and write atomically.
    artworks.update(staged)
    write_index_atomic(index_path, index)
    _logger.info("images-variants: %d generated, index updated atomically", stats.generated)
    return stats
