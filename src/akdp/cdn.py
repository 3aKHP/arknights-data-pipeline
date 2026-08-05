"""Shared CDN URL helper for HG asset downloads.

Both ``check.py`` (version detection) and ``images_fetch.py`` (art bundle
download) construct CDN URLs from arkprts' NetworkSession.  This module
owns that construction so the resVersion/platform URL scheme has a single
definition.
"""

from __future__ import annotations

from arkprts import network as netn
from arkprts.assets.bundle import asset_path_to_server_filename


def asset_url(session: netn.NetworkSession, path: str, server: str) -> str:
    """Build the CDN URL for a single asset path on a pre-loaded session."""
    platform = session.default_platform or "Android"
    return (
        session.domains[server]["hu"]
        + f"/{platform}/assets/{session.versions[(server, platform)]['resVersion']}/"
        + asset_path_to_server_filename(path)
    )
