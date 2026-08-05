"""Change detection: compare HG's remote versionId with the latest published one.

Short-circuits the pipeline when the client data has not changed, so cron runs
don't produce no-op releases.
"""

from __future__ import annotations

import asyncio
import re
import subprocess


def fetch_remote_version(server: str = "cn") -> dict:
    """Fetch the current hot_update_list version info from HG's CDN via arkprts.

    Uses only the network layer (no flatc / unpack dependencies).
    """
    import json

    from arkprts import network as netn
    from arkprts.assets.bundle import asset_path_to_server_filename

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


def latest_published_version() -> str | None:
    """Read the versionId embedded in the factory repo's latest release tag."""
    proc = subprocess.run(
        ["gh", "release", "view", "-R", DIST_REPO, "--json", "tagName", "--jq", ".tagName"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return parse_version_id_from_tag(proc.stdout.strip())
