"""Normalize arkprts extraction into the zh_CN layout the contract expects.

arkprts outputs `<root>/<server>/gamedata/...` plus aux dirs (config/, i18n/,
hot_update_list.json). The distribution contract is `zh_CN/gamedata/...`.
This step produces a normalized view containing only gamedata, with exclusions
applied.
"""

from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

#: paths under gamedata/ excluded from the normalized tree (glob patterns)
EXCLUSIONS: list[str] = []
POLICY_VERSION = "akdp-normalization/v1"


def policy_manifest() -> dict:
    """Return the normalization policy recorded in every release manifest."""
    return {
        "version": POLICY_VERSION,
        "inputLayout": "<server>/gamedata/**",
        "outputLayout": "zh_CN/gamedata/**",
        "exclusions": list(EXCLUSIONS),
    }


def normalize_extraction(extract_root: Path, out_root: Path, *, server: str = "cn") -> Path:
    """Copy `<extract_root>/<server>/gamedata` into `<out_root>/zh_CN/gamedata`.

    Returns the normalized server root (`<out_root>/zh_CN`).
    """
    src = extract_root / server / "gamedata"
    if not src.is_dir():
        raise FileNotFoundError(f"no gamedata in extraction: {src}")
    zh = out_root / "zh_CN"
    dst = zh / "gamedata"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    for p in src.rglob("*"):
        rel = p.relative_to(src).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in EXCLUSIONS):
            continue
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    return zh
