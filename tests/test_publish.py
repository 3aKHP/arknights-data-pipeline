import hashlib
import json
from pathlib import Path

import pytest

from akdp import contract, publish


def _make_dist(root: Path, *, revision: int | None = None) -> tuple[Path, dict[str, str]]:
    dist = root / "dist"
    dist.mkdir()
    for name in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
        path = dist / name
        path.write_bytes(name.encode("utf-8"))
    manifest: dict = {
        "contractVersion": contract.CONTRACT_VERSION,
        "source": {"versionId": "test" if revision is None else "V"},
        "validation": {"errors": []},
        "assets": {name: {"size": (dist / name).stat().st_size,
                          "sha256": hashlib.sha256((dist / name).read_bytes()).hexdigest()}
                   for name in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET)},
    }
    if revision is not None:
        manifest["publicationRevision"] = revision
    (dist / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    hashes = {
        name: hashlib.sha256((dist / name).read_bytes()).hexdigest()
        for name in publish.RELEASE_ASSETS
    }
    return dist, hashes


def _complete_state(dist: Path, hashes: dict[str, str], *, draft: bool) -> dict:
    return {
        "isDraft": draft,
        "assets": [
            {
                "name": name,
                "size": (dist / name).stat().st_size,
                "digest": f"sha256:{hashes[name]}",
            }
            for name in publish.RELEASE_ASSETS
        ],
    }


def test_publish_keeps_release_draft_until_verified(tmp_path, monkeypatch):
    dist, hashes = _make_dist(tmp_path)
    states = [
        None,
        {"isDraft": True, "assets": []},
        _complete_state(dist, hashes, draft=True),
    ]
    commands: list[tuple[list[str], str]] = []
    monkeypatch.setattr(publish, "_release_state", lambda _tag: states.pop(0))
    monkeypatch.setattr(
        publish,
        "_run_with_retry",
        lambda command, description: commands.append((command, description)),
    )

    publish.publish(dist, source_id="test", title_suffix="test", dry_run=False)

    assert any("--draft" in command for command, _ in commands)
    assert any("--draft=false" in command for command, _ in commands)
    assert any(
        any(str(item).endswith("/" + contract.MANIFEST_ASSET) for item in command)
        for command, _ in commands
    )
    assert any("--clobber" in command for command, _ in commands)


def test_latest_published_version_ignores_incomplete_release(monkeypatch):
    import subprocess

    incomplete = json.dumps({
        "tagName": "data-test",
        "isDraft": False,
        "assets": [{"name": contract.EXCEL_ASSET}],
    })
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=incomplete, stderr=""
        ),
    )
    from akdp.check import latest_published_version

    assert latest_published_version() is None


def test_force_runs_even_when_version_is_unchanged():
    from akdp.check import version_changed

    assert not version_changed("same", "same")
    assert version_changed("same", "same", force=True)


def test_publish_repairs_public_incomplete_release_without_reopen(tmp_path, monkeypatch):
    dist, hashes = _make_dist(tmp_path)
    states = [
        {"isDraft": False, "assets": []},
        _complete_state(dist, hashes, draft=False),
    ]
    commands: list[list[str]] = []
    monkeypatch.setattr(publish, "_release_state", lambda _tag: states.pop(0))
    monkeypatch.setattr(
        publish,
        "_run_with_retry",
        lambda command, _description: commands.append(command),
    )

    publish.publish(dist, source_id="test", title_suffix="test", dry_run=False)

    assert any("--clobber" in command for command in commands)
    assert not any("--draft" in command for command in commands)


