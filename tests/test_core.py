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
    _write(zh, "gamedata/excel/gacha_table.json", _MIN_GACHA_TABLE)
    _write(zh, "gamedata/levels/enemydata/enemy_database.json", {"enemies": []})
    if old_level:
        _write(zh, "gamedata/levels/activities/act1/level_act1_01.json", {})


#: minimal gacha_table shape satisfying the recruitment contract gate
_MIN_GACHA_TABLE = {
    "gachaTags": [{"tagId": 1, "tagName": "近卫干员", "tagGroup": 1}],
    "recruitPool": {
        "recruitTimeTable": [{"timeLength": 10, "recruitPrice": 0}],
        "recruitConstants": {"maxRecruitTime": 540},
    },
    "recruitDetail": "公开招募规则",
}


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


# --- summary acceptance release gate (gate 7) ---

_GOOD_CHAPTER_SUMMARY = "话" * 60 + "。"
_GOOD_EVENT_SUMMARY = "话" * 130 + "。"
_SENTINEL = "（无对话内容）"


def _mk_summary_tree(root, *, review_chapters, summaries=None, event_summaries=None):
    """Candidate tree whose story_review_table lists the given story keys."""
    _mk_tree(root, char_names=["阿米娅"])
    zh = root / "zh_CN"
    _write(zh, "gamedata/excel/story_review_table.json", {
        "act1": {"name": "活动一", "infoUnlockDatas": [
            {"storyTxt": key, "storyCode": f"EP{i}", "storyName": f"章{i}",
             "storySort": i}
            for i, key in enumerate(review_chapters)
        ]},
    })
    for i, key in enumerate(review_chapters):
        _write(zh, f"gamedata/story/{key}.json", {"storyList": [
            {"prop": "name", "attributes": {"name": "阿米娅", "content": "正文。" * 80}},
        ]})
    if summaries is not None:
        _write(zh, "summaries.json", summaries)
    if event_summaries is not None:
        _write(zh, "event_summaries.json", event_summaries)


def test_validate_summary_gate_passes_on_good_entries(tmp_path):
    cand = tmp_path / "cand"
    _mk_summary_tree(
        cand,
        review_chapters=["activities/act1/ch0"],
        summaries={"activities/act1/ch0": _GOOD_CHAPTER_SUMMARY},
        event_summaries={"act1": _GOOD_EVENT_SUMMARY},
    )
    res = validate_candidate(cand)
    assert res.ok, res.errors
    assert res.metrics["summary_gate"] == {
        "chapters": 1, "chapter_missing": 0, "chapter_rejected": 0,
        "events": 1, "event_missing": 0, "event_rejected": 0,
    }


def test_validate_summary_gate_accepts_sentinel(tmp_path):
    cand = tmp_path / "cand"
    _mk_summary_tree(
        cand,
        review_chapters=["activities/act1/ch0"],
        summaries={"activities/act1/ch0": _SENTINEL},
        event_summaries={"act1": _GOOD_EVENT_SUMMARY},
    )
    res = validate_candidate(cand)
    assert res.ok, res.errors


def test_validate_summary_gate_blocks_missing_empty_truncated(tmp_path):
    cand = tmp_path / "cand"
    _mk_summary_tree(
        cand,
        review_chapters=["activities/act1/ch0", "activities/act1/ch1", "activities/act1/ch2"],
        # ch0 missing entirely, ch1 empty (the incident's failure mode),
        # ch2 cut mid-sentence (the incident's other failure mode)
        summaries={
            "activities/act1/ch1": "",
            "activities/act1/ch2": "话" * 80,
        },
    )
    res = validate_candidate(cand)
    assert not res.ok
    errors = "\n".join(res.errors)
    assert "summary gate: chapter has no summary: activities/act1/ch0" in errors
    assert "summary gate: chapter rejected (empty): activities/act1/ch1" in errors
    assert "summary gate: chapter rejected (truncated_terminal): activities/act1/ch2" in errors
    assert "summary gate: event has no summary: act1" in errors
    gate = res.metrics["summary_gate"]
    assert gate["chapter_missing"] == 1 and gate["chapter_rejected"] == 2
    assert gate["event_missing"] == 1


def test_validate_summary_gate_flags_stale_bad_entries(tmp_path):
    cand = tmp_path / "cand"
    _mk_summary_tree(
        cand,
        review_chapters=["activities/act1/ch0"],
        summaries={
            "activities/act1/ch0": _GOOD_CHAPTER_SUMMARY,
            "stale/key": "",  # not a current chapter, still ships in the file
        },
        event_summaries={
            "act1": _GOOD_EVENT_SUMMARY,
            "oldact": "太短。",  # legacy short entry
        },
    )
    res = validate_candidate(cand)
    assert not res.ok
    errors = "\n".join(res.errors)
    assert "summary gate: chapter rejected (empty): stale/key" in errors
    assert "summary gate: event rejected (too_short): oldact" in errors


def test_validate_summary_gate_no_chapters_no_requirements(tmp_path):
    # empty story_review_table (the pre-existing fixtures) must keep passing
    cand = tmp_path / "cand"
    _mk_tree(cand, char_names=["阿米娅"])
    res = validate_candidate(cand)
    assert res.ok, res.errors


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


def test_validate_keeps_structured_errors_for_corrupt_character_table(tmp_path):
    candidate = tmp_path / "candidate"
    _mk_tree(candidate, char_names=["阿米娅"])
    (candidate / "zh_CN/gamedata/excel/character_table.json").write_bytes(b"not-json")

    result = validate_candidate(candidate)

    assert not result.ok
    assert any("unparseable excel file" in error for error in result.errors)


def test_validate_requires_gacha_table(tmp_path):
    candidate = tmp_path / "candidate"
    _mk_tree(candidate, char_names=["阿米娅"])
    (candidate / "zh_CN/gamedata/excel/gacha_table.json").unlink()

    result = validate_candidate(candidate)

    assert not result.ok
    assert any("missing required excel file" in e and "gacha_table" in e for e in result.errors)


def test_validate_rejects_bad_gacha_table_shapes(tmp_path):
    candidate = tmp_path / "candidate"
    _mk_tree(candidate, char_names=["阿米娅"])
    _write(candidate, "zh_CN/gamedata/excel/gacha_table.json", {
        "gachaTags": [{"tagId": 1}],  # missing tagName
        "recruitPool": {"recruitTimeTable": []},  # missing recruitConstants
        "recruitDetail": "   ",  # blank
    })

    result = validate_candidate(candidate)

    assert not result.ok
    assert any("gachaTags" in e for e in result.errors)
    assert any("recruitPool" in e for e in result.errors)
    assert any("recruitDetail" in e for e in result.errors)
