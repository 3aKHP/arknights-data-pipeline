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

    return res
