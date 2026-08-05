"""Fetch step for image bundles: download chararts/skinpack AB packages.

Unlike the JSON pipeline's ``fetch`` (which downloads all gamedata via the
arkprts CLI), this module selectively downloads art-asset bundles whose hashes
changed since the last run.  It uses arkprts' network layer directly (same
pattern as ``check.py``).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from arkprts import network as netn
from arkprts.assets.bundle import asset_path_to_server_filename, unzip_only_file

_logger = logging.getLogger(__name__)

#: AB-path prefixes whose bundles contain operator art (Texture2D/Sprite).
ART_PREFIXES: tuple[str, ...] = ("chararts/", "skinpack/")

#: Prefixes to skip entirely (no textures, or out of scope).
SKIP_PREFIXES: tuple[str, ...] = ("charpack/",)


@dataclass
class FetchStats:
    downloaded: list[str] = field(default_factory=list)
    skipped_unchanged: int = 0
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "downloaded": len(self.downloaded),
            "skipped_unchanged": self.skipped_unchanged,
            "failed": len(self.failed),
            "failed_details": self.failed[:20],
        }


def _load_hot_update_list(server: str = "cn") -> dict:
    """Fetch the current hot_update_list from HG CDN (network layer only)."""
    session = netn.NetworkSession(default_server=server)

    async def _go():
        platform = session.default_platform or "Android"
        try:
            if not session.versions[(server, platform)]:
                await session.load_version_config(server, platform)
            url = (
                session.domains[server]["hu"]
                + f"/{platform}/assets/{session.versions[(server, platform)]['resVersion']}/"
                + asset_path_to_server_filename("hot_update_list.json")
            )
            async with session.session.get(url) as response:
                response.raise_for_status()
                return json.loads(await response.read())
        finally:
            await session.session.close()

    return asyncio.run(_go())


async def _download_bundle(path: str, server: str = "cn") -> bytes:
    """Download and unzip a single AB bundle, returning raw AB bytes."""
    session = netn.NetworkSession(default_server=server)
    try:
        platform = session.default_platform or "Android"
        if not session.versions[(server, platform)]:
            await session.load_version_config(server, platform)
        url = (
            session.domains[server]["hu"]
            + f"/{platform}/assets/{session.versions[(server, platform)]['resVersion']}/"
            + asset_path_to_server_filename(path)
        )
        async with session.session.get(url) as response:
            response.raise_for_status()
            zipped = await response.read()
        return unzip_only_file(zipped)
    finally:
        await session.session.close()


async def _download_many(
    paths: list[str], server: str, max_concurrency: int = 8
) -> dict[str, bytes]:
    """Download multiple bundles with bounded concurrency."""
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, bytes] = {}
    errors: dict[str, str] = {}

    async def _one(p: str) -> None:
        async with sem:
            try:
                results[p] = await _download_bundle(p, server)
            except Exception as exc:  # noqa: BLE001
                errors[p] = f"{type(exc).__name__}: {exc}"

    await asyncio.gather(*(_one(p) for p in paths))
    return results, errors  # type: ignore[return-value]


def _changed_bundles(
    hot_update: dict, prev_hashes: dict[str, str]
) -> tuple[list[dict], int]:
    """Return (art bundles to download, count of unchanged art bundles)."""
    to_download: list[dict] = []
    unchanged = 0
    for info in hot_update.get("abInfos", []):
        name = info.get("name", "")
        if not any(name.startswith(p) for p in ART_PREFIXES):
            continue
        h = info.get("hash", "")
        if name in prev_hashes and prev_hashes[name] == h:
            unchanged += 1
        else:
            to_download.append(info)
    return to_download, unchanged


def fetch_image_bundles(
    cache_dir: Path,
    *,
    server: str = "cn",
    prev_hashes: dict[str, str] | None = None,
) -> tuple[FetchStats, dict]:
    """Download changed art bundles, save AB bytes to *cache_dir*.

    Returns (stats, hot_update_info) where hot_update_info carries the versionId
    and the full abInfos hash map for persistence by the caller.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    prev_hashes = prev_hashes or {}

    hot_update = _load_hot_update_list(server=server)
    version_id = hot_update.get("versionId")
    all_hashes = {
        info["name"]: info["hash"]
        for info in hot_update.get("abInfos", [])
        if any(info["name"].startswith(p) for p in ART_PREFIXES)
    }

    to_download, unchanged = _changed_bundles(hot_update, prev_hashes)
    names = [info["name"] for info in to_download]
    _logger.info(
        "images-fetch: %d bundles to download, %d unchanged (versionId=%s)",
        len(names), unchanged, version_id,
    )

    stats = FetchStats(skipped_unchanged=unchanged)

    if not names:
        return stats, {"versionId": version_id, "hashes": all_hashes}

    downloaded, errors = asyncio.run(_download_many(names, server))

    for name, data in downloaded.items():
        safe = name.replace("/", "_").replace("#", "__")
        (cache_dir / f"{safe}.ab").write_bytes(data)
        stats.downloaded.append(name)

    for name, err in errors.items():
        stats.failed.append({"bundle": name, "error": err})

    stats.downloaded.sort()
    return stats, {"versionId": version_id, "hashes": all_hashes}
