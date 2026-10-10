"""Tests for the roguelike story supplement catalog (story_supplement.py)."""

import json
from pathlib import Path

from akdp.story import iter_story_refs
from akdp.story_supplement import (
    SUPPLEMENT_FILENAME,
    build_supplement,
    iter_supplement_refs,
)


def _write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _mk_topic_table() -> dict:
    return {
        "topics": {
            "rogue_6": {"id": "rogue_6", "name": "沉沦者的黑流树海", "sort": 6},
            "rogue_1": {"id": "rogue_1", "name": "傀影与猩红孤钻", "sort": 1},
        },
        "details": {
            "rogue_1": {
                "endings": {
                    "ro_ending_1": {"name": "舞会终场"},
                    "ro_ending_2": {"name": "滑稽喜剧"},
                },
                "archiveComp": {"endbook": {"endbook": {}},
                                "chat": {"chat": {}}},
                "monthSquad": {},
            },
            "rogue_6": {
                "endings": {"ro6_ending_1": {"name": "强制重启"}},
                "archiveComp": {
                    "endbook": {"endbook": {
                        "endbook_rogue_6_1": {
                            "sortId": 1, "title": "强制重启",
                            "endingId": "ro6_ending_1", "hasAvg": True,
                            "avgId": "Obt/Roguelike/RO6/level_rogue6_ending_1",
                            "clientEndbookItemDatas": [
                                {"sortId": 1, "endbookName": "一棵发光的小树",
                                 "textId": "Obt/Rogue/rogue_6/Endbook/endbook_rogue_6_1_1"},
                            ],
                        },
                        "endbook_rogue_6_2": {  # no AVG ending (hasAvg false)
                            "sortId": 2, "title": "维度重构",
                            "endingId": "ro6_ending_2", "hasAvg": False,
                            "avgId": None,
                            "clientEndbookItemDatas": [],
                        },
                    }},
                    "chat": {"chat": {
                        "month_chat_rogue_6_4": {
                            "sortId": 4,
                            "chatItemList": [
                                {"floor": 1, "chatDesc": "欢迎来到特科马。",
                                 "chatStoryId": "Obt/Rogue/rogue_6/MonthRecord/month_record_rogue_6_4_1"},
                                {"floor": 3, "chatDesc": None,
                                 "chatStoryId": "Obt/Rogue/rogue_6/MonthRecord/month_record_rogue_6_4_2"},
                            ],
                        },
                    }},
                },
                "monthSquad": {
                    "a": {"chatId": "month_chat_rogue_6_4", "teamName": "南方往事"},
                },
            },
        },
    }


def _mk_meta_table() -> dict:
    return {
        "actArchiveResData": {
            "avgs": {
                "avg_rogue_1_1": {"desc": "开幕",
                                  "contentPath": "Obt/Roguelike/RO1/level_rogue1_entry"},
                "avg_rogue_1_2": {"desc": "落幕",
                                  "contentPath": "Obt/Roguelike/RO1/level_rogue1_ending_1"},
                "avg_rogue_1_3": {"desc": "幕后黑手",
                                  "contentPath": "Obt/Roguelike/RO1/level_rogue1_ending_2"},
            },
            "challengeBooks": {
                "rogue_3_challenge_story_01": {
                    "titleName": "观察者的视野", "storyName": "失败的科考记录",
                    "textId": "Obt/Rogue/rogue_3/Challenge/challenge_rogue_3_1_1",
                },
                "act29side_majorInvest_1": {  # non-rogue: out of scope
                    "titleName": "维杜尼亚的崩毁", "storyName": "维杜尼亚的崩毁",
                    "textId": "Activities/act29side/mark/mark1",
                },
            },
        },
    }


def _mk_story_files(zh: Path) -> None:
    """On-disk txts use the extracted lowercase layout."""
    story = zh / "gamedata/story"
    files = [
        "obt/roguelike/ro1/level_rogue1_entry",
        "obt/roguelike/ro1/level_rogue1_ending_1",
        "obt/roguelike/ro1/level_rogue1_ending_2",
        "obt/roguelike/ro6/level_rogue6_entry",
        "obt/roguelike/ro6/level_rogue6_ending_1",
        "obt/rogue/rogue_6/endbook/endbook_rogue_6_1_1",
        "obt/rogue/rogue_6/monthrecord/month_record_rogue_6_4_1",
        "obt/rogue/rogue_6/monthrecord/month_record_rogue_6_4_2",
        "obt/rogue/rogue_3/challenge/challenge_rogue_3_1_1",
    ]
    for rel in files:
        p = story / f"{rel}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[name=\"???\"]……\n", encoding="utf-8")


def _mk_zh(tmp_path: Path, *, with_supplement: bool = True) -> Path:
    zh = tmp_path / "zh_CN"
    _write(zh, "gamedata/excel/roguelike_topic_table.json", _mk_topic_table())
    _write(zh, "gamedata/excel/story_review_meta_table.json", _mk_meta_table())
    _write(zh, "gamedata/excel/story_review_table.json", {})
    _mk_story_files(zh)
    if with_supplement:
        supp = build_supplement(zh, source_version="v-test")
        _write(zh, SUPPLEMENT_FILENAME, supp)
    return zh


