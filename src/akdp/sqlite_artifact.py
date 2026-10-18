"""Build and independently verify the full SQLite sidecar with the Rust compiler."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

from . import contract

SQLITE_CONTRACT = "prts-mcp-sqlite/v1"
APPLICATION_ID = 0x50525453
SCHEMA_VERSION = 1
INDEX_VERSION = 1


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def descriptor(manifest: dict) -> dict:
    """Validate the additive DB descriptor without changing the LTS contract."""
    value = manifest.get("sqlite")
    if not isinstance(value, dict):
        raise ValueError("missing SQLite descriptor")
    expected = {
        "contractVersion": SQLITE_CONTRACT, "schemaVersion": SCHEMA_VERSION,
        "indexVersion": INDEX_VERSION, "language": "zh_CN",
        "sourceVersion": manifest.get("source", {}).get("versionId", "manual"),
        "publicationRevision": manifest.get("publicationRevision", 1),
        "asset": contract.SQLITE_ASSET,
    }
    for key, wanted in expected.items():
        actual = value.get(key)
        if type(actual) is not type(wanted) or actual != wanted:
            raise ValueError(f"SQLite descriptor mismatch: {key}")
    for key in ("size", "compressedSize"):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise ValueError(f"invalid SQLite {key}")
    for key in ("sha256", "compressedSha256"):
        if not isinstance(value.get(key), str) or not re.fullmatch("[0-9a-f]{64}", value[key]):
            raise ValueError(f"invalid SQLite {key}")
    if not isinstance(value.get("compilerVersion"), str) or not value["compilerVersion"]:
        raise ValueError("missing SQLite compilerVersion")
    asset = manifest.get("assets", {}).get(contract.SQLITE_ASSET, {})
    if type(asset.get("size")) is not int or asset.get("sha256") != value["compressedSha256"] or asset.get("size") != value["compressedSize"]:
        raise ValueError("SQLite compressed asset differs from descriptor")
    return value


def _verify(database: Path, compressed: Path, value: dict) -> None:
    for path, size, sha in (
        (database, value["size"], value["sha256"]),
        (compressed, value["compressedSize"], value["compressedSha256"]),
    ):
        if path.stat().st_size != size or _hash(path) != sha:
            raise ValueError(f"SQLite compiler output checksum/size mismatch: {path.name}")
    # Verify gzip CRC/EOF and its decoded digest, not just two unrelated files.
    hasher = hashlib.sha256()
    count = 0
    with gzip.open(compressed, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            count += len(chunk)
            hasher.update(chunk)
    if count != value["size"] or hasher.hexdigest() != value["sha256"]:
        raise ValueError("SQLite gzip does not contain the declared database")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("invalid SQLite application ID")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("unsupported SQLite schema")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        metadata = dict(db.execute("SELECT key,value FROM meta"))
        required = {
            "sources_complete": "1", "derived_complete": "1",
            "compiler_version": value["compilerVersion"],
            "contract": SQLITE_CONTRACT, "index_version": str(INDEX_VERSION),
            "source_version": value["sourceVersion"],
            "publication_revision": str(value["publicationRevision"]),
            "language": "zh_CN",
        }
        if any(metadata.get(k) != v for k, v in required.items()):
            raise ValueError("SQLite metadata differs from descriptor")


def build_sqlite(
    candidate: Path, dist: Path, *, source_info: dict | None, revision: int,
    compiler: Path | str | None = None,
) -> dict:
    executable = compiler or os.environ.get("PRTS_DATA_COMPILER") or shutil.which("prts-mcp-data-compiler")
    if not executable:
        raise RuntimeError("PRTS_DATA_COMPILER must name the Rust data compiler; no JSON-only publication fallback")
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sqlite-build-", dir=dist) as temporary:
        root = Path(temporary)
        database = root / contract.SQLITE_DATABASE
        compressed = root / contract.SQLITE_ASSET
        command = [
            str(executable), "--input", str(candidate.resolve()),
            "--output", str(database.resolve()), "--source-version",
            (source_info or {}).get("versionId") or "manual", "--revision", str(revision),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"SQLite compiler failed ({result.returncode}): {result.stderr.strip()}")
        value = json.loads(database.with_name(database.name + ".meta.json").read_text(encoding="utf-8"))
        descriptor({
            "source": source_info or {}, "publicationRevision": revision, "sqlite": value,
            "assets": {contract.SQLITE_ASSET: {
                "sha256": value.get("compressedSha256"), "size": value.get("compressedSize"),
            }},
        })
        _verify(database, compressed, value)
        # The outer manifest is written last by package_candidate.
        os.replace(compressed, dist / contract.SQLITE_ASSET)
        os.replace(database, dist / contract.SQLITE_DATABASE)
        return value
