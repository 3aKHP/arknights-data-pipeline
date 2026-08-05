"""Tests for the image pipeline texture-name mapping and skin-ID filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from akdp.images_fetch import _changed_bundles
from akdp.images_extract import _tex_name_to_skin_id, _load_skin_ids


def test_failed_download_not_marked_unchanged() -> None:
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
    # Simulate: amiya chararts downloaded successfully, chen failed,
    # amiya skinpack was unchanged (already cached).
    succeeded = {"chararts/char_002_amiya.ab"}
    to_download_names = {"chararts/char_002_amiya.ab", "chararts/char_010_chen.ab"}

    hot_update_hashes = {
        info["name"]: info["hash"] for info in hot_update["abInfos"]
    }
    all_hashes: dict[str, str] = {}
    for name, h in hot_update_hashes.items():
        if name in succeeded:
            all_hashes[name] = h
        elif name not in to_download_names:
            all_hashes[name] = h

    # Successfully downloaded bundle is persisted.
    assert "chararts/char_002_amiya.ab" in all_hashes
    # Failed bundle is NOT persisted (will retry next run).
    assert "chararts/char_010_chen.ab" not in all_hashes
    # Unchanged bundle is persisted.
    assert "skinpack/char_002_amiya.ab" in all_hashes

    # On the next run, the failed bundle should appear as "to download"
    # because its hash is not in prev_hashes.
    to_dl, unchanged = _changed_bundles(hot_update, all_hashes)
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
