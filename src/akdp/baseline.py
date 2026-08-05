"""Baseline handling: download the current factory release and extract it.

Primary source: the factory repo's latest Release (single release, three data assets
and an optional manifest). If no factory Release exists, the operator must provide a
controlled local baseline; this pipeline never fetches the legacy upstream repos.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from . import contract
from .publish import DIST_REPO


def _gh_download(repo: str, tag: str | None, asset: str, dest: Path) -> None:
    cmd = ["gh", "release", "download"]
    if tag is not None:
        cmd.append(tag)
    cmd.extend(["-R", repo, "-p", asset, "--clobber", "-D", str(dest)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # one retry
    if proc.returncode != 0:
        raise RuntimeError(f"baseline download failed: {' '.join(cmd)}\n{proc.stderr}")


def _factory_release_info() -> dict | None:
    proc = subprocess.run(
        ["gh", "release", "view", "-R", DIST_REPO, "--json", "tagName,isDraft,assets"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout).lower()
        if "no releases" in error or "not found" in error or "404" in error:
            return None
        raise RuntimeError(f"factory release lookup failed: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("factory release lookup returned invalid JSON") from exc


def _verify_asset_digest(path: Path, metadata: dict) -> None:
    expected_size = metadata.get("size")
    expected_digest = metadata.get("digest")
    actual_size = path.stat().st_size
    actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if isinstance(expected_size, int) and expected_size != actual_size:
        raise RuntimeError(
            f"baseline asset size mismatch for {path.name}: "
            f"expected {expected_size}, got {actual_size}"
        )
    if isinstance(expected_digest, str) and expected_digest != f"sha256:{actual_digest}":
        raise RuntimeError(
            f"baseline asset digest mismatch for {path.name}: "
            f"expected {expected_digest}, got sha256:{actual_digest}"
        )


def _verify_manifest(staging: Path, release: dict) -> None:
    manifest_path = staging / contract.MANIFEST_ASSET
    if not manifest_path.exists():
        return  # Transition compatibility for the first factory Release.
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["contractVersion"] != contract.CONTRACT_VERSION:
            raise ValueError(f"unsupported contractVersion {manifest['contractVersion']!r}")
        tag = str(release.get("tagName", ""))
        source = manifest.get("source", {})
        if not isinstance(source, dict):
            raise TypeError("manifest source must be an object")
        source_version = source.get("versionId")
        if tag.startswith("data-") and source_version != tag.removeprefix("data-"):
            raise ValueError("manifest source version does not match release tag")
        for name in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
            expected = manifest["assets"][name]
            _verify_asset_digest(staging / name, {
                "size": int(expected["size"]),
                "digest": f"sha256:{expected['sha256']}",
            })
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"factory manifest verification failed: {exc}") from exc


def _baseline_ready(baseline: Path) -> bool:
    required = [
        *(baseline / "zh_CN/gamedata/excel" / name
          for name in contract.REQUIRED_EXCEL_FILES),
        *(baseline / "zh_CN/gamedata/levels" / name
          for name in contract.REQUIRED_LEVELS_FILES),
        *(baseline / "zh_CN" / name for name in contract.REQUIRED_STORY_FILES),
    ]
    return all(path.is_file() for path in required)


def _activate_baseline(candidate: Path, baseline: Path) -> None:
    """Replace the baseline only after a complete candidate has been built."""
    previous = baseline.with_name(f".{baseline.name}.previous-{uuid4().hex}")
    had_previous = baseline.exists()
    if had_previous:
        baseline.replace(previous)
    try:
        candidate.replace(baseline)
    except Exception:
        if had_previous and previous.exists():
            previous.replace(baseline)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def download_baseline(baseline: Path, *, clobber: bool = False) -> None:
    """Fetch the factory release assets into `baseline` and unzip them."""
    if not clobber and _baseline_ready(baseline):
        return
    baseline.parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(
        prefix=f".{baseline.name}.staging-", dir=baseline.parent,
    ))
    staging = candidate / ".zips"
    staging.mkdir()
    try:
        factory = _factory_release_info()
        use_factory = factory is not None
        if use_factory:
            tag = factory.get("tagName")
            if not isinstance(tag, str) or not tag:
                raise RuntimeError("factory release has no tag")
            remote_assets = {
                asset.get("name"): asset for asset in factory.get("assets", [])
            }
            required = (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET)
            if any(name not in remote_assets for name in required):
                raise RuntimeError(f"factory release {tag} is missing a required data asset")
            for asset in required:
                _gh_download(DIST_REPO, tag, asset, staging)
                _verify_asset_digest(staging / asset, remote_assets[asset])
            if contract.MANIFEST_ASSET in remote_assets:
                _gh_download(DIST_REPO, tag, contract.MANIFEST_ASSET, staging)
                _verify_manifest(staging, factory)
        else:
            raise RuntimeError(
                "factory has no published Release; seed work/baseline from a "
                "controlled snapshot before running the pipeline"
            )

        for asset in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
            with zipfile.ZipFile(staging / asset) as zf:
                zf.extractall(candidate)
        shutil.rmtree(staging)
        if not _baseline_ready(candidate):
            raise RuntimeError("downloaded baseline is missing required contract files")
        _activate_baseline(candidate, baseline)
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise
