"""Fetch step for image bundles: download chararts/skinpack AB packages.

Unlike the JSON pipeline's ``fetch`` (which downloads all gamedata via the
arkprts CLI), this module selectively downloads art-asset bundles whose hashes
changed since the last run.  It uses arkprts' network layer directly (same
pattern as ``check.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from arkprts import network as netn
from arkprts.assets.bundle import asset_path_to_server_filename, unzip_only_file

_logger = logging.getLogger(__name__)

#: AB-path prefixes whose bundles contain operator art (Texture2D/Sprite).
ART_PREFIXES: tuple[str, ...] = ("chararts/", "skinpack/")


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


def _safe_filename(ab_path: str) -> str:
    """Convert an AB path to a cache filename."""
    return ab_path.replace("/", "_").replace("#", "__") + ".ab"


def _asset_url(session: netn.NetworkSession, path: str, server: str) -> str:
    """Build the CDN URL for a single asset path on a pre-loaded session."""
    platform = session.default_platform or "Android"
    return (
        session.domains[server]["hu"]
        + f"/{platform}/assets/{session.versions[(server, platform)]['resVersion']}/"
        + asset_path_to_server_filename(path)
    )


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


async def _run_fetch(
    cache_dir: Path,
    server: str,
    prev_hashes: dict[str, str],
) -> tuple[FetchStats, dict]:
    """Single async entry point: shared session for all network I/O."""
    session = netn.NetworkSession(default_server=server)
    try:
        platform = session.default_platform or "Android"
        if not session.versions[(server, platform)]:
            await session.load_version_config(server, platform)

        # Fetch hot_update_list.
        hul_url = _asset_url(session, "hot_update_list.json", server)
        async with session.session.get(hul_url) as response:
            response.raise_for_status()
            hot_update = json.loads(await response.read())

        version_id = hot_update.get("versionId")
        to_download, unchanged = _changed_bundles(hot_update, prev_hashes)
        to_download_names = [info["name"] for info in to_download]
        _logger.info(
            "images-fetch: %d bundles to download, %d unchanged (versionId=%s)",
            len(to_download_names), unchanged, version_id,
        )
        stats = FetchStats(skipped_unchanged=unchanged)

        # Download changed bundles with bounded concurrency, writing each
        # to disk immediately (no RAM buffering).
        if to_download_names:
            sem = asyncio.Semaphore(8)

            async def _one(path: str) -> None:
                async with sem:
                    url = _asset_url(session, path, server)
                    try:
                        async with session.session.get(url) as response:
                            response.raise_for_status()
                            zipped = await response.read()
                        data = unzip_only_file(zipped)
                        (cache_dir / _safe_filename(path)).write_bytes(data)
                        stats.downloaded.append(path)
                    except Exception as exc:  # noqa: BLE001
                        stats.failed.append(
                            {"bundle": path, "error": f"{type(exc).__name__}: {exc}"}
                        )

            await asyncio.gather(*(_one(n) for n in to_download_names))
            stats.downloaded.sort()

        # Build hash map: only bundles actually present in cache.
        # Failed downloads are excluded so they retry on the next run.
        hot_update_hashes = {
            info["name"]: info["hash"]
            for info in hot_update.get("abInfos", [])
            if any(info["name"].startswith(p) for p in ART_PREFIXES)
        }
        succeeded_set = set(stats.downloaded)
        all_hashes: dict[str, str] = {}
        for name, h in hot_update_hashes.items():
            if name in succeeded_set:
                all_hashes[name] = h
            elif name not in to_download_names and (cache_dir / _safe_filename(name)).exists():
                all_hashes[name] = h

        return stats, {"versionId": version_id, "hashes": all_hashes}
    finally:
        await session.session.close()


def fetch_image_bundles(
    cache_dir: Path,
    *,
    server: str = "cn",
    prev_hashes: dict[str, str] | None = None,
) -> tuple[FetchStats, dict]:
    """Download changed art bundles, save AB bytes to *cache_dir*.

    Returns (stats, hot_update_info) where hot_update_info carries the versionId
    and the abInfos hash map.  Only bundles that were actually downloaded or
    already cached are included in the hash map — failed downloads are excluded
    so they are retried on the next run.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_run_fetch(cache_dir, server, prev_hashes or {}))
