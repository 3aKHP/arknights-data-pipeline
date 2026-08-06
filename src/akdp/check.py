"""Change detection: compare HG's remote versionId with the latest published one.

Short-circuits the pipeline when the client data has not changed, so cron runs
don't produce no-op releases.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess

from . import contract


def fetch_remote_version(server: str = "cn") -> dict:
    """Fetch the current hot_update_list version info from HG's CDN via arkprts.

    Uses only the network layer (no flatc / unpack dependencies).
    """
    import json

    from arkprts import network as netn

    from . import cdn

    session = netn.NetworkSession(default_server=server)

    async def _go():
        platform = session.default_platform or "Android"
        try:
            if not session.versions[(server, platform)]:
                await session.load_version_config(server, platform)
            url = cdn.asset_url(session, "hot_update_list.json", server)
            async with session.session.get(url) as response:
                response.raise_for_status()
                return json.loads(await response.read())
        finally:
            await session.session.close()

    data = asyncio.run(_go())
    return {
        "versionId": data.get("versionId"),
        "manifestVersion": data.get("manifestVersion"),
        "abCount": len(data.get("abInfos") or []),
    }


_TAG_RE = re.compile(r"(?:data|upstream|gamedata)-(?P<vid>.+?)(?:-v\d+)?$")


def parse_version_id_from_tag(tag: str) -> str | None:
    m = _TAG_RE.search(tag)
    return m.group("vid") if m else None


#: the factory repo is the distribution repo
DIST_REPO = "3aKHP/arknights-data-pipeline"


def version_changed(
    remote_version: str | None,
    published_version: str | None,
    *,
    force: bool = False,
) -> bool:
    """Return whether the pipeline should run, independently of the scheduler."""
    return force or remote_version != published_version


def latest_published_version() -> str | None:
    """Read the latest complete, non-draft factory release version."""
    proc = subprocess.run(
        [
            "gh", "release", "view", "-R", DIST_REPO,
            "--json", "tagName,isDraft,assets",
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if value.get("isDraft"):
        return None
    names = {asset.get("name") for asset in value.get("assets", [])}
    required = {
        contract.EXCEL_ASSET,
        contract.LEVELS_ASSET,
        contract.STORY_ASSET,
        contract.MANIFEST_ASSET,
    }
    if not required.issubset(names):
        return None
    return parse_version_id_from_tag(value.get("tagName", ""))


def _is_image_delta_tag(tag: str) -> bool:
    """Return True if *tag* is an image delta tag (not baseline)."""
    return tag.startswith("images-") and not tag.startswith("images-baseline-")


def latest_published_image_version() -> str | None:
    """Read the latest published image delta release version.

    Searches for ``images-<versionId>`` tags (excluding ``images-baseline-*``).
    Only returns a version if the Release carries ``index.json`` (mirrors the
    JSON pipeline's asset-completeness guard).  Returns the versionId, or None
    if no complete image delta Release exists yet.
    """
    # Step 1: find candidate tags (gh release list doesn't support 'assets').
    proc = subprocess.run(
        ["gh", "release", "list", "-R", DIST_REPO,
         "--json", "tagName,isDraft", "--limit", "200"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        releases = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    candidates = [
        rel["tagName"] for rel in releases
        if not rel.get("isDraft") and _is_image_delta_tag(rel.get("tagName", ""))
    ]
    # Step 2: verify the first candidate has index.json asset.
    for tag in candidates:
        proc = subprocess.run(
            ["gh", "release", "view", tag, "-R", DIST_REPO,
             "--json", "assets"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        names = {a.get("name") for a in data.get("assets", [])}
        if "index.json" in names:
            return tag.removeprefix("images-")
    return None
