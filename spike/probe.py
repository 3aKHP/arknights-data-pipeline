#!/usr/bin/env python3
"""Phase 0 spike probes: validate arkprts/torappu CN output against PRTS-MCP data contract.

Usage: probe.py <data_root>
Checks (issue #86 completion criteria inputs):
  1. New operators 嘉辛塔/时隙/珊比 in character_table
  2. act53side in story_review_table + story text files extractable
  3. act53side stages in stage_table (emergency package left this unproven)
  4. levels/enemydata/enemy_database.json present
  5. Non-UTF-8 .json files inventory
  6. rarity field format in character_table (TIER_ prefix vs numeric)
  7. Directory layout vs PRTS-MCP contract (zh_CN/gamedata/excel/...)
"""

import json
import sys
from pathlib import Path

NEW_OPERATORS = ["嘉辛塔", "时隙", "珊比"]
NEW_EVENT = "act53side"
REQUIRED_EXCEL = [
    "character_table.json",
    "handbook_info_table.json",
    "charword_table.json",
    "story_review_table.json",
    "enemy_handbook_table.json",
    "stage_table.json",
    "zone_table.json",
    "item_table.json",
]


def find_excel_dir(root: Path) -> Path | None:
    for cand in root.rglob("gamedata/excel"):
        if (cand / "character_table.json").exists():
            return cand
    return None


def main() -> int:
    root = Path(sys.argv[1])
    excel = find_excel_dir(root)
    if excel is None:
        print("FAIL: no gamedata/excel with character_table.json found under", root)
        return 1
    print(f"excel dir: {excel}")
    gamedata = excel.parent
    server_root = gamedata.parent
    print(f"server root (contract equivalent of zh_CN/): {server_root}")

    ok = True

    # --- required excel files
    for name in REQUIRED_EXCEL:
        p = excel / name
        status = "OK " if p.exists() else "MISS"
        if not p.exists():
            ok = False
        print(f"  [{status}] excel/{name}")

    # --- 1. new operators + rarity format
    char_table = json.loads((excel / "character_table.json").read_text(encoding="utf-8"))
    by_name = {v.get("name"): v for v in char_table.values() if isinstance(v, dict)}
    rarity_samples = set()
    for name in NEW_OPERATORS:
        op = by_name.get(name)
        if op is None:
            print(f"  [MISS] operator {name}")
            ok = False
        else:
            rarity = op.get("rarity")
            rarity_samples.add(type(rarity).__name__ + ":" + str(rarity))
            print(f"  [OK ] operator {name} (rarity={rarity!r}, id={op.get('charId') or 'n/a'})")
    all_rarities = {type(v.get("rarity")).__name__ for v in char_table.values() if isinstance(v, dict)}
    print(f"  rarity python types across table: {sorted(all_rarities)}; samples: {sorted(rarity_samples)}")

    # --- 2. story
    srt_path = excel / "story_review_table.json"
    srt = json.loads(srt_path.read_text(encoding="utf-8"))
    entry = srt.get(NEW_EVENT)
    if entry is None:
        print(f"  [MISS] story_review_table has no {NEW_EVENT}")
        ok = False
    else:
        stories = entry.get("infoUnlockDatas", [])
        print(f"  [OK ] story_review_table[{NEW_EVENT}] with {len(stories)} entries")
        story_dir = gamedata / "story"
        missing_txt = []
        for s in stories:
            txt = s.get("storyTxt")
            if txt and not (story_dir / f"{txt}.txt").exists():
                missing_txt.append(txt)
        if missing_txt:
            print(f"  [MISS] {len(missing_txt)} story texts not found, e.g. {missing_txt[:3]}")
            ok = False
        else:
            print(f"  [OK ] all story text files for {NEW_EVENT} resolvable")

    # --- 3. stage_table for new event
    stage_table = json.loads((excel / "stage_table.json").read_text(encoding="utf-8"))
    stages = stage_table.get("stages", stage_table)
    event_stages = [k for k, v in stages.items() if isinstance(v, dict) and NEW_EVENT in str(v.get("stageId", "")) + str(v.get("code", ""))]
    if event_stages:
        print(f"  [OK ] stage_table contains {len(event_stages)} stages for {NEW_EVENT}, e.g. {event_stages[:5]}")
    else:
        print(f"  [MISS] stage_table has no stages referencing {NEW_EVENT}")
        ok = False

    # --- 4. enemy database
    edb = list(root.rglob("levels/enemydata/enemy_database.json"))
    print(f"  [{'OK ' if edb else 'MISS'}] levels/enemydata/enemy_database.json: {edb[:1]}")
    if not edb:
        ok = False

    # --- 5. non-UTF-8 json inventory
    bad = []
    for p in root.rglob("*.json"):
        try:
            p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            bad.append(p)
    print(f"  [{'OK ' if not bad else 'WARN'}] non-UTF-8 .json files: {len(bad)}")
    for p in bad[:10]:
        print(f"        {p}")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
