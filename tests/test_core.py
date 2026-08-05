import hashlib
import json
import os
from pathlib import Path

from akdp import contract
from akdp.merge import merge_trees
from akdp.package import package_candidate
from akdp.validate import _count_records, validate_candidate


def _write(root: Path, rel: str, content) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        p.write_text(json.dumps(content), encoding="utf-8")
    else:
        p.write_text(content, encoding="utf-8")


def _mk_tree(root: Path, *, char_names, old_level: bool = True) -> None:
    zh = root / "zh_CN"
    _write(zh, "gamedata/excel/character_table.json",
           {f"char_{i}": {"name": n, "rarity": "TIER_5"} for i, n in enumerate(char_names)})
    for name in ("handbook_info_table", "charword_table", "story_review_table",
                 "enemy_handbook_table", "zone_table"):
        _write(zh, f"gamedata/excel/{name}.json", {})
    _write(zh, "gamedata/excel/stage_table.json", {"stages": {"a": {}}})
    _write(zh, "gamedata/excel/item_table.json", {"items": {"i1": {}}})
    _write(zh, "gamedata/levels/enemydata/enemy_database.json", {"enemies": []})
    if old_level:
        _write(zh, "gamedata/levels/activities/act1/level_act1_01.json", {})


def test_merge_overlay_and_accumulation(tmp_path):
    baseline, extraction, candidate = tmp_path / "b", tmp_path / "e", tmp_path / "c"
    _write(baseline, "a.json", {"v": 1})
    _write(baseline, "old.json", {"v": 1})
    _write(extraction, "a.json", {"v": 2})
    _write(extraction, "new.json", {"v": 3})

    stats = merge_trees(baseline, extraction, candidate)

    assert json.loads((candidate / "a.json").read_text()) == {"v": 2}  # new wins
    assert (candidate / "old.json").exists()  # baseline kept (accumulation)
    assert (candidate / "new.json").exists()
    assert stats.added == ["new.json"]
    assert stats.changed == ["a.json"]
    assert stats.kept_from_baseline == 1


def test_wrapper_record_counting(tmp_path):
    p = tmp_path / "item_table.json"
    p.write_text(json.dumps({"items": {"a": {}, "b": {}}, "expItems": {}}))
    assert _count_records(p) == 2
    p2 = tmp_path / "enemy_handbook_table.json"
    p2.write_text(json.dumps({"enemyData": {"e": {}}, "raceData": {}}))
    assert _count_records(p2) == 1


def test_validate_pass_and_probe(tmp_path):
    cand = tmp_path / "cand"
    _mk_tree(cand, char_names=["阿米娅", "嘉辛塔"])
    res = validate_candidate(cand, probes={"operators": ["嘉辛塔"], "events": []})
    assert res.ok, res.errors


def test_validate_probe_failure(tmp_path):
    cand = tmp_path / "cand"
    _mk_tree(cand, char_names=["阿米娅"])
    res = validate_candidate(cand, probes={"operators": ["不存在的干员"]})
    assert not res.ok
    assert any("不存在的干员" in e for e in res.errors)


def test_validate_record_regression(tmp_path):
    baseline, cand = tmp_path / "b", tmp_path / "c"
    _mk_tree(baseline, char_names=[f"干员{i}" for i in range(100)])
    _mk_tree(cand, char_names=[f"干员{i}" for i in range(50)])
    res = validate_candidate(cand, baseline=baseline)
    assert not res.ok
    assert any("record regression" in e for e in res.errors)


def test_package_is_byte_reproducible(tmp_path):
    candidate = tmp_path / "candidate" / "zh_CN"
    _write(candidate, "gamedata/excel/character_table.json", {"char_1": {"rarity": "TIER_5"}})
    _write(candidate, "gamedata/levels/enemydata/enemy_database.json", {"enemies": []})
    _write(candidate, "gamedata/story/activities/test.json", {"storyList": []})
    _write(candidate, "storyinfo.json", {})

    first = tmp_path / "dist-first"
    second = tmp_path / "dist-second"
    first_manifest = package_candidate(candidate.parent, first)
    os.utime(candidate / "gamedata/excel/character_table.json", (1, 1))
    package_candidate(candidate.parent, second)

    assert first_manifest["contractVersion"] == contract.CONTRACT_VERSION
    assert first_manifest["normalization"]["version"] == "akdp-normalization/v1"
    assert first_manifest["pipeline"]["commit"]
    assert "flatc" in first_manifest["tools"]

    for name in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
        assert hashlib.sha256((first / name).read_bytes()).digest() == hashlib.sha256(
            (second / name).read_bytes()
        ).digest()


def test_validate_rejects_numeric_operator_rarity(tmp_path):
    candidate = tmp_path / "candidate"
    _mk_tree(candidate, char_names=["阿米娅"])
    _write(candidate, "zh_CN/gamedata/excel/character_table.json", {
        "char_001_bad": {"name": "坏数据", "rarity": 5},
    })
    result = validate_candidate(candidate)
    assert not result.ok
    assert any("invalid operator rarity" in error for error in result.errors)
