"""Publish image packages to GitHub Releases.

Creates Releases with ``images-*`` tag prefix — never sets ``--latest`` so
the JSON data pipeline's ``data-*`` Releases remain the repo's latest.

- Baseline: ``images-baseline-<ver>`` with 6 shard assets
- Delta:    ``images-<ver>`` with delta zip + index.json
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from .package import _sha256  # reuse the proven helper

_logger = logging.getLogger(__name__)

DIST_REPO = "3aKHP/arknights-data-pipeline"
MAX_RETRIES = 3
RETRY_DELAY = 10.0


def _run_with_retry(cmd: list[str], desc: str) -> None:
    last_err = ""
    for attempt in range(1, MAX_RETRIES + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return
        last_err = f"rc={proc.returncode} stderr={proc.stderr.strip()[:300]}"
        if attempt < MAX_RETRIES:
            wait = RETRY_DELAY * attempt
            _logger.info("  [%s] attempt %d/%d failed, retry in %ss", desc, attempt, MAX_RETRIES, wait)
            time.sleep(wait)
    raise RuntimeError(f"{desc} failed after {MAX_RETRIES} attempts: {last_err}")


def _release_exists(tag: str) -> bool:
    proc = subprocess.run(
        ["gh", "release", "view", tag, "-R", DIST_REPO, "--json", "isDraft"],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def publish_images(
    dist_dir: Path,
    *,
    version_id: str,
    mode: str,
    dry_run: bool = True,
) -> None:
    """Publish packaged images to GitHub Releases.

    *mode* is "baseline" or "delta".  Never sets --latest.
    """
    manifest = json.loads((dist_dir / "index.json").read_text(encoding="utf-8"))

    if mode == "baseline":
        _publish_baseline(dist_dir, version_id, manifest, dry_run)
    else:
        _publish_delta(dist_dir, version_id, manifest, dry_run)


def _publish_baseline(
    dist_dir: Path, version_id: str, manifest: dict, dry_run: bool,
) -> None:
    tag = f"images-baseline-{version_id}"

    shard_names = sorted(manifest.get("shards", {}).values())
    shard_paths = [dist_dir / n for n in shard_names if (dist_dir / n).exists()]
    sentinel = dist_dir / f"images-delta-{version_id}.zip"
    index_path = dist_dir / "index.json"

    if dry_run:
        print(f"[images-publish:dry-run] baseline {DIST_REPO} tag={tag}")
        print(f"  shards: {[p.name for p in shard_paths]}")
        print(f"  sentinel: {sentinel.name}")
        print(f"  index: {index_path.name}")
        return

    # Create baseline Release (not latest).
    notes_file = dist_dir / "baseline-notes.md"
    notes_file.write_text(
        "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```",
        encoding="utf-8",
    )
    if not _release_exists(tag):
        _run_with_retry([
            "gh", "release", "create", tag,
            "-R", DIST_REPO,
            "--title", f"Image Baseline {version_id}",
            "--notes-file", str(notes_file),
            "--latest=false",
        ], f"create baseline {tag}")

    # Upload shard assets.
    for asset in shard_paths + [sentinel]:
        _logger.info("  uploading %s (%.1f MB)", asset.name, asset.stat().st_size / 1e6)
        _run_with_retry([
            "gh", "release", "upload", tag,
            "-R", DIST_REPO, "--clobber", str(asset),
        ], f"upload {asset.name}")

    # Create sentinel delta Release.
    delta_tag = f"images-{version_id}"
    if not _release_exists(delta_tag):
        _run_with_retry([
            "gh", "release", "create", delta_tag,
            "-R", DIST_REPO,
            "--title", f"Image Delta {version_id} (sentinel)",
            "--notes", "Sentinel delta (0 new images). See baseline.",
            "--latest=false",
        ], f"create sentinel {delta_tag}")
    _run_with_retry([
        "gh", "release", "upload", delta_tag,
        "-R", DIST_REPO, "--clobber", str(index_path),
    ], "upload index.json to sentinel")
    _logger.info("[images-publish] baseline %s + sentinel %s published", tag, delta_tag)


def _publish_delta(
    dist_dir: Path, version_id: str, manifest: dict, dry_run: bool,
) -> None:
    tag = f"images-{version_id}"
    delta_zip = dist_dir / f"images-delta-{version_id}.zip"
    index_path = dist_dir / "index.json"

    if dry_run:
        print(f"[images-publish:dry-run] delta {DIST_REPO} tag={tag}")
        print(f"  delta: {delta_zip.name} ({delta_zip.stat().st_size / 1e6:.1f} MB)")
        print(f"  index: {index_path.name}")
        return

    notes_file = dist_dir / "delta-notes.md"
    notes_file.write_text(
        "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```",
        encoding="utf-8",
    )
    if not _release_exists(tag):
        _run_with_retry([
            "gh", "release", "create", tag,
            "-R", DIST_REPO,
            "--title", f"Image Delta {version_id}",
            "--notes-file", str(notes_file),
            "--latest=false",
        ], f"create delta {tag}")

    for asset in [delta_zip, index_path]:
        _logger.info("  uploading %s (%.1f MB)", asset.name, asset.stat().st_size / 1e6)
        _run_with_retry([
            "gh", "release", "upload", tag,
            "-R", DIST_REPO, "--clobber", str(asset),
        ], f"upload {asset.name}")

    _logger.info("[images-publish] delta %s published", tag)
