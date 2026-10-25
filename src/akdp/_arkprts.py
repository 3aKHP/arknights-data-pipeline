"""Run the arkprts CLI with direct GitHub archive downloads.

The github.com archive redirect can carry headers exceeding aiohttp's
limit. Use codeload directly, keeping arkprts' extraction and CLI intact.
"""

from __future__ import annotations

import runpy
import tempfile
from pathlib import Path

import aiohttp
from arkprts.assets import git


async def download_github_tarball(
    repository: str,
    destination: str | Path | None,
    *,
    branch: str = "HEAD",
) -> Path:
    """Download the same tarball without the github.com redirect."""
    if destination is None:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as temporary:
            destination = temporary.name
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://codeload.github.com/{repository}/tar.gz/{branch}"

    async with aiohttp.ClientSession(auto_decompress=False) as session, session.get(url) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            async for chunk in response.content.iter_any():
                output.write(chunk)

    return destination


def main() -> None:
    git.download_github_tarball = download_github_tarball
    runpy.run_module("arkprts.assets", run_name="__main__")


if __name__ == "__main__":
    main()
