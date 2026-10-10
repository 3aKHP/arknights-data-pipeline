"""Gate tests for the story supplement catalog (validate.py gate 6b)."""

import json
from pathlib import Path

from akdp.story_supplement import (
    SUPPLEMENT_FILENAME,
    build_supplement,
    iter_supplement_refs,
)
from akdp.validate import validate_candidate
from test_story_supplement import _mk_zh, _write


def _mk_validate_tree(tmp_path: Path, *, with_supplement: bool) -> Path:
    """Minimal candidate tree passing gates 1-5, with story fixtures."""
    cand = tmp_path / "cand"
    zh = _mk_zh(cand, with_supplement=with_supplement)
    _write(zh, "gamedata/excel/character_table.json",
           {"char_002_amiya": {"name": "阿米娅", "rarity": "TIER_5"}})
    for name in ("handbook_info_table", "charword_table", "enemy_handbook_table",
                 "zone_table"):
        _write(zh, f"gamedata/excel/{name}.json", {})
    _write(zh, "gamedata/excel/stage_table.json", {"stages": {"a": {}}})
    _write(zh, "gamedata/excel/item_table.json", {"items": {"i1": {}}})
    _write(zh, "gamedata/excel/gacha_table.json", {
        "gachaTags": [{"tagId": 1, "tagName": "近卫干员", "tagGroup": 1}],
        "recruitPool": {
            "recruitTimeTable": [{"timeLength": 10, "recruitPrice": 0}],
            "recruitConstants": {"maxRecruitTime": 540},
        },
        "recruitDetail": "公开招募规则",
    })
    _write(zh, "gamedata/levels/enemydata/enemy_database.json", {"enemies": []})
    # converted JSONs so gate 6 stays quiet for the supplement refs
    for txt in iter_supplement_refs(zh):
        p = zh / "gamedata/story" / f"{txt}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    return cand


def test_validate_supplement_ok(tmp_path):
    cand = _mk_validate_tree(tmp_path, with_supplement=True)
    res = validate_candidate(cand, expected_source_version="v-test")
    assert not [e for e in res.errors if "supplement" in e]
    assert res.metrics["story_supplement"]["events"] == 2


def test_validate_supplement_missing(tmp_path):
    cand = _mk_validate_tree(tmp_path, with_supplement=False)
    res = validate_candidate(cand)
    assert any("missing while roguelike_topic_table is present" in e for e in res.errors)


def test_validate_supplement_version_mismatch(tmp_path):
    cand = _mk_validate_tree(tmp_path, with_supplement=True)
    res = validate_candidate(cand, expected_source_version="v-other")
    assert any("generated from 'v-test'" in e and "v-other" in e for e in res.errors)


def test_validate_supplement_duplicate_sort_rejected(tmp_path):
    cand = _mk_validate_tree(tmp_path, with_supplement=True)
    zh = cand / "zh_CN"
    supp = json.loads((zh / SUPPLEMENT_FILENAME).read_text(encoding="utf-8"))
    chapters = supp["events"][0]["chapters"]
    chapters[1]["sort"] = chapters[0]["sort"]
    (zh / SUPPLEMENT_FILENAME).write_text(json.dumps(supp, ensure_ascii=False))
    res = validate_candidate(cand)
    assert any("duplicate sorts" in e for e in res.errors)


def test_validate_supplement_review_collision_rejected(tmp_path):
    cand = _mk_validate_tree(tmp_path, with_supplement=True)
    zh = cand / "zh_CN"
    supp = json.loads((zh / SUPPLEMENT_FILENAME).read_text(encoding="utf-8"))
    supp["events"][0]["event_id"] = "act1side"
    _write(zh, "gamedata/excel/story_review_table.json", {"act1side": {}})
    (zh / SUPPLEMENT_FILENAME).write_text(json.dumps(supp, ensure_ascii=False))
    res = validate_candidate(cand)
    assert any("collides with review table" in e for e in res.errors)
