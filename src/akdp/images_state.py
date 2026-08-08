"""Private, release-backed state for incremental image builds.

The public ``index.json`` is a consumer contract and deliberately contains no
CDN bundle provenance.  This module keeps that provenance in a separate,
versioned build-state asset so an ephemeral CI runner can select only changed
asset bundles without changing the public schema.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from .images_index import write_index_atomic


BUILD_STATE_ASSET = "images-build-state.json"
BUILD_STATE_SCHEMA = "akdp-images-build-state/v1"


@dataclass(frozen=True)
class BuildState:
    """Validated private state emitted only after an image Release succeeds."""

    version_id: str
    bundle_hashes: dict[str, str]
    bundle_candidates: dict[str, tuple[str, ...]]
    valid_skin_ids: frozenset[str]

    def to_dict(self) -> dict:
        return {
            "schemaVersion": BUILD_STATE_SCHEMA,
            "versionId": self.version_id,
            "bundleHashes": dict(sorted(self.bundle_hashes.items())),
            "bundleCandidates": {
                bundle: list(candidates)
                for bundle, candidates in sorted(self.bundle_candidates.items())
            },
            "validSkinIds": sorted(self.valid_skin_ids),
        }

    def bundles_for(self, skin_ids: set[str]) -> set[str]:
        """Return known source bundles for *skin_ids*.

        Candidate IDs are recorded even when they were not valid at the time
        of the previous run.  That makes a newly-valid skin in an unchanged
        bundle discoverable without a full cache restore.
        """
        return {
            bundle
            for bundle, candidates in self.bundle_candidates.items()
            if skin_ids.intersection(candidates)
        }

    def candidates_for(self, bundles: set[str]) -> set[str]:
        return {
            skin_id
            for bundle in bundles
            for skin_id in self.bundle_candidates.get(bundle, ())
        }


def load_build_state(path: Path) -> BuildState | None:
    """Load a state asset, returning ``None`` for any incompatible input."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schemaVersion") != BUILD_STATE_SCHEMA:
            return None
        version_id = raw.get("versionId")
        hashes = raw.get("bundleHashes")
        candidates = raw.get("bundleCandidates")
        valid = raw.get("validSkinIds")
        if not isinstance(version_id, str) or not version_id:
            return None
        if not isinstance(hashes, dict) or not isinstance(candidates, dict) or not isinstance(valid, list):
            return None
        if not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in hashes.items()):
            return None
        if not all(
            isinstance(bundle, str)
            and bundle
            and isinstance(skin_ids, list)
            and all(isinstance(skin_id, str) and skin_id for skin_id in skin_ids)
            for bundle, skin_ids in candidates.items()
        ):
            return None
        if not all(isinstance(skin_id, str) and skin_id for skin_id in valid):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    return BuildState(
        version_id=version_id,
        bundle_hashes=dict(hashes),
        bundle_candidates={
            bundle: tuple(sorted(set(skin_ids)))
            for bundle, skin_ids in candidates.items()
        },
        valid_skin_ids=frozenset(valid),
    )


def write_build_state(path: Path, state: BuildState) -> None:
    """Atomically write a private state asset using the shared JSON writer."""
    write_index_atomic(path, state.to_dict())


def state_matches_previous_index(state: BuildState, previous_index: dict | None) -> bool:
    """A state is usable only when it belongs to the downloaded public index."""
    return bool(previous_index) and previous_index.get("currentVersion") == state.version_id


def build_next_state(
    previous: BuildState | None,
    *,
    version_id: str,
    bundle_hashes: dict[str, str],
    processed_candidates: dict[str, list[str]],
    valid_skin_ids: set[str],
) -> BuildState:
    """Advance provenance only for bundles processed in this successful run."""
    candidates = dict(previous.bundle_candidates) if previous else {}
    current_bundles = set(bundle_hashes)
    for bundle in set(candidates) - current_bundles:
        del candidates[bundle]
    for bundle, skin_ids in processed_candidates.items():
        if bundle in current_bundles:
            candidates[bundle] = tuple(sorted(set(skin_ids)))
    return BuildState(
        version_id=version_id,
        bundle_hashes=dict(bundle_hashes),
        bundle_candidates=candidates,
        valid_skin_ids=frozenset(valid_skin_ids),
    )


def merge_incremental_index(
    previous_index: dict | None,
    partial_index: dict,
    *,
    valid_skin_ids: set[str],
    affected_skin_ids: set[str],
) -> dict:
    """Overlay extracted affected artworks onto the prior full public index.

    The output intentionally keeps only the pre-package fields of the public
    schema.  ``package_images`` remains the sole owner of schemaVersion,
    baselineVersion, shards, and sinceVersion injection.
    """
    previous_artworks = (previous_index or {}).get("artworks", {})
    artworks = copy.deepcopy(previous_artworks)
    for skin_id in affected_skin_ids | (set(artworks) - valid_skin_ids):
        artworks.pop(skin_id, None)
    for skin_id, entry in partial_index.get("artworks", {}).items():
        if skin_id in valid_skin_ids:
            artworks[skin_id] = entry
    return {
        "currentVersion": partial_index.get("currentVersion", ""),
        "artworks": artworks,
    }
