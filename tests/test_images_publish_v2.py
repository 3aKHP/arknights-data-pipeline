import copy
import hashlib
import json
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from akdp.images_parts import package
from akdp.images_publish_v2 import publish


class FakeGitHub:
    def __init__(self):
        self.items = []
        self.payloads = {}
        self.uploads = []
        self.activations = []
        self.fail_after_baseline = False
        self.ambiguous_upload = False
        self.interrupt_upload = False

    def releases(self):
        return copy.deepcopy(self.items)

    def assets(self, release):
        return copy.deepcopy(self.payloads[release['id']])

    def create(self, tag):
        ident = len(self.items) + 1
        release = dict(id=ident, tag_name=f'untagged-{ident}', name=tag, draft=True, target_commitish='main')
        self.items.append(release)
        self.payloads[ident] = {}
        return copy.deepcopy(release)

    def upload(self, release, path):
        if self.interrupt_upload:
            self.interrupt_upload = False
            raise RuntimeError('interrupted upload')
        data = path.read_bytes()
        self.payloads[release['id']][path.name] = dict(
            id=len(self.uploads)+1, name=path.name, state='uploaded', size=len(data),
            digest='sha256:'+hashlib.sha256(data).hexdigest(), data=data)
        self.uploads.append((release['id'], path.name))
        if self.ambiguous_upload:
            self.ambiguous_upload = False
            raise subprocess.CalledProcessError(1, ['gh'])

    def delete(self, asset):
        for assets in self.payloads.values():
            for name, value in list(assets.items()):
                if value['id'] == asset['id']:
                    del assets[name]
                    return
        raise AssertionError('missing starter')

    def activate(self, release, tag):
        actual = next(item for item in self.items if item['id'] == release['id'])
        actual.update(tag_name=tag, draft=False)
        self.activations.append(tag)
        if self.fail_after_baseline and tag.startswith('images2-baseline-'):
            self.fail_after_baseline = False
            raise RuntimeError('interrupted after baseline')
        return copy.deepcopy(actual)

    def index(self, release):
        return json.loads(self.payloads[release['id']]['index.json']['data'])


def candidate(root, version='26-10-01', previous=None, data=b'png'):
    images = root / ('input-' + version)
    images.mkdir()
    (images / 'a.png').write_bytes(data)
    index = dict(currentVersion=version, artworks={'a': dict(shard='chararts', large=dict(
        file='a.png', bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))})
    dist = root / version
    packaged = package(images, index, dist, previous, target=700)
    return dist, packaged


def test_dry_run_uses_no_remote(tmp_path):
    dist, _ = candidate(tmp_path)
    assert [job['tag'] for job in publish(dist, 'owner/repo')] == [
        'images2-baseline-26-10-01', 'images2-26-10-01']


@pytest.mark.parametrize('fault', ['interrupt_upload', 'fail_after_baseline'])
def test_resume_after_interruption(tmp_path, fault):
    dist, _ = candidate(tmp_path)
    gh = FakeGitHub()
    setattr(gh, fault, True)
    with pytest.raises(RuntimeError):
        publish(dist, 'owner/repo', dry_run=False, github=gh)
    receipt = publish(dist, 'owner/repo', dry_run=False, github=gh)
    assert all(record['state'] == 'public' for record in receipt)
    uploads = list(gh.uploads)
    publish(dist, 'owner/repo', dry_run=False, github=gh)
    assert uploads == gh.uploads
    assert len(gh.items) == 2


def test_starter_replaced_and_ambiguous_success_reconciled(tmp_path):
    dist, index = candidate(tmp_path)
    gh = FakeGitHub()
    draft = gh.create('images2-baseline-26-10-01')
    filename = index['baselineParts'][0]['file']
    gh.payloads[draft['id']][filename] = dict(id=999, name=filename, state='starter', size=0)
    gh.ambiguous_upload = True
    publish(dist, 'owner/repo', dry_run=False, github=gh)
    assert len(gh.uploads) == 3


def test_public_release_is_never_repaired_in_place(tmp_path):
    dist, _ = candidate(tmp_path)
    gh = FakeGitHub()
    publish(dist, 'owner/repo', dry_run=False, github=gh)
    first = gh.items[0]
    gh.payloads[first['id']]['index.json']['digest'] = 'sha256:wrong'
    uploads = list(gh.uploads)
    with pytest.raises(ValueError, match='remote content differs'):
        publish(dist, 'owner/repo', dry_run=False, github=gh)
    assert uploads == gh.uploads


def test_delta_requires_public_matching_dependencies(tmp_path):
    dist, baseline = candidate(tmp_path)
    next_dist, _ = candidate(tmp_path, '26-10-02', baseline, b'changed')
    gh = FakeGitHub()
    with pytest.raises(ValueError, match='predecessor'):
        publish(next_dist, 'owner/repo', baseline, dry_run=False, github=gh)
    publish(dist, 'owner/repo', dry_run=False, github=gh)
    publish(next_dist, 'owner/repo', baseline, dry_run=False, github=gh)
    assert gh.activations == ['images2-baseline-26-10-01', 'images2-26-10-01', 'images2-26-10-02']
    gh.payloads[1]['index.json']['data'] = b'{}'
    with pytest.raises(ValueError, match='lineage'):
        publish(next_dist, 'owner/repo', baseline, dry_run=False, github=gh)


def test_concurrent_same_candidate_is_serialized(tmp_path):
    dist, _ = candidate(tmp_path)
    gh = FakeGitHub()
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(publish, dist, 'owner/repo', dry_run=False, github=gh) for _ in range(2)]
        for job in jobs:
            assert len(job.result(timeout=5)) == 2
    assert len(gh.items) == 2
    assert len(gh.uploads) == 3


def test_cli_advances_previous_only_after_success(tmp_path, monkeypatch):
    import argparse
    from akdp import cli, images_publish_v2
    images = tmp_path / 'images-out'
    images.mkdir()
    (tmp_path / 'images-cache').mkdir()
    (images / 'index.json').write_text(json.dumps({'currentVersion':'26-10-01', 'artworks':{}}))
    (tmp_path / 'images-cache/hashes.json').write_text(json.dumps({'versionId':'26-10-01'}))
    args = argparse.Namespace(workdir=tmp_path, image_format='v2', rebaseline=False,
                              part_target_bytes=700, execute=True, image_repo='owner/repo')
    cli.cmd_images_package(args)
    def fail(*a, **k):
        raise RuntimeError('network interrupted')
    monkeypatch.setattr(images_publish_v2, 'publish', fail)
    with pytest.raises(RuntimeError):
        cli.cmd_images_publish(args)
    assert not (tmp_path / 'images-prev/index.json').exists()
    monkeypatch.setattr(images_publish_v2, 'publish', lambda *a, **k: [])
    cli.cmd_images_publish(args)
    assert json.loads((tmp_path / 'images-prev/index.json').read_text())['currentVersion'] == '26-10-01'
