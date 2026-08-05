from __future__ import annotations

import hashlib

import pytest

from akdp.baseline import _verify_asset_digest


def test_verify_asset_digest_accepts_github_sha256_schema(tmp_path):
    asset = tmp_path / "asset.zip"
    asset.write_bytes(b"payload")

    _verify_asset_digest(asset, {
        "size": asset.stat().st_size,
        "digest": f"sha256:{hashlib.sha256(b'payload').hexdigest()}",
    })


@pytest.mark.parametrize("metadata", [
    {"size": 7},
    {"size": 7, "digest": "base64-not-supported"},
])
def test_verify_asset_digest_rejects_missing_or_unknown_digest_schema(tmp_path, metadata):
    asset = tmp_path / "asset.zip"
    asset.write_bytes(b"payload")

    with pytest.raises(RuntimeError, match="valid digest"):
        _verify_asset_digest(asset, metadata)
