from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import zlib

import pytest

from akdp import baseline, contract, sqlite_artifact
from akdp.merge import merge_trees
from akdp.package import package_candidate


def _candidate(root: Path) -> None:
    excel = root / "zh_CN/gamedata/excel"
    excel.mkdir(parents=True)
    for name in contract.REQUIRED_EXCEL_FILES:
        (excel / name).write_text("{}", encoding="utf-8")
    (excel / "stage_table.json").write_text('{"stages":{}}', encoding="utf-8")
    (excel / "item_table.json").write_text('{"items":{}}', encoding="utf-8")
    levels = root / "zh_CN/gamedata/levels/enemydata"
    levels.mkdir(parents=True)
    (levels / "enemy_database.json").write_text('{"enemies":[]}', encoding="utf-8")
    (root / "zh_CN/storyinfo.json").write_text("{}", encoding="utf-8")
    (root / "zh_CN/future-file.bin").write_bytes(b"\x00future\xff")
    story = root / "zh_CN/gamedata/story"
    story.mkdir()
    (story / "retired.txt").write_bytes(b"historical raw story")


def _release(root: Path) -> Path:
    candidate, dist = root / "candidate", root / "dist"
    _candidate(candidate)
    package_candidate(candidate, dist, source_info={"versionId": "fixture"})
    return dist


def _rehash_database(dist: Path) -> None:
    database = dist / contract.SQLITE_DATABASE
    compressed = dist / contract.SQLITE_ASSET
    compressed.write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    manifest_path = dist / contract.MANIFEST_ASSET
    manifest = json.loads(manifest_path.read_text())
    value = manifest["sqlite"]
    value.update(size=database.stat().st_size,
                 sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
                 compressedSize=compressed.stat().st_size,
                 compressedSha256=hashlib.sha256(compressed.read_bytes()).hexdigest())
    manifest["assets"][contract.SQLITE_ASSET] = {
        "size": value["compressedSize"], "sha256": value["compressedSha256"],
    }
    manifest_path.write_text(json.dumps(manifest))


def _serve(monkeypatch, dist: Path, *, omit: str | None = None) -> list[str]:
    names = [contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET,
             contract.MANIFEST_ASSET, contract.SQLITE_ASSET]
    assets = [{"name": name, "size": (dist / name).stat().st_size,
               "digest": "sha256:" + hashlib.sha256((dist / name).read_bytes()).hexdigest()}
              for name in names if name != omit]
    monkeypatch.setattr(baseline, "_factory_release_info",
                        lambda: {"tagName": "data-fixture", "assets": assets})
    downloaded = []

    def download(repo, tag, asset, destination):
        downloaded.append(asset)
        shutil.copyfile(dist / asset, destination / asset)

    monkeypatch.setattr(baseline, "_gh_download", download)
    return downloaded


def test_sqlite_only_history_survives_baseline_merge_and_next_release(tmp_path, monkeypatch):
    dist = _release(tmp_path)
    downloaded = _serve(monkeypatch, dist)
    restored = tmp_path / "restored"
    baseline.download_baseline(restored)
    assert contract.SQLITE_ASSET in downloaded
    extraction = tmp_path / "extraction"
    extraction.mkdir()
    candidate = tmp_path / "next-candidate"
    merge_trees(restored, extraction, candidate)
    next_dist = tmp_path / "next-dist"
    package_candidate(candidate, next_dist, source_info={"versionId": "next"})
    with sqlite3.connect(next_dist / contract.SQLITE_DATABASE) as db:
        for path, expected in [("zh_CN/future-file.bin", b"\x00future\xff"),
                               ("zh_CN/gamedata/story/retired.txt", b"historical raw story")]:
            row = db.execute("SELECT b.body FROM sources s JOIN source_blobs b USING(sha256) "
                             "WHERE s.namespace='original' AND s.path=?", (path,)).fetchone()
            assert row is not None
            assert zlib.decompress(row[0]) == expected


