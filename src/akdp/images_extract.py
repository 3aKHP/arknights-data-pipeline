"""Extract step for image bundles: UnityPy AB → filtered Sprite PNGs.

Loads each downloaded AB bundle with UnityPy, extracts Texture2D/Sprite objects,
and saves only those whose name maps to a valid operator skin in
``skin_table.json`` / ``character_table.json``.  Everything else (avatars,
building sprites, alpha companions, tokens) is discarded immediately.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import UnityPy
from PIL import Image
from UnityPy.helpers import CompressionHelper
from UnityPy.enums.BundleFile import CompressionFlags

# arkprts patches LZHAM decompression; replicate for standalone UnityPy.
from arkprts.assets.bundle import decompress_lz4ak

CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak

_logger = logging.getLogger(__name__)


def _load_skin_ids(excel_path: Path) -> set[str]:
    """Return the set of valid skinIds from skin_table.json, filtered to
    operators present in character_table.json."""
    skin_table_path = excel_path / "skin_table.json"
    char_table_path = excel_path / "character_table.json"
    if not skin_table_path.is_file():
        raise FileNotFoundError(f"skin_table.json not found at {skin_table_path}")
    if not char_table_path.is_file():
        raise FileNotFoundError(f"character_table.json not found at {char_table_path}")

    skin_table = json.loads(skin_table_path.read_text(encoding="utf-8"))
    char_table = json.loads(char_table_path.read_text(encoding="utf-8"))
    char_ids = {cid for cid in char_table if cid.startswith("char_")}

    skins = skin_table.get("charSkins", {})
    valid: set[str] = set()
    for skin_id, info in skins.items():
        char_id = info.get("charId", "")
        if char_id in char_ids and not skin_id.startswith("token_"):
            valid.add(skin_id)
    return valid


def _tex_name_to_skin_id(tex_name: str) -> str | None:
    """Convert a UnityPy texture/sprite name to a skinId.

    Texture names in AB bundles:
      char_002_amiya_2           → char_002_amiya#2
      char_002_amiya_epoque#4    → char_002_amiya@epoque#4
      char_002_amiya             → avatar (no skin suffix, return None)
      build_char_002_amiya       → building sprite (return None)
      char_002_amiya[alpha]      → alpha companion (return None)
    """
    if tex_name.startswith("build_") or "[alpha]" in tex_name:
        return None

    # Skin-group textures: char_002_amiya_epoque#4
    if "#" in tex_name:
        # Reconstruct: char_002_amiya_epoque#4 → char_002_amiya@epoque#4
        parts = tex_name.split("_", 2)  # ['char', '002', 'amiya_epoque#4']
        if len(parts) < 3:
            return None
        rest = parts[2]  # amiya_epoque#4
        # Find the last _ that separates operator name from skin group
        idx = rest.rfind("_")
        if idx < 0:
            return None
        op_part = rest[:idx]      # amiya
        skin_part = rest[idx + 1:]  # epoque#4
        return f"char_{parts[1]}_{op_part}@{skin_part}"

    # Base art: char_002_amiya_2 → char_002_amiya#2
    # char_002_amiya_1+  → char_002_amiya#1+  (rare: E1 art, only Amiya)
    m = re.match(r"^(char_\d+_[a-z0-9]+)_(\d+\+?)$", tex_name)
    if not m:
        return None
    return f"{m.group(1)}#{m.group(2)}"


@dataclass
class ExtractStats:
    extracted: list[str] = field(default_factory=list)
    skipped_not_skin: int = 0
    failed: list[dict] = field(default_factory=list)
    bundles_processed: int = 0

    def to_dict(self) -> dict:
        return {
            "extracted": len(self.extracted),
            "skipped_not_skin": self.skipped_not_skin,
            "failed": len(self.failed),
            "bundles_processed": self.bundles_processed,
            "failed_details": self.failed[:20],
        }


def _extract_bundle(
    ab_data: bytes, valid_skin_ids: set[str], out_dir: Path
) -> tuple[list[str], int, list[dict]]:
    """Extract Sprite PNGs from one AB bundle.

    Returns (extracted skin IDs, count of skipped non-skin objects, failures).
    Sprite is preferred over Texture2D (Sprite is the tightly cropped art;
    Texture2D is the power-of-2 atlas with transparent padding).  Texture2D
    is used as a fallback when no Sprite exists for a given skin_id; a later
    Sprite overwrites the earlier Texture2D output.
    """
    extracted: list[str] = []
    skipped = 0
    failures: list[dict] = []

    try:
        env = UnityPy.load(io.BytesIO(ab_data))
    except Exception as exc:  # noqa: BLE001
        return extracted, skipped, [{"bundle": "<unknown>", "error": f"{type(exc).__name__}: {exc}"}]

    for obj in env.objects:
        if obj.type.name not in ("Texture2D", "Sprite"):
            continue
        name = ""
        try:
            data = obj.read()
            name = getattr(data, "m_Name", None) or ""
            if not name:
                skipped += 1
                continue

            skin_id = _tex_name_to_skin_id(name)
            if skin_id is None or skin_id not in valid_skin_ids:
                skipped += 1
                continue

            # Skip Texture2D if a Sprite for this skin_id was already saved.
            if obj.type.name == "Texture2D" and skin_id in extracted:
                continue

            img: Image.Image = data.image

            # @, #, + are valid in Linux/Windows filenames and ZIP entries.
            out_path = out_dir / f"{skin_id}.original.png"
            img.save(out_path, format="PNG")
            if skin_id not in extracted:
                extracted.append(skin_id)
        except Exception as exc:  # noqa: BLE001
            failures.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
            skipped += 1

    return extracted, skipped, failures


def extract_images(
    cache_dir: Path,
    out_dir: Path,
    excel_path: Path,
) -> ExtractStats:
    """Extract all cached AB bundles into PNG files.

    *cache_dir* contains ``*.ab`` files from ``images_fetch``.
    *out_dir* receives ``<skinId>.original.png`` files.
    *excel_path* points at the gamedata excel directory (for skin_table).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    valid_skin_ids = _load_skin_ids(excel_path)
    _logger.info("images-extract: %d valid skin IDs", len(valid_skin_ids))

    stats = ExtractStats()

    ab_files = sorted(cache_dir.glob("*.ab"))
    for ab_path in ab_files:
        stats.bundles_processed += 1
        try:
            ab_data = ab_path.read_bytes()
        except OSError as exc:
            stats.failed.append({"bundle": ab_path.name, "error": str(exc)})
            continue

        extracted, skipped, failures = _extract_bundle(
            ab_data, valid_skin_ids, out_dir
        )
        stats.extracted.extend(extracted)
        stats.skipped_not_skin += skipped
        stats.failed.extend(failures)

    stats.extracted.sort()
    _logger.info(
        "images-extract: %d sprites extracted, %d skipped, %d failed (%d bundles)",
        len(stats.extracted), stats.skipped_not_skin, len(stats.failed),
        stats.bundles_processed,
    )
    return stats
