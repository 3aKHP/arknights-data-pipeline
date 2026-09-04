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
    # Producer-side requirement ahead of the PRTS 2.9 recruitment lookup;
    # consumers only read the files they know, so requiring it here is
    # forward-compatible.
    "gacha_table.json",
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

EXCEL_ASSET = "zh_CN-excel.zip"
LEVELS_ASSET = "zh_CN-levels.zip"
STORY_ASSET = "zh_CN.zip"
MANIFEST_ASSET = "manifest.json"

# Versioned consumer contract. PRTS-MCP consumers validate the manifest with an
# EXACT match on this string, so a bump rejects every deployed client: only bump
# after consumers learn to tolerate unknown versions. Additive tightening (new
# required files or schema gates on data the tree already carries) ships within
# the current version.
CONTRACT_VERSION = "prts-mcp-data/v1"

# The workflow downloads this exact torappu artifact.  Keeping the source
# revision in the manifest makes a release auditable even when the binary's
# `flatc --version` output is not sufficient to identify its build.
TORAPPU_FLATC_COMMIT = "37e645f4528248b639b63c35dbb63ee7ae64a315"
TORAPPU_FLATC_SHA256 = "b5adf3dbc4867a3f08acf6e30f32671f114d189093da07453ff2764c6acdd925"

#: index files at zh_CN/ root in the story zip (ASTR-generated indexes plus
#: the summary acceptance sidecars; all optional in the zip)
STORY_INDEX_FILES = [
    "extrastory.json",
    "chardict.json",
    "wordcount.json",
    "storyinfo.json",
    "summaries.json",
    "event_summaries.json",
    "summaries.meta.json",
    "event_summaries.meta.json",
]

#: key tables whose top-level record counts are tracked for regression checks
RECORD_COUNT_TABLES = [
    "gamedata/excel/character_table.json",
    "gamedata/excel/stage_table.json",
    "gamedata/excel/item_table.json",
    "gamedata/excel/enemy_handbook_table.json",
    "gamedata/excel/story_review_table.json",
]
