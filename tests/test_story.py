import json
import os
from pathlib import Path

import pytest

from akdp.check import parse_version_id_from_tag
from akdp.story import convert_stories

ASTR = Path(__file__).resolve().parent.parent / "vendor" / "ASTR-Script"

pytestmark = pytest.mark.skipif(not ASTR.exists(), reason="ASTR-Script submodule not checked out")


def _mk_story_tree(root: Path) -> None:
    zh = root / "zh_CN"
    (zh / "gamedata/excel").mkdir(parents=True)
    (zh / "gamedata/excel/story_review_table.json").write_text(json.dumps({
        "act1side": {
            "entryType": "ACTIVITY",
            "name": "测试活动",
            "infoUnlockDatas": [{
                "storyCode": "ACT-1", "avgTag": "幕间", "storyName": "测试剧情",
                "storyInfo": None, "storyTxt": "activities/act1side/level_act1side_01",
            }],
        },
    }, ensure_ascii=False))
    (zh / "gamedata/excel/story_review_meta_table.json").write_text(json.dumps({
        "actArchiveResData": {"avgs": {
            "avg_rogue_1_1": {
                "id": "avg_rogue_1_1", "desc": "开幕", "breifPath": None,
                "contentPath": "Obt/Roguelike/RO1/level_rogue1_entry",
                "rawBrief": "简介文本",
            },
        }},
    }, ensure_ascii=False))
    (zh / "gamedata/excel/character_table.json").write_text(json.dumps({
        "char_002_amiya": {"name": "阿米娅"},
        "token_xxx": {"name": "召唤物"},
    }, ensure_ascii=False))
    story = zh / "gamedata/story"
    (story / "activities/act1side").mkdir(parents=True)
    (story / "activities/act1side/level_act1side_01.txt").write_text(
        "[HEADER(is_skippable=true)]\n[name=\"阿米娅\"]你好，博士。\n[Dialog]\n", encoding="utf-8")
    # case-mismatched: table says Obt/Roguelike/RO1, file is obt/roguelike/ro1
    (story / "obt/roguelike/ro1").mkdir(parents=True)
    (story / "obt/roguelike/ro1/level_rogue1_entry.txt").write_text(
        "[name=\"???\"]……\n", encoding="utf-8")


def test_convert_stories_and_case_fix(tmp_path):
    _mk_story_tree(tmp_path)
    stats = convert_stories(tmp_path, ASTR)

    assert stats.failed == []
    assert stats.missing_source == []
    assert len(stats.case_fixed) == 1  # Obt/... symlink created

    zh = tmp_path / "zh_CN"
    main_json = zh / "gamedata/story/activities/act1side/level_act1side_01.json"
    extra_json = zh / "gamedata/story/Obt/Roguelike/RO1/level_rogue1_entry.json"
    assert main_json.exists()
    assert extra_json.exists()  # converted through the case-fix symlink

    data = json.loads(main_json.read_text(encoding="utf-8"))
    assert data["lang"] == "zh_CN"
    assert data["eventid"] == "act1side"
    assert any(line["prop"] == "name" for line in data["storyList"])

    storyinfo = json.loads((zh / "storyinfo.json").read_text(encoding="utf-8"))
    assert "activities/act1side/level_act1side_01" in storyinfo
    wordcount = json.loads((zh / "wordcount.json").read_text(encoding="utf-8"))
    assert wordcount["act1side"]["activities/act1side/level_act1side_01"] > 0
    extrainfo = json.loads((zh / "extrastory.json").read_text(encoding="utf-8"))
    assert extrainfo["extra"] == [
        {"storyName": "开幕", "storyTxt": "Obt/Roguelike/RO1/level_rogue1_entry"}
    ]
    chardict = json.loads((zh / "chardict.json").read_text(encoding="utf-8"))
    assert chardict == {"amiya": {"name": "阿米娅", "id": "002"}}


def test_convert_stories_idempotent(tmp_path):
    _mk_story_tree(tmp_path)
    convert_stories(tmp_path, ASTR)
    stats = convert_stories(tmp_path, ASTR)
    assert stats.converted == []  # existing JSONs are not regenerated


def test_parse_version_id_from_tag():
    assert parse_version_id_from_tag("upstream-26-08-03-23-34-20_a745fc") == "26-08-03-23-34-20_a745fc"
    assert parse_version_id_from_tag("gamedata-81c6d458a177-v2") == "81c6d458a177"
    assert parse_version_id_from_tag("v1.0.0") is None
