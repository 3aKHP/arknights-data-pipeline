import json
from pathlib import Path

import pytest

from akdp.check import parse_version_id_from_tag, version_changed
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
    assert parse_version_id_from_tag("data-26-08-03-23-34-20_a745fc") == "26-08-03-23-34-20_a745fc"
    assert parse_version_id_from_tag("upstream-26-08-03-23-34-20_a745fc") == "26-08-03-23-34-20_a745fc"
    assert parse_version_id_from_tag("gamedata-81c6d458a177-v2") == "81c6d458a177"
    assert parse_version_id_from_tag("v1.0.0") is None


def test_version_changed_is_scheduler_independent():
    assert not version_changed("same", "same")
    assert version_changed("new", "same")
    assert version_changed("same", "same", force=True)


def _mk_rogue_tree(root: Path) -> None:
    """Tree with a roguelike topic table, one Dialog month chat, one ending."""
    zh = root / "zh_CN"
    (zh / "gamedata/excel").mkdir(parents=True, exist_ok=True)
    (zh / "gamedata/excel/story_review_table.json").write_text("{}", encoding="utf-8")
    (zh / "gamedata/excel/story_review_meta_table.json").write_text(json.dumps({
        "actArchiveResData": {"avgs": {
            "avg_rogue_1_1": {
                "id": "avg_rogue_1_1", "desc": "开幕", "breifPath": None,
                "contentPath": "Obt/Roguelike/RO1/level_rogue1_entry",
                "rawBrief": "猩红孤钻开幕梗概",
            },
        }},
    }, ensure_ascii=False))
    (zh / "gamedata/excel/character_table.json").write_text(json.dumps({
        "char_405_absin": {"name": "苦艾"},
    }, ensure_ascii=False))
    (zh / "gamedata/excel/roguelike_topic_table.json").write_text(json.dumps({
        "topics": {"rogue_1": {"id": "rogue_1", "name": "傀影与猩红孤钻", "sort": 1}},
        "details": {"rogue_1": {
            "endings": {"ro_ending_1": {"name": "舞会终场"}},
            "archiveComp": {"endbook": {"endbook": {}}, "chat": {"chat": {
                "month_chat_rogue_1_1": {
                    "sortId": 1,
                    "chatItemList": [
                        {"floor": 1, "chatDesc": None,
                         "chatStoryId": "Obt/Rogue/month_chat_rogue_1_1/month_chat_rogue_1_1_1"},
                    ],
                },
            }}},
            "monthSquad": {"m1": {"chatId": "month_chat_rogue_1_1", "teamName": "卡兹戴尔联谊会"}},
        }},
    }, ensure_ascii=False))
    story = zh / "gamedata/story"
    (story / "obt/roguelike/ro1").mkdir(parents=True, exist_ok=True)
    (story / "obt/roguelike/ro1/level_rogue1_entry.txt").write_text(
        "[name=\"？？？\"]开幕词。\n", encoding="utf-8")
    (story / "obt/roguelike/ro1/level_rogue1_ending_1.txt").write_text(
        "[name=\"剧作家\"]落幕词。\n", encoding="utf-8")
    # official brief companion for the ending
    (story / "[uc]info/obt/roguelike/ro1").mkdir(parents=True, exist_ok=True)
    (story / "[uc]info/obt/roguelike/ro1/level_rogue1_ending_1.txt").write_text(
        "官方梗概：舞会终场。", encoding="utf-8")
    # Dialog-inline month chat
    mc = story / "obt/rogue/month_chat_rogue_1_1"
    mc.mkdir(parents=True, exist_ok=True)
    (mc / "month_chat_rogue_1_1_1.txt").write_text(
        "[Title] MC\n"
        "[Dialog(head=\"char_405_absin\", delay=1)]啊，赫拉格将军。\n"
        "[Dialog]\n"
        "[Dialog(head=\"npc_unknown\")]匿名台词。\n",
        encoding="utf-8")


def test_convert_stories_supplement_catalog(tmp_path):
    _mk_rogue_tree(tmp_path)
    stats = convert_stories(tmp_path, ASTR)
    assert stats.failed == []

    zh = tmp_path / "zh_CN"
    entry = json.loads((zh / "gamedata/story/Obt/Roguelike/RO1/level_rogue1_entry.json")
                       .read_text(encoding="utf-8"))
    assert entry["eventid"] == "rogue_1"
    assert entry["eventName"] == "傀影与猩红孤钻"
    assert entry["entryType"] == "ROGUELIKE"
    assert entry["storyCode"] == "RO1-OP"
    assert entry["storyName"] == "开幕"
    assert entry["storyInfo"] == "猩红孤钻开幕梗概"  # meta rawBrief fallback

    ending = json.loads(
        (zh / "gamedata/story/Obt/Roguelike/RO1/level_rogue1_ending_1.json")
        .read_text(encoding="utf-8"))
    assert ending["storyName"] == "舞会终场"  # topic ending name, not meta desc
    assert ending["avgTag"] == "结局"
    assert ending["storyInfo"] == "官方梗概：舞会终场。"  # [uc]info companion

    month = json.loads(
        (zh / "gamedata/story/Obt/Rogue/month_chat_rogue_1_1/month_chat_rogue_1_1_1.json")
        .read_text(encoding="utf-8"))
    assert month["storyName"] == "卡兹戴尔联谊会·1"
    names = [l for l in month["storyList"] if l.get("prop") == "name"]
    texts = [(l["attributes"].get("name"), l["attributes"].get("content")) for l in names]
    assert ("苦艾", "啊，赫拉格将军。") in texts  # Dialog head resolved
    assert ("？？？", "匿名台词。") in texts      # unknown head anonymized
    assert month["storyInfo"] == ""  # Dialog files have no brief

    storyinfo = json.loads((zh / "storyinfo.json").read_text(encoding="utf-8"))
    assert storyinfo["Obt/Roguelike/RO1/level_rogue1_ending_1"] == "官方梗概：舞会终场。"


def test_convert_stories_supplement_rebuilds_drifted_metadata(tmp_path):
    _mk_rogue_tree(tmp_path)
    zh = tmp_path / "zh_CN"
    # pre-seed a JSON with stale extra-avg metadata (the pre-catalog shape)
    jpath = zh / "gamedata/story/Obt/Roguelike/RO1/level_rogue1_ending_1.json"
    jpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.write_text(json.dumps({
        "lang": "zh_CN", "eventid": "", "eventName": "", "entryType": "EXTRA",
        "storyCode": "", "avgTag": "", "storyName": "落幕",
        "storyInfo": "官方梗概：舞会终场。", "storyList": [],
    }, ensure_ascii=False), encoding="utf-8")

    stats = convert_stories(tmp_path, ASTR)
    data = json.loads(jpath.read_text(encoding="utf-8"))
    assert data["eventName"] == "傀影与猩红孤钻"
    assert data["storyName"] == "舞会终场"
    assert data["entryType"] == "ROGUELIKE"
    assert any(l.get("prop") == "name" for l in data["storyList"])  # re-converted
    assert "Obt/Roguelike/RO1/level_rogue1_ending_1" in stats.converted


def test_convert_stories_supplement_idempotent(tmp_path):
    _mk_rogue_tree(tmp_path)
    convert_stories(tmp_path, ASTR)
    stats = convert_stories(tmp_path, ASTR)
    assert stats.converted == []
    assert stats.failed == []
