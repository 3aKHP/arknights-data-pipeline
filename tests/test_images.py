"""Tests for the image pipeline texture-name mapping and skin-ID filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from akdp.images_fetch import _changed_bundles, _build_hash_map, _safe_filename
from akdp.images_extract import _tex_name_to_skin_id, _load_skin_ids
from akdp.images_index import generate_index, _classify_skin_id
from akdp.images_variants import generate_variants, compute_delta


def test_failed_download_not_marked_unchanged(tmp_path: Path) -> None:
    """A bundle that failed to download must not appear in the persisted hash
    map, otherwise it would be classified as 'unchanged' on the next run and
    never retried."""
    hot_update = {
        "abInfos": [
            {"name": "chararts/char_002_amiya.ab", "hash": "aaa"},
            {"name": "chararts/char_010_chen.ab", "hash": "bbb"},
            {"name": "skinpack/char_002_amiya.ab", "hash": "ccc"},
        ]
    }
    hot_update_hashes = {
        info["name"]: info["hash"] for info in hot_update["abInfos"]
    }
    # Simulate: amiya chararts downloaded successfully, chen failed,
    # amiya skinpack was unchanged (already cached on disk).
    succeeded = {"chararts/char_002_amiya.ab"}
    to_download_names = {"chararts/char_002_amiya.ab", "chararts/char_010_chen.ab"}
    # Create the cached file for the unchanged bundle.
    (tmp_path / _safe_filename("skinpack/char_002_amiya.ab")).write_bytes(b"")

    all_hashes = _build_hash_map(
        hot_update_hashes, to_download_names, succeeded, tmp_path,
    )

    # Successfully downloaded bundle is persisted.
    assert "chararts/char_002_amiya.ab" in all_hashes
    # Failed bundle is NOT persisted (will retry next run).
    assert "chararts/char_010_chen.ab" not in all_hashes
    # Unchanged bundle is persisted.
    assert "skinpack/char_002_amiya.ab" in all_hashes

    # On the next run, the failed bundle should appear as "to download"
    # because its hash is not in prev_hashes.
    to_dl, _ = _changed_bundles(hot_update, all_hashes)
    to_dl_names = [info["name"] for info in to_dl]
    assert "chararts/char_010_chen.ab" in to_dl_names
    assert "chararts/char_002_amiya.ab" not in to_dl_names


@pytest.mark.parametrize("tex_name, expected", [
    # Base art
    ("char_002_amiya_1", "char_002_amiya#1"),
    ("char_002_amiya_2", "char_002_amiya#2"),
    ("char_451_robin_2", "char_451_robin#2"),
    ("char_617_sharp2_2", "char_617_sharp2#2"),
    # E1+ variant (only Amiya)
    ("char_002_amiya_1+", "char_002_amiya#1+"),
    # Multi-form operators
    ("char_1001_amiya2_2", "char_1001_amiya2#2"),
    # Skins (@ → _ in texture names)
    ("char_002_amiya_epoque#4", "char_002_amiya@epoque#4"),
    ("char_002_amiya_winter#1", "char_002_amiya@winter#1"),
    ("char_010_chen_sale#10", "char_010_chen@sale#10"),
    ("char_199_yak_summer#1", "char_199_yak@summer#1"),
    # Excluded: avatars, building sprites, alpha companions
    ("char_002_amiya", None),
    ("build_char_002_amiya", None),
    ("char_002_amiya[alpha]", None),
    ("build_char_010_chen_nian#2", None),
])
def test_tex_name_to_skin_id(tex_name: str, expected: str | None) -> None:
    assert _tex_name_to_skin_id(tex_name) == expected


def _write_excel(root: Path) -> None:
    """Write minimal skin_table + character_table for filtering tests."""
    excel = root / "gamedata" / "excel"
    excel.mkdir(parents=True, exist_ok=True)
    (excel / "character_table.json").write_text(json.dumps({
        "char_002_amiya": {"name": "阿米娅"},
        "char_010_chen": {"name": "陈"},
        "char_451_robin": {"name": "罗宾"},
    }), encoding="utf-8")
    (excel / "skin_table.json").write_text(json.dumps({
        "charSkins": {
            "char_002_amiya#1": {"charId": "char_002_amiya"},
            "char_002_amiya#2": {"charId": "char_002_amiya"},
            "char_002_amiya@epoque#4": {"charId": "char_002_amiya"},
            "char_010_chen#2": {"charId": "char_010_chen"},
            "char_010_chen@nian#2": {"charId": "char_010_chen"},
            # token should be excluded
            "token_10002_kalts_mon3tr_boc#6": {"charId": "char_003_kalts"},
            # charId not in character_table should be excluded
            "char_999_unknown#1": {"charId": "char_999_unknown"},
        }
    }), encoding="utf-8")


def test_load_skin_ids_filters_tokens_and_unknown(tmp_path: Path) -> None:
    _write_excel(tmp_path)
    valid = _load_skin_ids(tmp_path / "gamedata" / "excel")
    assert "char_002_amiya#2" in valid
    assert "char_002_amiya@epoque#4" in valid
    assert "char_010_chen#2" in valid
    # token excluded
    assert "token_10002_kalts_mon3tr_boc#6" not in valid
    # unknown charId excluded
    assert "char_999_unknown#1" not in valid


# ---------------------------------------------------------------------------
# images_index tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skin_id, expected_kind, expected_shard", [
    ("char_002_amiya#1", "base", "chararts"),
    ("char_002_amiya#2", "base", "chararts"),
    ("char_002_amiya#1+", "base", "chararts"),
    ("char_1001_amiya2#2", "base", "chararts"),
    ("char_002_amiya@epoque#4", "skin", "skinpack"),
    ("char_010_chen@sale#10", "skin", "skinpack"),
])
def test_classify_skin_id(skin_id: str, expected_kind: str, expected_shard: str) -> None:
    kind, shard = _classify_skin_id(skin_id)
    assert kind == expected_kind
    assert shard == expected_shard


def test_generate_index_basic(tmp_path: Path) -> None:
    """Generate an index from a small set of fake PNGs and verify structure."""
    from PIL import Image

    _write_excel(tmp_path)
    images_dir = tmp_path / "images-out"
    images_dir.mkdir()

    # Create fake PNGs for known skin IDs.
    for skin_id, size in [
        ("char_002_amiya#2", (100, 200)),
        ("char_002_amiya@epoque#4", (200, 200)),
        ("char_010_chen#2", (50, 50)),
    ]:
        img = Image.new("RGBA", size, (255, 0, 0, 128))
        img.save(images_dir / f"{skin_id}.original.png")

    index, stats = generate_index(images_dir, tmp_path / "gamedata" / "excel", version_id="test-v1")

    assert index["currentVersion"] == "test-v1"
    assert stats.indexed == 3
    assert "char_002_amiya#2" in index["artworks"]
    assert "char_002_amiya@epoque#4" in index["artworks"]

    amiya_e2 = index["artworks"]["char_002_amiya#2"]
    assert amiya_e2["kind"] == "base"
    assert amiya_e2["shard"] == "chararts"
    assert amiya_e2["original"]["w"] == 100
    assert amiya_e2["original"]["h"] == 200
    assert amiya_e2["original"]["bytes"] > 0
    assert len(amiya_e2["original"]["sha256"]) == 64

    epoque = index["artworks"]["char_002_amiya@epoque#4"]
    assert epoque["kind"] == "skin"
    assert epoque["shard"] == "skinpack"


def test_generate_index_skips_unknown(tmp_path: Path) -> None:
    """PNGs whose skinId is not in skin_table should be skipped."""
    from PIL import Image

    _write_excel(tmp_path)
    images_dir = tmp_path / "images-out"
    images_dir.mkdir()

    # Valid + invalid skin IDs.
    img = Image.new("RGBA", (10, 10))
    img.save(images_dir / "char_002_amiya#2.original.png")
    img.save(images_dir / "char_999_fake#1.original.png")

    index, stats = generate_index(images_dir, tmp_path / "gamedata" / "excel")
    assert stats.indexed == 1
    assert stats.skipped == 1
    assert "char_999_fake#1" in stats.missing_from_tables
    assert "char_999_fake#1" not in index["artworks"]


# ---------------------------------------------------------------------------
# images_variants tests
# ---------------------------------------------------------------------------

def test_generate_variants_creates_large_and_preview(tmp_path: Path) -> None:
    """Variants are generated with correct max-side dimensions."""
    from PIL import Image

    images_dir = tmp_path / "images-out"
    images_dir.mkdir()
    # Create an original larger than both thresholds.
    orig = Image.new("RGBA", (2048, 1024), (255, 0, 0, 255))
    orig_path = images_dir / "char_002_amiya#2.original.png"
    orig.save(orig_path)

    index = {
        "currentVersion": "v1",
        "artworks": {
            "char_002_amiya#2": {
                "kind": "base",
                "shard": "chararts",
                "original": {"file": "char_002_amiya#2.original.png", "w": 2048, "h": 1024, "bytes": 100, "sha256": "abc"},
            }
        }
    }
    index_path = images_dir / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    stats = generate_variants(images_dir, index_path)
    assert stats.generated == 1

    updated = json.loads(index_path.read_text("utf-8"))
    entry = updated["artworks"]["char_002_amiya#2"]
    # Large: max side 1024, so 2048x1024 → 1024x512
    assert entry["large"]["w"] == 1024
    assert entry["large"]["h"] == 512
    # Preview: max side 256, so 2048x1024 → 256x128
    assert entry["preview"]["w"] == 256
    assert entry["preview"]["h"] == 128
    # Files exist
    assert (images_dir / "char_002_amiya#2.large.png").exists()
    assert (images_dir / "char_002_amiya#2.preview.png").exists()


def test_generate_variants_no_upscale(tmp_path: Path) -> None:
    """Small originals should not be upscaled."""
    from PIL import Image

    images_dir = tmp_path / "images-out"
    images_dir.mkdir()
    orig = Image.new("RGBA", (100, 50), (0, 255, 0, 255))
    orig.save(images_dir / "small.original.png")

    index = {
        "currentVersion": "v1",
        "artworks": {
            "small": {
                "kind": "base", "shard": "chararts",
                "original": {"file": "small.original.png", "w": 100, "h": 50, "bytes": 10, "sha256": "x"},
            }
        }
    }
    index_path = images_dir / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    generate_variants(images_dir, index_path)
    updated = json.loads(index_path.read_text("utf-8"))
    entry = updated["artworks"]["small"]
    # Both variants should match original dimensions (no upscale).
    assert entry["large"]["w"] == 100
    assert entry["large"]["h"] == 50
    assert entry["preview"]["w"] == 100
    assert entry["preview"]["h"] == 50


def test_compute_delta() -> None:
    """Delta computation correctly identifies added/changed/removed."""
    current = {
        "artworks": {
            "a": {"original": {"sha256": "aaa"}},
            "b": {"original": {"sha256": "bbb_new"}},
            "c": {"original": {"sha256": "ccc"}},
        }
    }
    previous = {
        "artworks": {
            "a": {"original": {"sha256": "aaa"}},       # unchanged
            "b": {"original": {"sha256": "bbb_old"}},   # changed
            "d": {"original": {"sha256": "ddd"}},       # removed
        }
    }
    delta = compute_delta(current, previous)
    assert delta["added"] == {"c"}
    assert delta["changed"] == {"b"}
    assert delta["removed"] == {"d"}


def test_compute_delta_no_previous() -> None:
    """First run (no previous index) → everything is 'added'."""
    current = {"artworks": {"a": {"original": {"sha256": "x"}}, "b": {"original": {"sha256": "y"}}}}
    delta = compute_delta(current, None)
    assert delta["added"] == {"a", "b"}
    assert delta["changed"] == set()
    assert delta["removed"] == set()