def _chapters(cat: dict, event_id: str) -> list[dict]:
    return next(e for e in cat["events"] if e["event_id"] == event_id)["chapters"]


def test_build_supplement_ro6_groups_and_titles(tmp_path):
    cat = build_supplement(_mk_zh(tmp_path, with_supplement=False))
    chapters = _chapters(cat, "rogue_6")
    by_group = {}
    for ch in chapters:
        by_group.setdefault(ch["group"], []).append(ch)

    assert [c["name"] for c in by_group["entry"]] == ["开幕"]
    assert [c["name"] for c in by_group["ending"]] == ["强制重启"]
    assert [c["name"] for c in by_group["endbook"]] == ["一棵发光的小树"]
    # month group: teamName from monthSquad, sequential suffix
    assert [c["name"] for c in by_group["month"]] == ["南方往事·1", "南方往事·2"]

    # endbook without AVG contributes no ending chapter
    assert not any(c["name"] == "维度重构" for c in by_group["ending"])

    # keys keep the client tables' original casing
    assert by_group["ending"][0]["key"] == "Obt/Roguelike/RO6/level_rogue6_ending_1"
    assert by_group["endbook"][0]["key"] == "Obt/Rogue/rogue_6/Endbook/endbook_rogue_6_1_1"

    # deterministic order: sorts unique and increasing
    sorts = [c["sort"] for c in chapters]
    assert sorts == sorted(sorts) and len(set(sorts)) == len(sorts)


def test_build_supplement_ro1_empty_endbook_uses_meta_and_topic_names(tmp_path):
    cat = build_supplement(_mk_zh(tmp_path, with_supplement=False))
    chapters = _chapters(cat, "rogue_1")
    endings = [c for c in chapters if c["group"] == "ending"]
    # names come from the topic endings dict, not meta avgs' desc (落幕/…)
    assert [c["name"] for c in endings] == ["舞会终场", "滑稽喜剧"]
    assert endings[0]["key"] == "Obt/Roguelike/RO1/level_rogue1_ending_1"


def test_build_supplement_challenge_scope(tmp_path):
    """rogue_3 challengeBooks are in; non-rogue challengeBooks stay out."""
    cat = build_supplement(_mk_zh(tmp_path, with_supplement=False))
    # rogue_3 topic is absent from the fixture topic table, so its challenge
    # chapters attach to no event; act29side must never appear either.
    all_keys = [c["key"] for e in cat["events"] for c in e["chapters"]]
    assert not any("challenge" in k.lower() for k in all_keys)
    assert not any("act29side" in k for k in all_keys)


def test_build_supplement_skips_missing_source(tmp_path):
    zh = _mk_zh(tmp_path, with_supplement=False)
    (zh / "gamedata/story/obt/rogue/rogue_6/monthrecord/month_record_rogue_6_4_2.txt").unlink()
    cat = build_supplement(zh)
    months = [c for c in _chapters(cat, "rogue_6") if c["group"] == "month"]
    assert [c["name"] for c in months] == ["南方往事·1"]
    assert cat["skipped_missing_source"] == [
        {"key": "Obt/Rogue/rogue_6/MonthRecord/month_record_rogue_6_4_2",
         "reason": "source_txt_missing", "source": "topic:chat.chatStoryId"}
    ]


def test_build_supplement_dedups_against_review_table(tmp_path):
    zh = _mk_zh(tmp_path, with_supplement=False)
    _write(zh, "gamedata/excel/story_review_table.json", {
        "rogue_6": {  # upstream absorbed the topic into the review table
            "entryType": "ACTIVITY", "name": "沉沦者的黑流树海",
            "infoUnlockDatas": [{
                "storyCode": "X", "storyName": "强制重启", "avgTag": "结局",
                "storyInfo": None,
                "storyTxt": "obt/roguelike/ro6/level_rogue6_ending_1",  # lowercase
            }],
        },
    })
    cat = build_supplement(zh)
    endings = [c for c in _chapters(cat, "rogue_6") if c["group"] == "ending"]
    assert endings == []
    assert cat["skipped_existing_in_review"] == ["Obt/Roguelike/RO6/level_rogue6_ending_1"]


def test_build_supplement_deterministic(tmp_path):
    zh = _mk_zh(tmp_path, with_supplement=False)
    a = json.dumps(build_supplement(zh), ensure_ascii=False, sort_keys=True)
    b = json.dumps(build_supplement(zh), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_iter_story_refs_include_supplement_keys(tmp_path):
    zh = _mk_zh(tmp_path, with_supplement=False)
    refs = set(iter_story_refs(zh))
    assert "Obt/Roguelike/RO6/level_rogue6_entry" in refs
    assert "Obt/Rogue/rogue_6/MonthRecord/month_record_rogue_6_4_1" in refs
    assert set(iter_supplement_refs(zh)) <= refs
