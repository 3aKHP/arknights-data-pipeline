"""Data contract shared with PRTS-MCP consumers.

Keep in sync with:
  - python/src/prts_mcp/data/sync.py (GAMEDATA_FILES, LEVELS_REQUIRED_FILES, story contract)
  - ts/src/data/sync.ts
"""

#: excel files PRTS-MCP requires under zh_CN/gamedata/excel/
REQUIRED_EXCEL_FILES = [
    "character_table.json",
    "handbook_info_table.json",
    "charword_table.json",
    "story_review_table.json",
    "enemy_handbook_table.json",
    "stage_table.json",
    "zone_table.json",
    "item_table.json",
]

#: files PRTS-MCP requires under zh_CN/gamedata/levels/
REQUIRED_LEVELS_FILES = [
    "enemydata/enemy_database.json",
]

#: files PRTS-MCP requires inside the story zip (zh_CN.zip)
REQUIRED_STORY_FILES = [
    "gamedata/excel/story_review_table.json",
    "storyinfo.json",
]

#: distribution targets: asset name -> top-level gamedata subtree(s) included
PACKAGE_GAMEDATA_REPO = "3aKHP/ArknightsGameData"
PACKAGE_STORY_REPO = "3aKHP/ArknightsStoryJson"

EXCEL_ASSET = "zh_CN-excel.zip"
LEVELS_ASSET = "zh_CN-levels.zip"
STORY_ASSET = "zh_CN.zip"

#: index files at zh_CN/ root in the story zip (ASTR-generated)
STORY_INDEX_FILES = [
    "extrastory.json",
    "chardict.json",
    "wordcount.json",
    "storyinfo.json",
    "summaries.json",
    "event_summaries.json",
]

#: key tables whose top-level record counts are tracked for regression checks
RECORD_COUNT_TABLES = [
    "gamedata/excel/character_table.json",
    "gamedata/excel/stage_table.json",
    "gamedata/excel/item_table.json",
    "gamedata/excel/enemy_handbook_table.json",
    "gamedata/excel/story_review_table.json",
]