def test_latest_published_image_version_skips_incomplete_and_baseline(monkeypatch):
    """latest_published_image_version should skip drafts, baseline tags,
    and releases missing index.json."""
    import subprocess

    # Step 1: gh release list returns tag list (no assets field supported).
    release_list = json.dumps([
        {"tagName": "data-v1", "isDraft": False},
        {"tagName": "images-baseline-v1", "isDraft": False},
        {"tagName": "images-v0", "isDraft": False},   # incomplete
        {"tagName": "images-v1", "isDraft": False},   # complete
    ])
    # Step 2: gh release view returns assets for each candidate.
    view_responses = {
        "images-v0": json.dumps({"assets": []}),  # missing index.json
        "images-v1": json.dumps({"assets": [{"name": "index.json"}, {"name": "images-delta-v1.zip"}]}),
    }
    call_count = [0]

    def mock_run(*args, **kwargs):
        cmd = args[0]
        call_count[0] += 1
        if "release" in cmd and "list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=release_list, stderr="")
        if "release" in cmd and "view" in cmd:
            tag = cmd[cmd.index("view") + 1]
            resp = view_responses.get(tag, json.dumps({"assets": []}))
            return subprocess.CompletedProcess(cmd, 0, stdout=resp, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    from akdp.check import latest_published_image_version

    result = latest_published_image_version()
    assert result == "v1"  # skips data-, baseline, incomplete images-v0


def test_latest_published_image_version_none_when_no_images(monkeypatch):
    import subprocess

    release_list = json.dumps([
        {"tagName": "data-v1", "isDraft": False},
    ])
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=release_list, stderr="",
        ),
    )
    from akdp.check import latest_published_image_version

    assert latest_published_image_version() is None


# --- revision releases (datarev namespace) ---


def test_publish_revision_mode_uses_datarev_tag_without_latest(tmp_path, monkeypatch):
    dist, hashes = _make_dist(tmp_path, revision=2)
    states = [
        None,
        {"isDraft": True, "assets": []},
        _complete_state(dist, hashes, draft=True),
    ]
    commands: list[list[str]] = []
    monkeypatch.setattr(publish, "_release_state", lambda _tag: states.pop(0))
    monkeypatch.setattr(
        publish, "_run_with_retry",
        lambda command, _description: commands.append(command),
    )
    monkeypatch.setattr(publish, "_existing_revisions", lambda _sid: [1])

    publish.publish(dist, source_id="V", title_suffix="V", dry_run=False, revision=2)

    create = next(c for c in commands if "create" in c)
    assert "datarev-V-r2" in create
    assert "Game Data V rev 2" in create
    edit = next(c for c in commands if "edit" in c)
    assert "--draft=false" in edit
    assert "--latest=false" in edit and "--latest=true" not in edit


def test_publish_revision_enforces_monotonic_revision_numbers(tmp_path, monkeypatch):
    dist, _ = _make_dist(tmp_path, revision=2)
    monkeypatch.setattr(publish, "_existing_revisions", lambda _sid: [2, 1])
    with pytest.raises(ValueError, match="must exceed"):
        publish.publish(dist, source_id="V", title_suffix="V", dry_run=False, revision=2)


def test_publish_revision_must_match_manifest(tmp_path, monkeypatch):
    dist, _ = _make_dist(tmp_path, revision=1)
    monkeypatch.setattr(publish, "_existing_revisions", lambda _sid: [])
    with pytest.raises(ValueError, match="does not match"):
        publish.publish(dist, source_id="V", title_suffix="V", dry_run=False, revision=2)


def test_publish_revision_dry_run_never_touches_remote(tmp_path, monkeypatch, capsys):
    dist, _ = _make_dist(tmp_path, revision=3)
    monkeypatch.setattr(publish, "_existing_revisions", lambda _sid: [2])
    publish.publish(dist, source_id="V", title_suffix="V", dry_run=True, revision=3)
    out = capsys.readouterr().out
    assert "datarev-V-r3" in out and "latest: False" in out


# --- tag namespaces and baseline lineage selection ---


