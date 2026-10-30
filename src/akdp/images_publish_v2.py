"""Publish verified v2 candidates through resumable immutable GitHub releases."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

from .images_index import _sha256
from .images_parts import SCHEMA, atomic_json, verify
from .images_state import BUILD_STATE_ASSET, load_build_state


class GitHub:
    def __init__(self, repo: str):
        self.repo = repo

    def json(self, *args, body=None):
        command = ["gh", "api", *args]
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def releases(self):
        pages = self.json("--paginate", "--slurp", f"repos/{self.repo}/releases?per_page=100")
        return [release for page in pages for release in page]

    def assets(self, release):
        pages = self.json("--paginate", "--slurp", f"repos/{self.repo}/releases/{release['id']}/assets?per_page=100")
        return {asset["name"]: asset for page in pages for asset in page}

    def index(self, release):
        asset = self.assets(release)["index.json"]
        return self.json("-H", "Accept: application/octet-stream", f"repos/{self.repo}/releases/assets/{asset['id']}")

    def create(self, tag):
        return self.json("--method", "POST", f"repos/{self.repo}/releases", body={
            "tag_name": tag, "name": tag, "draft": True, "make_latest": "false",
            "body": "AKDP v2 image distribution."})

    def delete(self, asset):
        self.json("--method", "DELETE", f"repos/{self.repo}/releases/assets/{asset['id']}")

    def upload(self, release, path):
        subprocess.run(["gh", "release", "upload", release["tag_name"], "-R", self.repo, str(path)],
                       capture_output=True, text=True, check=True)

    def activate(self, release, tag):
        return self.json("--method", "PATCH", f"repos/{self.repo}/releases/{release['id']}", body={
            "tag_name": tag, "target_commitish": release["target_commitish"],
            "draft": False, "make_latest": "false"})


@contextmanager
def _lock(dist):
    import fcntl
    with (dist / ".publication.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _find(releases, tag):
    matches = [release for release in releases if release["tag_name"] == tag or (
        release["draft"] and release.get("name") == tag and release["tag_name"].startswith("untagged-"))]
    if len(matches) > 1:
        raise ValueError(f"ambiguous release: {tag}")
    return matches[0] if matches else None


def _matches(asset, path):
    return (asset.get("state") == "uploaded" and asset.get("size") == path.stat().st_size
            and asset.get("digest") == "sha256:" + _sha256(path))


def _dependencies(github, index, previous):
    releases = github.releases()
    baseline = _find(releases, "images2-baseline-" + index["baselineVersion"])
    predecessor = _find(releases, "images2-" + index["previousVersion"])
    if baseline is None or predecessor is None or baseline["draft"] or predecessor["draft"]:
        raise ValueError("baseline or predecessor is not publicly complete")
    base_index = github.index(baseline)
    if (base_index.get("schemaVersion") != SCHEMA
            or base_index.get("currentVersion") != index["baselineVersion"]
            or base_index.get("baselineParts") != index["baselineParts"]
            or github.index(predecessor) != previous):
        raise ValueError("published dependency differs from candidate lineage")
    assets = github.assets(baseline)
    for part in index["baselineParts"]:
        asset = assets.get(part["file"], {})
        if (asset.get("state") != "uploaded" or asset.get("size") != part["bytes"]
                or asset.get("digest") != "sha256:" + part["sha256"]):
            raise ValueError("published baseline part differs or is missing")


def publish(dist: Path, repo: str, previous=None, *, dry_run=True, github=None):
    """Publish baseline before its sentinel, or a delta after public dependencies.

    Failed requests propagate; reruns reconcile remote assets before continuing.
    Public content is never clobbered. No requests occur during dry-run.
    """
    verify(dist, previous)
    index = json.loads((dist / "index.json").read_text())
    version = index["currentVersion"]
    state_path = dist / BUILD_STATE_ASSET
    if state_path.exists():
        state = load_build_state(state_path)
        if state is None or state.version_id != version:
            raise ValueError("candidate build state version mismatch")
    baseline = version == index["baselineVersion"]
    extra = [BUILD_STATE_ASSET] if state_path.exists() else []
    jobs = [("images2-baseline-" + version, index["baselineParts"], []) ,
            ("images2-" + version, [], extra)] if baseline else [
            ("images2-" + version, index["deltaParts"], extra)]
    if any(len(parts) + 1 + len(extras) > 999 for _, parts, extras in jobs):
        raise ValueError("release asset limit exceeded (signature slot reserved)")
    if dry_run:
        return [{"tag": tag, "assets": len(parts) + 1 + len(extras)} for tag, parts, extras in jobs]
    github = github or GitHub(repo)
    with _lock(dist):
        if not baseline:
            _dependencies(github, index, previous)
        receipts = []
        for tag, parts, extras in jobs:
            names = [part["file"] for part in parts] + ["index.json"] + extras
            release = _find(github.releases(), tag)
            if release is None:
                release = github.create(tag)
            receipt_path = dist / (tag + ".publication.json")
            atomic_json(receipt_path, {"repo": repo, "tag": tag, "release_id": release["id"], "state": "uploading"})
            assets = github.assets(release)
            if set(assets) - set(names):
                raise ValueError("unexpected remote assets")
            for name in names:
                path = dist / name
                asset = assets.get(name)
                if asset and asset.get("state") == "starter" and release["draft"]:
                    github.delete(asset)
                    asset = None
                if asset is None:
                    if not release["draft"]:
                        raise ValueError("incomplete public release; refusing mutation")
                    try:
                        github.upload(release, path)
                    except subprocess.CalledProcessError:
                        # A timeout may follow a successful upload. Inspect before retrying.
                        if name not in github.assets(release):
                            raise
                    assets = github.assets(release)
                    asset = assets.get(name, {})
                if not _matches(asset, path):
                    raise ValueError("remote content differs: " + name)
            assets = github.assets(release)
            if set(assets) != set(names) or any(not _matches(assets[name], dist / name) for name in names):
                raise ValueError("release asset verification failed")
            if release["draft"]:
                release = github.activate(release, tag)
            if release["draft"] or release["tag_name"] != tag:
                raise ValueError("release activation failed")
            receipt = {"repo": repo, "tag": tag, "release_id": release["id"], "state": "public"}
            atomic_json(receipt_path, receipt)
            receipts.append(receipt)
        return receipts
