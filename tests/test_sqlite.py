import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import pytest

from akdp import contract
from akdp.sqlite_artifact import build_sqlite, validate_sqlite_descriptor


def _input(root: Path) -> bytes:
    excel = root / "zh_CN/gamedata/excel"
    excel.mkdir(parents=True)
    for name in contract.REQUIRED_EXCEL_FILES:
        (excel / name).write_text("{}", encoding="utf-8")
    (excel / "stage_table.json").write_text('{"stages":{}}', encoding="utf-8")
    (excel / "item_table.json").write_text('{"items":{}}', encoding="utf-8")
    raw = (b'{"char_x":{"name":"x","future":{"u64":18446744073709551615,'
           b'"empty":[],"object":{},"null":null}}}')
    (excel / "character_table.json").write_bytes(raw)
    levels = root / "zh_CN/gamedata/levels/enemydata"
    levels.mkdir(parents=True)
    (levels / "enemy_database.json").write_text('{"enemies":[]}', encoding="utf-8")
    (root / "zh_CN/storyinfo.json").write_text("{}", encoding="utf-8")
    (root / "zh_CN/future-file.bin").write_bytes(b"\0future\xff")
    return raw


def test_sqlite_sidecar_uses_real_compiler_and_roundtrips_complete_fields(tmp_path):
    root = tmp_path / "candidate"
    raw = _input(root)
    dist = tmp_path / "dist"
    value = build_sqlite(root, dist, source_info={"versionId": "fixture"}, revision=2)
    assert value["sourceVersion"] == "fixture"
    assert value["publicationRevision"] == 2
    compressed = dist / contract.SQLITE_ASSET
    with gzip.open(compressed, "rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == value["sha256"]
    with sqlite3.connect(dist / contract.SQLITE_DATABASE) as db:
        body = db.execute(
            "SELECT b.body FROM sources s JOIN source_blobs b USING(sha256) "
            "WHERE s.namespace='excel' AND s.path='character_table.json'"
        ).fetchone()[0]
        assert zlib.decompress(body) == raw
        payload = db.execute(
            "SELECT payload FROM entities WHERE dataset='character_table' AND key='char_x'"
        ).fetchone()[0]
        assert json.loads(payload)["future"]["u64"] == 2**64 - 1
        assert db.execute(
            "SELECT count(*) FROM sources "
            "WHERE namespace='original' AND path='zh_CN/future-file.bin'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("source_info", [
    None,
    {},
    {"versionId": None},
    {"versionId": ""},
])
def test_sqlite_sidecar_normalizes_missing_source_version(tmp_path, source_info):
    root = tmp_path / "candidate"
    _input(root)
    dist = tmp_path / "dist"
    value = build_sqlite(root, dist, source_info=source_info, revision=1)
    assert value["sourceVersion"] == "manual"
    validate_sqlite_descriptor({
        "source": source_info or {},
        "sqlite": value,
        "assets": {contract.SQLITE_ASSET: {
            "size": value["compressedSize"],
            "sha256": value["compressedSha256"],
        }},
    })
    with sqlite3.connect(dist / contract.SQLITE_DATABASE) as db:
        assert db.execute(
            "SELECT value FROM meta WHERE key='source_version'"
        ).fetchone()[0] == "manual"


def test_sqlite_compiler_is_required_no_json_only_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("PRTS_DATA_COMPILER", raising=False)
    monkeypatch.setattr("akdp.sqlite_artifact.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="no JSON-only"):
        build_sqlite(tmp_path / "candidate", tmp_path / "dist", source_info=None, revision=1)


@pytest.mark.parametrize("field,bad", [
    ("schemaVersion", True),
    ("indexVersion", 99),
    ("language", "en_US"),
    ("publicationRevision", 0),
    ("compressedSize", False),
    ("sha256", "bad"),
    ("sourceVersion", "other"),
])
def test_descriptor_rejects_incompatible_or_ambiguous_metadata(field, bad):
    value = {
        "contractVersion": "prts-mcp-sqlite/v1", "schemaVersion": 1, "indexVersion": 1,
        "language": "zh_CN", "sourceVersion": "fixture", "publicationRevision": 1,
        "compilerVersion": "test", "asset": contract.SQLITE_ASSET,
        "size": 1, "sha256": "a" * 64, "compressedSize": 1, "compressedSha256": "b" * 64,
    }
    value[field] = bad
    with pytest.raises(ValueError):
        validate_sqlite_descriptor({
            "source": {"versionId": "fixture"}, "sqlite": value,
            "assets": {contract.SQLITE_ASSET: {"size": 1, "sha256": "b" * 64}},
        })
