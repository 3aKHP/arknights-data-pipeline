import argparse
import json
import pytest
from akdp import provenance, cli, check, contract


def listing(*names):
    return {"abInfos": [{"name": n, "hash": "hash", "md5": "md5"} for n in names]}


def test_order_independent_and_content_sensitive():
    original = provenance.source_identity(listing("a", "b"))
    assert original == provenance.source_identity(listing("b", "a"))
    for data in (listing("a"), listing("a", "c"), listing("a", "b", "c")):
        assert original != provenance.source_identity(data)
    data = listing("a", "b")
    data["abInfos"][0]["md5"] = "changed"
    assert original != provenance.source_identity(data)


@pytest.mark.parametrize("data", [{}, listing("a", "a"), {"abInfos": [{"name": "a"}]}])
def test_invalid_identity_fails(data):
    with pytest.raises(ValueError):
        provenance.source_identity(data)


def test_highest_complete_revision(monkeypatch):
    names = (contract.EXCEL_ASSET, contract.LEVELS_ASSET, contract.STORY_ASSET, contract.MANIFEST_ASSET)
    def release(tag, draft=False, complete=True):
        return {"tag_name": tag, "draft": draft, "assets": [
            {"name": n, "id": 4} for n in (names if complete else names[:1])]}
    calls = []
    def gh(*args):
        calls.append(args)
        if "--paginate" in args:
            return [[release("data-V")], [release("datarev-V-r2"),
                release("datarev-V-r3", draft=True), release("datarev-V-r4", complete=False)]]
        return {"source": {"versionId": "V"}}
    monkeypatch.setattr(provenance, "_gh_json", gh)
    assert provenance.published_source("V", "o/r") == ("datarev-V-r2", {"versionId": "V"})
    assert "repos/o/r/releases/assets/4" in calls[-1]


@pytest.mark.parametrize("force", [False, True])
def test_drift_blocks_all_steps_even_force(tmp_path, monkeypatch, force):
    remote = {"versionId": "V", **provenance.source_identity(listing("a"))}
    monkeypatch.setattr(check, "fetch_remote_version", lambda _: remote)
    monkeypatch.setattr(provenance, "published_source", lambda *a:
                        ("data-V", {"fingerprintAlgorithm": provenance.ALGORITHM, "contentFingerprint": "old"}))
    def forbidden(*a):
        pytest.fail("drift reached pipeline or publisher")
    args = argparse.Namespace(force=force, server="cn", workdir=tmp_path, publish=True)
    assert cli._run_pipeline(args, (forbidden,), forbidden, forbidden, "test") == 1
    body = (tmp_path / "pipeline-events.jsonl").read_bytes()
    assert json.loads(body)["verdict"] == "content_drift"
    cli._run_pipeline(args, (forbidden,), forbidden, forbidden, "test")
    assert body == (tmp_path / "pipeline-events.jsonl").read_bytes()


def test_statuses(monkeypatch):
    remote = {"versionId": "V", **provenance.source_identity(listing("a"))}
    for published, expected in [(None, "new_version"), (("data-V", {}), "baseline_unknown"),
                                (("data-V", remote), "unchanged")]:
        monkeypatch.setattr(provenance, "published_source", lambda *a: published)
        assert provenance.inspect_remote(remote, "o/r") == (expected, None)


def test_remote_errors_propagate(monkeypatch):
    def failed(*a):
        raise RuntimeError("network unavailable")
    monkeypatch.setattr(provenance, "published_source", failed)
    with pytest.raises(RuntimeError):
        provenance.inspect_remote({"versionId": "V"}, "o/r")
