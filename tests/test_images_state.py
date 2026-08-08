"""Regression coverage for release-backed incremental image build state."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from akdp import images_publish
from akdp.cli import cmd_images_package
from akdp.images_index import write_index_atomic
from akdp.images_state import (
    BUILD_STATE_ASSET,
    BuildState,
    build_next_state,
    load_build_state,
    merge_incremental_index,
    write_build_state,
)


def _entry(skin_id: str, marker: str) -> dict:
    return {
        "kind": "skin" if "@" in skin_id else "base",
        "shard": "skinpack" if "@" in skin_id else "chararts",
        **{
            tier: {
                "file": f"{skin_id}.{tier}.png",
                "w": 10,
                "h": 10,
                "bytes": len(f"{marker}-{tier}"),
                "sha256": hashlib.sha256(f"{marker}-{tier}".encode()).hexdigest(),
            }
            for tier in ("original", "large", "preview")
        },
    }


def test_build_state_round_trip_retains_candidates_for_future_skin(tmp_path: Path) -> None:
    state = BuildState(
        version_id="v1",
        bundle_hashes={"skinpack/char_002_amiya.ab": "hash-a"},
        bundle_candidates={
            "skinpack/char_002_amiya.ab": (
                "char_002_amiya@future#99",
                "char_002_amiya@epoque#4",
            )
        },
        valid_skin_ids=frozenset({"char_002_amiya@epoque#4"}),
    )

    write_build_state(tmp_path / BUILD_STATE_ASSET, state)
    loaded = load_build_state(tmp_path / BUILD_STATE_ASSET)

    assert loaded is not None
    assert loaded.bundles_for({"char_002_amiya@future#99"}) == {
        "skinpack/char_002_amiya.ab"
    }


def test_merge_incremental_index_preserves_unchanged_and_removes_invalid() -> None:
    previous = {
        "currentVersion": "v1",
        "artworks": {
            "unchanged": _entry("unchanged", "old-a"),
            "changed": _entry("changed", "old-b"),
            "removed": _entry("removed", "old-c"),
        },
    }
    partial = {
        "currentVersion": "v2",
        "artworks": {"changed": _entry("changed", "new-b")},
    }

    merged = merge_incremental_index(
        previous,
        partial,
        valid_skin_ids={"unchanged", "changed"},
        affected_skin_ids={"changed"},
    )

    assert merged["currentVersion"] == "v2"
    assert merged["artworks"]["unchanged"] == previous["artworks"]["unchanged"]
    assert merged["artworks"]["changed"] == partial["artworks"]["changed"]
    assert "removed" not in merged["artworks"]


def test_incremental_package_uses_only_partial_files_and_advances_state(tmp_path: Path) -> None:
    """A fresh runner can merge one changed bundle without old PNG files."""
    work = tmp_path / "work"
    out = work / "images-out"
    prev = work / "images-prev"
    cache = work / "images-cache"
    out.mkdir(parents=True)
    prev.mkdir()
    cache.mkdir()

    previous = {
        "schemaVersion": "akdp-images/v1",
        "baselineVersion": "v0",
        "currentVersion": "v1",
        "shards": {"chararts-original": "baseline.zip"},
        "artworks": {
            "unchanged": {"sinceVersion": "v0", **_entry("unchanged", "old-a")},
            "changed": {"sinceVersion": "v0", **_entry("changed", "old-b")},
        },
    }
    write_index_atomic(prev / "index.json", previous)
    write_build_state(prev / BUILD_STATE_ASSET, BuildState(
        version_id="v1",
        bundle_hashes={"chararts/unchanged.ab": "old-a", "chararts/changed.ab": "old-b"},
        bundle_candidates={
            "chararts/unchanged.ab": ("unchanged",),
            "chararts/changed.ab": ("changed",),
        },
        valid_skin_ids=frozenset({"unchanged", "changed"}),
    ))

    partial = {"currentVersion": "v2", "artworks": {"changed": _entry("changed", "new-b")}}
    write_index_atomic(out / "index.json", partial)
    for tier in ("original", "large", "preview"):
        (out / f"changed.{tier}.png").write_bytes(f"new-b-{tier}".encode())
    (cache / "hashes.json").write_text(json.dumps({"versionId": "v2"}), encoding="utf-8")
    (work / "images-plan.json").write_text(json.dumps({
        "incremental": True,
        "bundleHashes": {"chararts/unchanged.ab": "old-a", "chararts/changed.ab": "new-b"},
        "downloadedBundles": ["chararts/changed.ab"],
        "removedBundles": [],
        "currentValidSkinIds": ["unchanged", "changed"],
        "newSkinIds": [],
        "requiredBundles": [],
    }), encoding="utf-8")
    (work / "images-extract.json").write_text(json.dumps({
        "bundle_candidates": {"chararts/changed.ab": ["changed"]},
    }), encoding="utf-8")

    assert cmd_images_package(argparse.Namespace(workdir=work)) == 0

    final_index = json.loads((work / "images-dist" / "index.json").read_text(encoding="utf-8"))
    assert set(final_index["artworks"]) == {"unchanged", "changed"}
    assert final_index["artworks"]["unchanged"]["original"]["sha256"] == previous["artworks"]["unchanged"]["original"]["sha256"]
    assert final_index["artworks"]["changed"]["original"]["sha256"] == partial["artworks"]["changed"]["original"]["sha256"]

    with zipfile.ZipFile(work / "images-dist" / "images-delta-v2.zip") as zf:
        assert sorted(zf.namelist()) == [
            "changed.large.png",
            "changed.original.png",
            "changed.preview.png",
        ]
    next_state = load_build_state(work / "images-dist" / BUILD_STATE_ASSET)
    assert next_state is not None
    assert next_state.version_id == "v2"
    assert next_state.bundle_hashes["chararts/changed.ab"] == "new-b"
    assert next_state.bundle_candidates["chararts/unchanged.ab"] == ("unchanged",)
    assert next_state.bundle_candidates["chararts/changed.ab"] == ("changed",)


def test_build_next_state_prunes_removed_bundles() -> None:
    previous = BuildState(
        version_id="v1",
        bundle_hashes={"old.ab": "old", "keep.ab": "keep"},
        bundle_candidates={"old.ab": ("old",), "keep.ab": ("keep",)},
        valid_skin_ids=frozenset({"old", "keep"}),
    )
    state = build_next_state(
        previous,
        version_id="v2",
        bundle_hashes={"keep.ab": "new"},
        processed_candidates={"keep.ab": ["keep", "new"]},
        valid_skin_ids={"keep", "new"},
    )
    assert "old.ab" not in state.bundle_candidates
    assert state.bundle_candidates["keep.ab"] == ("keep", "new")


def test_delta_publish_uploads_private_build_state(tmp_path: Path, monkeypatch) -> None:
    """The next hosted runner can recover state from the same delta Release."""
    dist = tmp_path / "images-dist"
    dist.mkdir()
    (dist / "images-delta-v2.zip").write_bytes(b"zip")
    (dist / "index.json").write_text(json.dumps({"artworks": {}}), encoding="utf-8")
    write_build_state(dist / BUILD_STATE_ASSET, BuildState(
        version_id="v2",
        bundle_hashes={"chararts/a.ab": "hash"},
        bundle_candidates={"chararts/a.ab": ("a",)},
        valid_skin_ids=frozenset({"a"}),
    ))
    commands: list[list[str]] = []
    monkeypatch.setattr(images_publish, "_release_exists", lambda _tag: True)
    monkeypatch.setattr(
        images_publish,
        "_run_with_retry",
        lambda command, _description: commands.append(command),
    )

    images_publish.publish_images(dist, version_id="v2", mode="delta", dry_run=False)

    uploaded = [command[-1] for command in commands]
    assert str(dist / "images-delta-v2.zip") in uploaded
    assert str(dist / "index.json") in uploaded
    assert str(dist / BUILD_STATE_ASSET) in uploaded
