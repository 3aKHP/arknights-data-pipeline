"""Roguelike story supplement catalog generation.

The story review table covers mainline/activity/memoir chapters, but
roguelike (集成战略) narrative content is enumerated only by
roguelike_topic_table (ending AVGs, endbook texts, monthly chat records)
and story_review_meta_table (RO1 entry/endings, rogue_3 challenge
stories). This module derives a deterministic supplement catalog from
those tables so consumers can list/search rogue stories like regular
ones.

The catalog is a derived artifact: it is regenerated from the candidate
tree on every story run and carries the source versionId for drift
detection. Chapter keys keep the client tables' original casing; the
story conversion layer resolves them to on-disk lowercase files via
case-fix symlinks (see story._fix_case_mismatches).

Deliberately out of scope: `ref_*` image lists, tutorial prompts, and
`text_scene_*` ending-recap templates (UI text, not narrative). The 12
non-rogue challengeBooks (act29side/act42side) are a separate gap and
are intentionally not pulled in here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

#: catalog file at the zh_CN/ root of the story zip (registered in
#: contract.STORY_INDEX_FILES)
SUPPLEMENT_FILENAME = "story_supplement.json"
SUPPLEMENT_VERSION = 1

#: entryType assigned to supplement events. Must NOT be "NONE" (PRTS
#: search indexing filters non-memoir NONE entries) and must not reuse
#: "ACTIVITY" (would pollute the activities category).
ENTRY_TYPE = "ROGUELIKE"

_TOPIC_TABLE = "gamedata/excel/roguelike_topic_table.json"
_META_TABLE = "gamedata/excel/story_review_meta_table.json"
_REVIEW_TABLE = "gamedata/excel/story_review_table.json"

_CHALLENGE_KEY_RE = re.compile(r"^rogue_(\d+)_challenge_story_(\d+)$")


def _load_table(zh: Path, rel: str) -> dict:
    path = zh / rel
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _story_file_index(zh: Path) -> set[str]:
    """Case-folded index of story .txt paths (minus extension)."""
    story_dir = zh / "gamedata/story"
    if not story_dir.is_dir():
        return set()
    return {
        p.relative_to(story_dir).as_posix()[:-4].lower()
        for p in story_dir.rglob("*.txt")
        if not p.is_symlink()
    }


def _values(node) -> list:
    """Dict-or-list tolerant value iteration (client tables vary)."""
    if isinstance(node, dict):
        return [v for v in node.values() if isinstance(v, dict)]
    if isinstance(node, list):
        return [v for v in node if isinstance(v, dict)]
    return []


def _chapter(key: str, name: str, code: str, tag: str, sort: int,
             group: str, source: str) -> dict:
    return {
        "key": key,
        "name": name,
        "code": code,
        "avg_tag": tag,
        "sort": sort,
        "group": group,
        "source": source,
    }


def _iter_topic_events(topic_table: dict, meta: dict,
                       story_index: set[str], skipped: list[dict]
                       ) -> Iterator[dict]:
    """Yield one supplement event per roguelike topic, in topic order."""
    topics = topic_table.get("topics") or {}
    details = topic_table.get("details") or {}
    avgs = ((meta.get("actArchiveResData") or {}).get("avgs")) or {}
    challenge_books = ((meta.get("actArchiveResData") or {}).get("challengeBooks")) or {}

    ordered = sorted(
        (t for t in _values(topics) if t.get("id", "").startswith("rogue_")),
        key=lambda t: t.get("sort", 0),
    )
    for topic in ordered:
        topic_id: str = topic["id"]
        detail = details.get(topic_id) or {}
        num = topic_id.removeprefix("rogue_")
        chapters: list[dict] = []

        def add(key, name, code, tag, sort, group, source):
            if not key:
                return
            if key.lower() not in story_index:
                skipped.append({"key": key, "reason": "source_txt_missing",
                                "source": source})
                return
            chapters.append(_chapter(key, name, code, tag, sort, group, source))

        # (a) entry AVG: no table reference anywhere; path convention only.
        add(f"Obt/Roguelike/RO{num}/level_rogue{num}_entry",
            "开幕", f"RO{num}-OP", "序章", 1, "entry", "synthetic:entry-path")

        # (b)+(c) endings and endbook texts.
        endbooks = ((detail.get("archiveComp") or {}).get("endbook") or {}).get("endbook") or {}
        endings = detail.get("endings") or {}
        ordered_endbooks = sorted(_values(endbooks), key=lambda e: e.get("sortId", 0))
        if ordered_endbooks:
            for eb in ordered_endbooks:
                i = eb.get("sortId", 0)
                title = eb.get("title") or (endings.get(eb.get("endingId")) or {}).get("name") or ""
                if eb.get("hasAvg") and eb.get("avgId"):
                    add(eb["avgId"], title, f"RO{num}-E{i}", "结局",
                        10 + i * 100, "ending", "topic:endbook.avgId")
                for item in sorted(_values(eb.get("clientEndbookItemDatas")),
                                   key=lambda it: it.get("sortId", 0)):
                    j = item.get("sortId", 0)
                    add(item.get("textId"),
                        item.get("endbookName") or f"{title}·手记{j}",
                        f"RO{num}-E{i}-{j}", f"结局手记·{title}",
                        10 + i * 100 + j, "endbook", "topic:endbook.textId")
        else:
            # RO1: no endbook section at all; entry + 4 endings live in the
            # meta table's avgs (avg_rogue_1_1..5). Ending display names come
            # from the topic's endings dict (ro_ending_N, ordered by N).
            ro1_endings = sorted(
                ((k, v) for k, v in endings.items() if isinstance(v, dict)),
                key=lambda kv: kv[0],
            )
            for idx, (ending_key, ending) in enumerate(ro1_endings, start=1):
                name = ending.get("name") or ""
                add(f"Obt/Roguelike/RO{num}/level_rogue{num}_ending_{idx}",
                    name, f"RO{num}-E{idx}", "结局", 10 + idx * 100,
                    "ending", "meta:avgs+topic:endings")

        # (d) monthly chat records.
        squads = {s.get("chatId"): s for s in _values(detail.get("monthSquad"))}
        chats = ((detail.get("archiveComp") or {}).get("chat") or {}).get("chat") or {}
        chat_groups = [(gid, g) for gid, g in chats.items() if isinstance(g, dict)] \
            if isinstance(chats, dict) else []
        for gid, group in sorted(chat_groups, key=lambda kv: kv[1].get("sortId", 0)):
            gid_sort = group.get("sortId", 0)
            items = [it for it in (group.get("chatItemList") or [])
                     if isinstance(it, dict) and it.get("chatStoryId")]
            team = (squads.get(gid) or {}).get("teamName") or "月度记录"
            for k, item in enumerate(items, start=1):
                add(item["chatStoryId"], f"{team}·{k}",
                    f"RO{num}-M{gid_sort}-{k}", f"月度记录·{team}",
                    10000 + gid_sort * 100 + k, "month", "topic:chat.chatStoryId")

        # (e) challenge stories (rogue_3 only today): meta challengeBooks.
        for cb_key, cb in sorted(challenge_books.items()):
            m = _CHALLENGE_KEY_RE.match(cb_key)
            if not m or int(m.group(1)) != int(num):
                continue
            add(cb.get("textId"),
                cb.get("storyName") or cb.get("titleName") or "",
                f"RO{num}-C{int(m.group(2)):02d}", "挑战记录",
                20000 + int(m.group(2)), "challenge", "meta:challengeBooks")

        yield {
            "event_id": topic_id,
            "name": topic.get("name") or topic_id,
            "entry_type": ENTRY_TYPE,
            "sort": topic.get("sort", 0),
            "chapters": sorted(chapters, key=lambda c: c["sort"]),
        }


def build_supplement(zh: Path, source_version: str | None = None) -> dict:
    """Build the supplement catalog from the candidate tree's tables.

    Pure table-driven and deterministic except for one deliberate FS
    check: chapters whose source .txt is absent from the tree are skipped
    (recorded under skipped_missing_source), so the catalog never
    references phantom scripts.
    """
    topic_table = _load_table(zh, _TOPIC_TABLE)
    meta = _load_table(zh, _META_TABLE)
    story_index = _story_file_index(zh)

    skipped: list[dict] = []
    events = []
    if topic_table:
        events = list(_iter_topic_events(topic_table, meta, story_index, skipped))

    # defense in depth: never duplicate a key the review table already lists
    # (e.g. upstream absorbs rogue content into the review table later).
    review = _load_table(zh, _REVIEW_TABLE)
    review_keys = {
        s.get("storyTxt").lower()
        for e in review.values() if isinstance(e, dict)
        for s in (e.get("infoUnlockDatas") or [])
        if isinstance(s, dict) and s.get("storyTxt")
    }
    skipped_existing: list[str] = []
    for event in events:
        kept = []
        for ch in event["chapters"]:
            if ch["key"].lower() in review_keys:
                skipped_existing.append(ch["key"])
            else:
                kept.append(ch)
        event["chapters"] = kept

    return {
        "version": SUPPLEMENT_VERSION,
        "generated_from": {
            "tables": [_TOPIC_TABLE, _META_TABLE],
            "source_version": source_version,
        },
        "events": events,
        "skipped_missing_source": skipped,
        "skipped_existing_in_review": skipped_existing,
    }


def iter_supplement_refs(zh: Path) -> Iterator[str]:
    """Yield every story key the catalog would carry (table-derived).

    Independent of whether story_supplement.json has been written yet, so
    enumeration consumers (case-fix symlinks, conversion gate) always see
    the full intended inventory.
    """
    catalog = build_supplement(zh)
    for event in catalog["events"]:
        for ch in event["chapters"]:
            yield ch["key"]
