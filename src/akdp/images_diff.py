"""Delta computation between two image indexes.

Compares all three variant SHA-256 values (original + large + preview).
A change in any variant — not just original — places the entry in the
``changed`` set.  This catches Pillow/resampling implementation changes
that alter large/preview output without touching the original.
"""

from __future__ import annotations

_VARIANT_TIERS: tuple[str, ...] = ("original", "large", "preview")


def compute_delta(
    current: dict, previous: dict | None,
) -> dict[str, set[str]]:
    """Compare two indexes and return the delta set.

    Returns ``{"added": {...}, "changed": {...}, "removed": {...}}`` where
    each set contains skin IDs.

    A skin is *changed* if any of its variant sha256 values differ or are
    missing between the two indexes.
    """
    prev_artworks = (previous or {}).get("artworks", {})
    curr_artworks = current.get("artworks", {})

    prev_ids = set(prev_artworks)
    curr_ids = set(curr_artworks)

    added = curr_ids - prev_ids
    removed = prev_ids - curr_ids

    changed: set[str] = set()
    for sid in curr_ids & prev_ids:
        prev_entry = prev_artworks[sid]
        curr_entry = curr_artworks[sid]
        for tier in _VARIANT_TIERS:
            prev_hash = prev_entry.get(tier, {}).get("sha256")
            curr_hash = curr_entry.get(tier, {}).get("sha256")
            if prev_hash != curr_hash:
                changed.add(sid)
                break

    return {"added": added, "changed": changed, "removed": removed}
