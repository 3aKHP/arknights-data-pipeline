"""Versioned upstream identity and same-version publication guard."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

ALGORITHM = "akdp-hot-update-fingerprint/v1"


def source_identity(data: dict) -> dict:
    records = data.get("abInfos")
    if not isinstance(records, list) or not records:
        raise ValueError("hot update list has no bundle records")
    rows = []
    names = set()
    for record in records:
        row = tuple(record.get(key) for key in ("name", "hash", "md5"))
        if any(not isinstance(value, str) or not value for value in row):
            raise ValueError("bundle identity requires name, hash and md5")
        if row[0] in names:
            raise ValueError(f"duplicate bundle name: {row[0]}")
        names.add(row[0])
        rows.append(row)
    encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":")).encode()
    return {"fingerprintAlgorithm": ALGORITHM,
            "contentFingerprint": hashlib.sha256(encoded).hexdigest()}


def _gh_json(*args: str):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def published_source(version: str, repo: str) -> tuple[str, dict] | None:
    """Find the highest complete revision of this version, including older pages."""
    from . import contract
    from .check import parse_release_tag

    pages = _gh_json("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100")
    required = {contract.EXCEL_ASSET, contract.LEVELS_ASSET,
                contract.STORY_ASSET, contract.MANIFEST_ASSET}
    candidates = []
    for page in pages:
        for release in page:
            tag = release.get("tag_name", "")
            parsed = parse_release_tag(tag)
            assets = {asset["name"]: asset for asset in release.get("assets", [])}
            if (parsed and parsed[0] == version and not release.get("draft")
                    and not release.get("prerelease") and required <= assets.keys()):
                candidates.append((parsed[1], tag, assets[contract.MANIFEST_ASSET]["id"]))
    if not candidates:
        return None
    _, tag, asset_id = max(candidates)
    manifest = _gh_json("api", "-H", "Accept: application/octet-stream",
                        f"repos/{repo}/releases/assets/{asset_id}")
    if (manifest.get("source") or {}).get("versionId") != version:
        raise ValueError("published manifest version disagrees with release tag")
    return tag, manifest.get("source") or {}


def inspect_remote(remote: dict, repo: str) -> tuple[str, dict | None]:
    version = remote.get("versionId")
    if not isinstance(version, str) or not version:
        raise ValueError("upstream versionId is missing")
    published = published_source(version, repo)
    if published is None:
        return "new_version", None
    tag, source = published
    if source.get("fingerprintAlgorithm") != ALGORITHM or not source.get("contentFingerprint"):
        return "baseline_unknown", None
    if source["contentFingerprint"] == remote["contentFingerprint"]:
        return "unchanged", None
    event = {
        "schemaVersion": 1, "eventType": "upstream_content_drift",
        "repository": repo, "version_id": version, "publishedTag": tag,
        "fingerprintAlgorithm": ALGORITHM,
        "publishedFingerprint": source["contentFingerprint"],
        "observedFingerprint": remote["contentFingerprint"],
        "verdict": "content_drift", "attempt": 1,
    }
    identity = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    event["event_id"] = hashlib.sha256(identity).hexdigest()
    return "content_drift", event


def report(status: str, event: dict | None, workdir: Path) -> None:
    message = f"Upstream provenance: {status}"
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"\n{message}\n")
    if event is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "pipeline-events.jsonl").write_text(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