def test_parse_release_tag_namespaces():
    from akdp.check import parse_release_tag

    assert parse_release_tag("data-26-09-03-04-06-00_ed95a2") == ("26-09-03-04-06-00_ed95a2", 1)
    assert parse_release_tag("datarev-26-09-03-04-06-00_ed95a2-r2") == ("26-09-03-04-06-00_ed95a2", 2)
    assert parse_release_tag("images-v1") is None
    assert parse_release_tag("datarev-x-r2-extra") is None
    assert parse_release_tag("datarev-x") is None


def test_select_baseline_release_prefers_max_version_revision_tuple():
    from akdp.baseline import select_baseline_release

    tags = [
        "data-26-08-03_x", "datarev-26-08-03_x-r3", "datarev-26-08-03_x-r2",
        "data-26-09-03_y", "images-v9",
    ]
    assert select_baseline_release(tags) == ("data-26-09-03_y", ("26-09-03_y", 1))
    assert select_baseline_release(["data-26-09-03_y", "datarev-26-09-03_y-r2"]) == (
        "datarev-26-09-03_y-r2", ("26-09-03_y", 2))
    assert select_baseline_release(["images-v1"]) is None


def test_verify_manifest_accepts_datarev_tag(tmp_path):
    from akdp.baseline import _verify_manifest
    from akdp.package import _sha256

    staging = tmp_path
    assets = {}
    for name in (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET):
        p = staging / name
        p.write_bytes(name.encode("utf-8"))
        assets[name] = {"sha256": _sha256(p), "size": p.stat().st_size}
    manifest = {
        "contractVersion": contract.CONTRACT_VERSION,
        "publicationRevision": 2,
        "source": {"versionId": "V"},
        "assets": assets,
    }
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _verify_manifest(staging, {"tagName": "datarev-V-r2"})  # accepted

    manifest["source"] = {"versionId": "W"}
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        _verify_manifest(staging, {"tagName": "datarev-V-r2"})


def test_baseline_revision_requires_manifest(tmp_path):
    from akdp.baseline import _verify_manifest
    with pytest.raises(RuntimeError, match="manifest"):
        _verify_manifest(tmp_path, {"tagName": "datarev-V-r2"})


def test_baseline_revision_must_match_manifest(tmp_path):
    from akdp.baseline import _verify_manifest
    (tmp_path / "manifest.json").write_text(json.dumps({
        "contractVersion": contract.CONTRACT_VERSION,
        "source": {"versionId": "V"}, "publicationRevision": 3,
    }))
    with pytest.raises(RuntimeError, match="revision"):
        _verify_manifest(tmp_path, {"tagName": "datarev-V-r2"})


@pytest.mark.parametrize("bad", ["source", "hash", "validation", "revision"])
def test_publish_checks_candidate_before_remote_calls(tmp_path, monkeypatch, bad):
    dist, _ = _make_dist(tmp_path, revision=2)
    manifest_path = dist / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if bad == "source":
        manifest["source"]["versionId"] = "other"
    elif bad == "hash":
        (dist / contract.STORY_ASSET).write_bytes(b"changed")
    elif bad == "validation":
        manifest["validation"]["errors"] = ["summary rejected"]
    else:
        manifest["publicationRevision"] = 2.5
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(publish, "_existing_revisions", lambda _: pytest.fail("remote called before preflight"))
    with pytest.raises(ValueError):
        publish.publish(dist, source_id="V", title_suffix="V", dry_run=False, revision=2)


def test_revision_public_race_never_clobbers_assets(tmp_path, monkeypatch):
    dist, _ = _make_dist(tmp_path, revision=2)
    monkeypatch.setattr(publish, "_existing_revisions", lambda _: [])
    monkeypatch.setattr(publish, "_release_state", lambda _: {"isDraft": False, "assets": []})
    monkeypatch.setattr(publish, "_run_with_retry", lambda *a: pytest.fail("public revision mutated"))
    with pytest.raises(ValueError, match="immutable"):
        publish.publish(dist, source_id="V", title_suffix="V", dry_run=False, revision=2)
