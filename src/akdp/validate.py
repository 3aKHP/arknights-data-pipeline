"""Validation gates. Any gate failure must block publishing (fail-closed)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import contract


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


#: tables that wrap their records under a sub-key
_WRAPPER_KEYS = {
    "stage_table.json": "stages",
    "item_table.json": "items",
    "enemy_handbook_table.json": "enemyData",
}


def _count_records(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(data, dict):
        wrapper = _WRAPPER_KEYS.get(path.name)
        if wrapper and isinstance(data.get(wrapper), dict):
            return len(data[wrapper])
        return len(data)
    if isinstance(data, list):
        return len(data)
    return None


def validate_candidate(
    candidate: Path,
    baseline: Path | None = None,
    probes: dict | None = None,
    *,
    max_record_drop_ratio: float = 0.05,
) -> ValidationResult:
    """Run all gates against a candidate release tree (rooted at the dir containing zh_CN/)."""
    res = ValidationResult()
    zh = candidate / "zh_CN"
    if not zh.is_dir():
        res.errors.append(f"missing server root: {zh}")
        return res

    # --- gate 1: contract files exist and parse
    for name in contract.REQUIRED_EXCEL_FILES:
        p = zh / "gamedata/excel" / name
        if not p.exists():
            res.errors.append(f"missing required excel file: gamedata/excel/{name}")
        elif _count_records(p) is None:
            res.errors.append(f"unparseable excel file: gamedata/excel/{name}")
    for rel in contract.REQUIRED_LEVELS_FILES:
        if not (zh / "gamedata/levels" / rel).exists():
            res.errors.append(f"missing required levels file: gamedata/levels/{rel}")

    # Reject the numeric rarity shape that previously broke PRTS consumers.
    char_path = zh / "gamedata/excel/character_table.json"
    if char_path.is_file():
        try:
            table = json.loads(char_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            table = None
        if isinstance(table, dict):
            bad_rarity = [
                cid for cid, data in table.items()
                if isinstance(data, dict) and cid.startswith("char_")
                and data.get("rarity") is not None
                and (not isinstance(data["rarity"], str)
                     or not data["rarity"].startswith("TIER_"))
            ]
            if bad_rarity:
                res.errors.append(
                    "invalid operator rarity (expected TIER_* string): "
                    + ", ".join(bad_rarity[:10])
                )

    # Reject gacha_table shapes the PRTS recruitment lookup cannot consume.
    # gacha_table is a bag of functional keys, not a record map, so the
    # generic record-count gate does not apply; assert the keys directly.
    gacha_path = zh / "gamedata/excel/gacha_table.json"
    if gacha_path.is_file():
        try:
            gacha = json.loads(gacha_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            gacha = None
        if isinstance(gacha, dict):
            tags = gacha.get("gachaTags")
            if not (
                isinstance(tags, list) and tags
                and all(isinstance(t, dict) and "tagId" in t and "tagName" in t for t in tags)
            ):
                res.errors.append(
                    "invalid gacha_table.json: gachaTags must be a non-empty list "
                    "of {tagId, tagName, ...} objects"
                )
            pool = gacha.get("recruitPool")
            if not (
                isinstance(pool, dict)
                and isinstance(pool.get("recruitTimeTable"), list)
                and isinstance(pool.get("recruitConstants"), dict)
            ):
                res.errors.append(
                    "invalid gacha_table.json: recruitPool must contain "
                    "recruitTimeTable (list) and recruitConstants (object)"
                )
            if not isinstance(gacha.get("recruitDetail"), str) or not gacha["recruitDetail"].strip():
                res.errors.append(
                    "invalid gacha_table.json: recruitDetail must be a non-empty string"
                )

    # --- gate 2: every .json is decodable UTF-8 JSON
    bad = 0
    for p in zh.rglob("*.json"):
        try:
            json.loads(p.read_bytes().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            bad += 1
            if bad <= 10:
                res.errors.append(f"invalid JSON/UTF-8: {p.relative_to(zh)}")
    if bad:
        res.errors.append(f"total invalid .json files: {bad}")

    # --- gate 3: record-count regression vs baseline
    counts: dict[str, int] = {}
    for rel in contract.RECORD_COUNT_TABLES:
        p = zh / rel
        if p.exists():
            n = _count_records(p)
            if n is not None:
                counts[rel] = n
    res.metrics["record_counts"] = counts
    if baseline is not None:
        for rel, n in counts.items():
            bp = baseline / "zh_CN" / rel
            if not bp.exists():
                continue
            bn = _count_records(bp)
            if bn and n < bn * (1 - max_record_drop_ratio):
                res.errors.append(
                    f"record regression in {rel}: baseline {bn} -> candidate {n} "
                    f"(drop > {max_record_drop_ratio:.0%})"
                )

    # --- gate 4: accumulation invariant — file counts per subtree must not shrink
    for subtree in ("gamedata/excel", "gamedata/levels", "gamedata/story"):
        cand_n = len(list((zh / subtree).rglob("*"))) if (zh / subtree).exists() else 0
        res.metrics.setdefault("file_counts", {})[subtree] = cand_n
        if baseline is not None and (baseline / "zh_CN" / subtree).exists():
            base_n = len(list((baseline / "zh_CN" / subtree).rglob("*")))
            if cand_n < base_n:
                res.errors.append(
                    f"accumulation violated in {subtree}: baseline {base_n} files -> candidate {cand_n}"
                )

    # --- gate 5: probes (new-content smoke checks)
    probes = probes or {}
    if probes.get("operators"):
        char_table = json.loads((zh / "gamedata/excel/character_table.json").read_text(encoding="utf-8"))
        names = {v.get("name") for v in char_table.values() if isinstance(v, dict)}
        for op in probes["operators"]:
            if op not in names:
                res.errors.append(f"probe failed: operator {op} not in character_table")
    if probes.get("events"):
        srt = json.loads((zh / "gamedata/excel/story_review_table.json").read_text(encoding="utf-8"))
        stage_table = json.loads((zh / "gamedata/excel/stage_table.json").read_text(encoding="utf-8"))
        stages = stage_table.get("stages", stage_table)
        for ev in probes["events"]:
            if ev not in srt:
                res.errors.append(f"probe failed: {ev} not in story_review_table")
            if not any(ev in k for k in stages):
                res.errors.append(f"probe failed: no stage in stage_table for {ev}")

    # --- gate 6: story entries without converted story JSON
    # Covers story_review_table infoUnlockDatas and meta table extra avgs.
    # txt present but json missing = conversion failed (error);
    # txt missing entirely = extraction gap (warning, needs investigation).
    from .story import iter_story_refs

    story_dir = zh / "gamedata/story"
    if story_dir.is_dir():
        # case-insensitive source index: client tables sometimes reference
        # paths that differ from extracted files only by case
        txt_index = {
            p.relative_to(story_dir).as_posix().lower()
            for p in story_dir.rglob("*.txt")
        }
        unconverted, source_missing = set(), set()
        for txt in iter_story_refs(zh):
            if (story_dir / f"{txt}.json").exists():
                continue
            if f"{txt}.txt".lower() in txt_index:
                unconverted.add(txt)
            else:
                source_missing.add(txt)
        res.metrics["story_entries_unconverted"] = len(unconverted)
        res.metrics["story_entries_source_missing"] = len(source_missing)
        if unconverted:
            res.errors.append(
                f"{len(unconverted)} story entries have .txt but no converted JSON, "
                f"e.g. {sorted(unconverted)[:3]}"
            )
        if source_missing:
            res.warnings.append(
                f"{len(source_missing)} story entries lack source .txt in the tree, "
                f"e.g. {sorted(source_missing)[:3]}"
            )

    # --- gate 7: summary acceptance (standing release gate)
    # Full-inventory scan with zero LLM cost: every chapter/event discovered
    # from story_review_table must be covered, and every shipped entry must
    # pass the same acceptance gate applied at generation time. This is what
    # turns "the cache poisoned a release once" into a blocked release.
    review_path = zh / "gamedata/excel/story_review_table.json"
    if review_path.is_file():
        from .summarize import _iter_chapters
        from .summary_gate import CHAPTER, EVENT, accept_summary

        try:
            chapters = _iter_chapters(zh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            chapters = None  # corruption already reported by gates 1/2
        if chapters is not None:
            def _load_map(name: str) -> dict:
                p = zh / name
                if not p.is_file():
                    return {}
                loaded = json.loads(p.read_text(encoding="utf-8"))
                return loaded if isinstance(loaded, dict) else {}

            chapter_summaries = _load_map("summaries.json")
            event_summaries = _load_map("event_summaries.json")
            chapter_missing = chapter_rejected = 0
            event_missing = event_rejected = 0

            for ch in chapters:
                key = ch["story_key"]
                if key not in chapter_summaries:
                    chapter_missing += 1
                    res.errors.append(f"summary gate: chapter has no summary: {key}")
            for key, value in sorted(chapter_summaries.items()):
                ok, reason = accept_summary(CHAPTER, value if isinstance(value, str) else None)
                if not ok:
                    chapter_rejected += 1
                    res.errors.append(f"summary gate: chapter rejected ({reason}): {key}")

            events = {ch["event_id"] for ch in chapters if ch["event_id"]}
            for ev in sorted(events):
                if ev not in event_summaries:
                    event_missing += 1
                    res.errors.append(f"summary gate: event has no summary: {ev}")
            for ev, value in sorted(event_summaries.items()):
                ok, reason = accept_summary(EVENT, value if isinstance(value, str) else None)
                if not ok:
                    event_rejected += 1
                    res.errors.append(f"summary gate: event rejected ({reason}): {ev}")

            res.metrics["summary_gate"] = {
                "chapters": len(chapters),
                "chapter_missing": chapter_missing,
                "chapter_rejected": chapter_rejected,
                "events": len(events),
                "event_missing": event_missing,
                "event_rejected": event_rejected,
            }

    return res
