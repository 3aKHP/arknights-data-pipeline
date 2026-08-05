"""Publish step: upload the four assets as a single release on this repo.

The factory repo (3aKHP/arknights-data-pipeline) is its own distribution
point — one Release per game version, carrying all three zips as assets.

Tag convention: data-<versionId> (e.g. data-26-08-03-23-34-20_a745fc)
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import contract
from .package import _sha256

#: this repo is the distribution repo
DIST_REPO = "3aKHP/arknights-data-pipeline"
TAG_PREFIX = "data-"

#: all assets go into a single release
RELEASE_ASSETS = [
    contract.EXCEL_ASSET,
    contract.LEVELS_ASSET,
    contract.STORY_ASSET,
    contract.MANIFEST_ASSET,
]

MAX_RETRIES = 3
RETRY_DELAY = 10.0


def _run_with_retry(cmd: list[str], desc: str) -> None:
    """Run a command, retry on network failures."""
    last_err = ""
    for attempt in range(1, MAX_RETRIES + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return
        last_err = f"rc={proc.returncode} stderr={proc.stderr.strip()} stdout={proc.stdout.strip()}"
        if attempt < MAX_RETRIES:
            wait = RETRY_DELAY * attempt
            print(f"  [{desc}] attempt {attempt}/{MAX_RETRIES} failed: {last_err[:200]}")
            print(f"  retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"{desc} failed after {MAX_RETRIES} attempts: {last_err}")


def _release_state(tag: str) -> dict | None:
    proc = subprocess.run(
        ["gh", "release", "view", tag, "-R", DIST_REPO,
         "--json", "isDraft,assets"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _asset_matches(path: Path, remote: dict) -> bool:
    if remote.get("size") != path.stat().st_size:
        return False
    digest = remote.get("digest")
    return isinstance(digest, str) and digest == f"sha256:{_sha256(path)}"


def _missing_or_invalid_assets(state: dict, asset_paths: list[Path]) -> list[Path]:
    remote = {asset.get("name"): asset for asset in state.get("assets", [])}
    return [path for path in asset_paths
            if not _asset_matches(path, remote.get(path.name, {}))]


def publish(dist: Path, *, source_id: str, title_suffix: str, dry_run: bool = True) -> None:
    """Publish dist assets as a single release.

    Two-phase: create the release (lightweight), then upload assets one by
    one with retries. This isolates network failures on large uploads.
    """
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))

    tag = f"{TAG_PREFIX}{source_id}"
    asset_paths = [dist / name for name in RELEASE_ASSETS]
    missing = [p.name for p in asset_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing dist assets: {missing}")

    if dry_run:
        print(f"[publish:dry-run] {DIST_REPO} tag={tag}")
        print(f"  assets: {[p.name for p in asset_paths]}")
        print(f"  title:  Game Data {title_suffix}")
        return

    # Keep the release draft until every asset has been uploaded and verified.
    # A failed run therefore cannot become `latest` and the next run can resume.
    notes_file = dist / "release-notes.md"
    notes_file.write_text(
        "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```",
        encoding="utf-8",
    )
    state = _release_state(tag)
    if state is None:
        _run_with_retry([
            "gh", "release", "create", tag,
            "-R", DIST_REPO,
            "--title", f"Game Data {title_suffix}",
            "--notes-file", str(notes_file),
            "--draft",
            "--latest=false",
        ], "release create draft")
        state = _release_state(tag) or {"isDraft": True, "assets": []}
    elif not state.get("isDraft", False) and _missing_or_invalid_assets(state, asset_paths):
        # GitHub cannot convert a public release back to draft. Upload the
        # manifest first (it is the smallest asset), then replace invalid
        # data assets; consumers that enforce the manifest fail closed while
        # this repair is in progress.
        print(f"  repairing public incomplete release {tag} in place")

    # Upload assets one by one (smallest first, largest last), resuming any
    # already verified uploads left by an interrupted run.
    for asset in sorted(_missing_or_invalid_assets(state, asset_paths),
                        key=lambda p: p.stat().st_size):
        print(f"  uploading {asset.name} ({asset.stat().st_size / 1e6:.1f} MB)")
        _run_with_retry([
            "gh", "release", "upload", tag,
            "-R", DIST_REPO,
            "--clobber",
            str(asset),
        ], f"upload {asset.name}")

    state = _release_state(tag)
    if state is None or _missing_or_invalid_assets(state, asset_paths):
        raise RuntimeError(f"release {tag} is incomplete or has a digest mismatch")
    _run_with_retry(
        ["gh", "release", "edit", tag, "-R", DIST_REPO, "--draft=false", "--latest"],
        "publish verified release",
    )
    print(f"[publish] published {tag} on {DIST_REPO}")