def test_old_four_asset_baseline_remains_supported(tmp_path, monkeypatch):
    dist = _release(tmp_path)
    manifest_path = dist / contract.MANIFEST_ASSET
    manifest = json.loads(manifest_path.read_text())
    del manifest["sqlite"]
    del manifest["assets"][contract.SQLITE_ASSET]
    manifest_path.write_text(json.dumps(manifest))
    downloaded = _serve(monkeypatch, dist, omit=contract.SQLITE_ASSET)
    restored = tmp_path / "restored"
    baseline.download_baseline(restored)
    assert baseline._baseline_ready(restored)
    assert contract.SQLITE_ASSET not in downloaded


@pytest.mark.parametrize("failure", ["missing", "decoded-digest", "blob", "path"])
def test_bad_sqlite_never_replaces_previous_baseline(tmp_path, monkeypatch, failure):
    dist = _release(tmp_path)
    if failure in ("blob", "path"):
        with sqlite3.connect(dist / contract.SQLITE_DATABASE) as db:
            if failure == "blob":
                db.execute("UPDATE source_blobs SET body=? WHERE sha256=(SELECT sha256 "
                           "FROM sources WHERE namespace='original' AND path='zh_CN/future-file.bin')",
                           (zlib.compress(b"corrupt"),))
            else:
                db.execute("UPDATE sources SET path='zh_CN/../escaped.bin' "
                           "WHERE namespace='original' AND path='zh_CN/future-file.bin'")
        _rehash_database(dist)
    elif failure == "decoded-digest":
        path = dist / contract.MANIFEST_ASSET
        manifest = json.loads(path.read_text())
        manifest["sqlite"]["sha256"] = "0" * 64
        path.write_text(json.dumps(manifest))
    _serve(monkeypatch, dist, omit=contract.SQLITE_ASSET if failure == "missing" else None)
    previous = tmp_path / "baseline"
    previous.mkdir()
    (previous / "sentinel").write_bytes(b"healthy previous generation")
    with pytest.raises((RuntimeError, ValueError)):
        baseline.download_baseline(previous, clobber=True)
    assert list(previous.iterdir()) == [previous / "sentinel"]
    assert (previous / "sentinel").read_bytes() == b"healthy previous generation"
    assert not (tmp_path / "escaped.bin").exists()
    assert not list(tmp_path.glob(".baseline.staging-*"))


@pytest.mark.parametrize("failure", ["exit", "database-hash", "compressed-hash", "gzip",
                                     "application-id", "schema", "metadata"])
def test_invalid_compiler_output_never_publishes_package(tmp_path, monkeypatch, failure):
    candidate, dist = tmp_path / "candidate", tmp_path / "dist"
    _candidate(candidate)
    real_run = subprocess.run

    def compiler(command, *args, **kwargs):
        if failure == "exit":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="compiler failed")
        result = real_run(command, *args, **kwargs)
        assert result.returncode == 0, result.stderr
        database = Path(command[command.index("--output") + 1])
        compressed = database.with_name(contract.SQLITE_ASSET)
        receipt = database.with_name(database.name + ".meta.json")
        value = json.loads(receipt.read_text())
        if failure == "database-hash":
            value["sha256"] = "0" * 64
        elif failure == "compressed-hash":
            value["compressedSha256"] = "0" * 64
        elif failure == "gzip":
            compressed.write_bytes(gzip.compress(b"unrelated database", mtime=0))
            value["compressedSize"] = compressed.stat().st_size
            value["compressedSha256"] = hashlib.sha256(compressed.read_bytes()).hexdigest()
        else:
            with sqlite3.connect(database) as db:
                if failure == "application-id":
                    db.execute("PRAGMA application_id=0")
                elif failure == "schema":
                    db.execute("PRAGMA user_version=99")
                else:
                    db.execute("UPDATE meta SET value='other' WHERE key='source_version'")
            compressed.write_bytes(gzip.compress(database.read_bytes(), mtime=0))
            value.update(size=database.stat().st_size,
                         sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
                         compressedSize=compressed.stat().st_size,
                         compressedSha256=hashlib.sha256(compressed.read_bytes()).hexdigest())
        receipt.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(sqlite_artifact.subprocess, "run", compiler)
    with pytest.raises((RuntimeError, ValueError)):
        package_candidate(candidate, dist, source_info={"versionId": "fixture"})
    assert not (dist / contract.MANIFEST_ASSET).exists()
    assert not (dist / contract.SQLITE_ASSET).exists()
    assert not (dist / contract.SQLITE_DATABASE).exists()
