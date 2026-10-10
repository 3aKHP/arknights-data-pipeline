"""Story conversion: turn raw avg .txt scripts into structured story JSON.

Wraps ASTR-Script (vendor/ASTR-Script, pinned submodule):
  - func.getEvents / getExtraAvg enumerate stories from story_review tables
  - jsonconvert.reader parses one avg script into {storyList, ...}

The vendored code expects a client-data layout (`<root>/cn/gamedata/...`),
so we expose the candidate's zh_CN tree through a `cn` symlink.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

STORY_DIR = "gamedata/story"


@dataclass
class StoryStats:
    converted: list[str] = field(default_factory=list)
    missing_source: list[str] = field(default_factory=list)  # json wanted but no .txt
    failed: list[dict] = field(default_factory=list)
    case_fixed: list[dict] = field(default_factory=list)  # case-mismatch symlinks created

    def to_dict(self) -> dict:
        return {
            "converted": len(self.converted),
            "missing_source": self.missing_source,
            "failed": self.failed,
            "case_fixed": self.case_fixed,
            "converted_files": self.converted[:50],
        }


def iter_story_refs(zh: Path):
    """Yield every referenced story text path (without extension) in the tree.

    Covers story_review_table infoUnlockDatas, story_review_meta_table
    extra avgs (contentPath), and the roguelike supplement catalog refs
    (table-derived; does not require story_supplement.json to exist).
    """
    srt_path = zh / "gamedata/excel/story_review_table.json"
    if srt_path.exists():
        srt = json.loads(srt_path.read_text(encoding="utf-8"))
        for entry in srt.values():
            if not isinstance(entry, dict):
                continue
            for s in entry.get("infoUnlockDatas", []):
                if s.get("storyTxt"):
                    yield s["storyTxt"]
    meta_path = zh / "gamedata/excel/story_review_meta_table.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        avgs = (meta.get("actArchiveResData") or {}).get("avgs") or {}
        for avg in avgs.values():
            if isinstance(avg, dict) and avg.get("contentPath"):
                yield avg["contentPath"]
    from .story_supplement import iter_supplement_refs

    yield from iter_supplement_refs(zh)


def _fix_case_mismatches(zh: Path) -> list[dict]:
    """Symlink expected-case storyTxt paths whose actual file differs only by case.

    The client tables reference e.g. `Obt/Roguelike/RO1/...` while
    the extracted files live at `obt/roguelike/ro1/...`; on a case-sensitive
    filesystem the vendored converter would otherwise miss them.
    """
    story_dir = zh / STORY_DIR
    if not story_dir.is_dir():
        return []
    index: dict[str, str] = {}
    for p in story_dir.rglob("*.txt"):
        rel = p.relative_to(story_dir).as_posix()
        index.setdefault(rel.lower(), rel)
    fixed = []
    for txt in iter_story_refs(zh):
        expected = story_dir / f"{txt}.txt"
        if expected.exists():
            continue
        actual = index.get(f"{txt}.txt".lower())
        if actual and actual != f"{txt}.txt":
            expected.parent.mkdir(parents=True, exist_ok=True)
            os.symlink((story_dir / actual).resolve(), expected)
            fixed.append({"expected": f"{txt}.txt", "actual": actual})
    return fixed


def _import_astr(astr_path: Path):
    if not (astr_path / "jsonconvert.py").exists():
        raise FileNotFoundError(
            f"ASTR-Script not found at {astr_path} "
            f"(hint: git submodule update --init, or set --astr-path)"
        )
    sys.path.insert(0, str(astr_path.resolve()))
    import func  # type: ignore[import-not-found]
    import jsonconvert  # type: ignore[import-not-found]

    return func, jsonconvert


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _regen_chardict(zh: Path) -> None:
    """Rebuild chardict.json from character_table (mirrors ASTR-Script logic)."""
    table_path = zh / "gamedata/excel/character_table.json"
    if not table_path.exists():
        return
    table = json.loads(table_path.read_text(encoding="utf-8"))
    char_dict = {}
    for cid, data in table.items():
        parts = cid.split("_")
        if len(parts) >= 3 and parts[0] == "char":
            char_dict[parts[2]] = {"name": data.get("name"), "id": parts[1]}
    (zh / "chardict.json").write_text(
        json.dumps(char_dict, ensure_ascii=False), encoding="utf-8"
    )


def _meta_raw_briefs(zh: Path) -> dict[str, str]:
    """contentPath -> rawBrief from story_review_meta_table avgs (RO1 five)."""
    meta = _load_json(zh / "gamedata/excel/story_review_meta_table.json", {})
    avgs = (meta.get("actArchiveResData") or {}).get("avgs") or {}
    return {
        v["contentPath"]: v["rawBrief"]
        for v in avgs.values()
        if isinstance(v, dict) and v.get("contentPath") and v.get("rawBrief")
    }


def _make_supplement_story(func, link_root: Path, event: dict, ch: dict,
                           zh: Path, briefs: dict, resolve, shadow_root: Path):
    """Duck-typed ExtraStory carrying catalog metadata into the JSON output."""
    s = func.ExtraStory.__new__(func.ExtraStory)
    s.root_dir = link_root / "cn"
    s.lang = "cn"
    s.eventid = event["event_id"]
    s.eventName = event["name"]
    s.entryType = event["entry_type"]
    s.storyCode = ch["code"]
    s.avgTag = ch["avg_tag"]
    s.storyName = ch["name"]
    s.storyData = {}
    # official brief: a [uc]info companion file wins; otherwise fall back
    # to the meta table's rawBrief (the RO1 five carry those).
    info = zh / STORY_DIR / f"[uc]info/{ch['key'].lower()}.txt"
    s.storyInfo = info if info.is_file() else None
    if not s.storyInfo and briefs.get(ch["key"]):
        s.rawStoryInfo = briefs[ch["key"]]
    txt = s.root_dir / "gamedata" / "story" / f"{ch['key']}.txt"
    try:
        raw = txt.read_text(encoding="utf-8")
    except OSError:
        raw = None  # convert_one reports the missing source
    if raw is not None:
        from .story_normalize import normalize_dialog_text

        normalized = normalize_dialog_text(raw, resolve)
        if normalized != raw:
            # keep the vendored converter untouched: feed it a normalized
            # shadow copy outside the candidate tree
            shadow = shadow_root / "cn" / "gamedata" / "story" / f"{ch['key']}.txt"
            shadow.parent.mkdir(parents=True, exist_ok=True)
            shadow.write_text(normalized, encoding="utf-8")
            txt = shadow
    s.storyTxt = txt
    s.f = ch["key"]
    return s


def _supplement_metadata_drifted(jpath: Path, event: dict, ch: dict) -> bool:
    """True when an existing JSON was baked with different catalog metadata.

    Incremental conversion skips existing files; without this check the
    RO1 five would keep their old extra-avg metadata (eventid/storyName)
    forever even though the catalog now owns their titles.
    """
    try:
        data = json.loads(jpath.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return True
    return (
        data.get("eventid") != event["event_id"]
        or data.get("eventName") != event["name"]
        or data.get("entryType") != event["entry_type"]
        or data.get("storyCode") != ch["code"]
        or data.get("storyName") != ch["name"]
        or data.get("avgTag") != ch["avg_tag"]
    )


def convert_stories(candidate: Path, astr_path: Path) -> StoryStats:
    """Convert every story entry in the candidate that lacks a JSON.

    Reads/writes inside `candidate` (the dir containing zh_CN/).
    """
    func, jsonconvert = _import_astr(astr_path)
    zh = candidate / "zh_CN"
    stats = StoryStats()
    stats.case_fixed = _fix_case_mismatches(zh)

    # expose zh_CN as cn/ for the vendored code
    link_root = candidate.parent / ".astr-src"
    link = link_root / "cn"
    link_root.mkdir(exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(zh.resolve(), link)

    storyinfo = _load_json(zh / "storyinfo.json", {})
    wordcount = _load_json(zh / "wordcount.json", {})
    extrainfo = _load_json(zh / "extrastory.json", {"extra": []})

    def convert_one(story) -> None:
        jpath = zh / STORY_DIR / f"{story.f}.json"
        if jpath.exists():
            return
        try:
            story_json, counter = jsonconvert.reader(story)
        except FileNotFoundError:
            stats.missing_source.append(story.f)
            return
        except Exception as e:  # parser failure on one file must not kill the run
            stats.failed.append({"story": story.f, "error": f"{type(e).__name__}: {e}"})
            return
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(json.dumps(story_json, ensure_ascii=False), encoding="utf-8")
        storyinfo[story.f] = story_json.get("storyInfo", "")
        stats.converted.append(story.f)
        return counter

    for event in func.getEvents(link_root, "cn"):
        wc = wordcount.setdefault(event.eventid, {})
        for story in event:
            counter = convert_one(story)
            if counter is not None:
                wc[story.f] = counter

    # supplement catalog chapters (roguelike). Runs BEFORE the extra-avg
    # pass so the RO1 five get catalog metadata; getExtraAvg then skips
    # them as already converted.
    from .story_supplement import build_supplement
    from .story_normalize import speaker_resolver

    shadow_root = candidate.parent / ".astr-normalized"
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    char_table = _load_json(zh / "gamedata/excel/character_table.json", {})
    resolve = speaker_resolver(char_table)
    briefs = _meta_raw_briefs(zh)
    catalog = build_supplement(zh)
    for event in catalog["events"]:
        wc = wordcount.setdefault(event["event_id"], {})
        for ch in event["chapters"]:
            jpath = zh / STORY_DIR / f"{ch['key']}.json"
            if jpath.exists() and _supplement_metadata_drifted(jpath, event, ch):
                jpath.unlink()
            story = _make_supplement_story(
                func, link_root, event, ch, zh, briefs, resolve, shadow_root
            )
            counter = convert_one(story)
            if counter is not None:
                wc[ch["key"]] = counter

    # extra avg (情报处理室 etc.)
    try:
        extra_list = []
        for extra in func.getExtraAvg(link_root, "cn"):
            counter = convert_one(extra)
            if (zh / STORY_DIR / f"{extra.f}.json").exists():
                extra_list.append({"storyName": extra.storyName, "storyTxt": extra.f})
        extrainfo["extra"] = extra_list
    except Exception as e:
        stats.failed.append({"story": "<extra-avg>", "error": f"{type(e).__name__}: {e}"})

    (zh / "storyinfo.json").write_text(
        json.dumps(storyinfo, ensure_ascii=False), encoding="utf-8"
    )
    (zh / "wordcount.json").write_text(
        json.dumps(wordcount, ensure_ascii=False), encoding="utf-8"
    )
    (zh / "extrastory.json").write_text(
        json.dumps(extrainfo, ensure_ascii=False), encoding="utf-8"
    )
    _regen_chardict(zh)

    stats.converted.sort()
    return stats
