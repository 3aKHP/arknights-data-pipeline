import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path
from akdp.images_parts import package, verify

class ShardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.images = self.root / 'images'; self.images.mkdir()
        self.index = {'currentVersion': '26-01-01', 'artworks': {}}

    def add(self, n, seed=0):
        data = random.Random(seed + n).randbytes(512)
        name = f'{n:04d}.large.png'
        (self.images / name).write_bytes(data)
        self.index['artworks'][str(n)] = {'shard': 'chararts', 'large': {'file': name,
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'w': 1, 'h': 1}}

    def build(self, name, previous=None, target=1500):
        return package(self.images, self.index, self.root / name, previous, target)

    def test_growth_determinism_and_resume(self):
        for n in range(107): self.add(n)
        a = self.build('a', target=700)
        self.assertEqual(len(a['baselineParts']), 107)
        self.assertEqual(a, self.build('b', target=700))
        self.assertEqual(a, self.build('a', target=700))
        self.assertEqual(verify(self.root/'a')['members'], 107)

    def test_delta_changes_deletion_empty_and_predecessor(self):
        for n in range(6): self.add(n)
        p = self.build('p')
        self.index['currentVersion'] = '26-01-02'
        self.add(0, 100); self.add(7); del self.index['artworks']['1']
        l = self.build('l', p, 700)
        self.assertEqual(verify(self.root/'l', p)['members'], 2)
        self.assertEqual(l['baselineParts'], p['baselineParts'])
        self.index['currentVersion'] = '26-01-03'
        empty = self.build('empty', l)
        self.assertEqual(empty['deltaParts'], [])
        with self.assertRaises(ValueError): verify(self.root/'l', l)

    def test_bad_input_and_corrupted_part(self):
        self.add(1)
        p = self.build('p')
        asset = self.root/'p'/p['baselineParts'][0]['file']
        asset.write_bytes(b'bad')
        with self.assertRaises(ValueError): verify(self.root/'p')
        with self.assertRaises(ValueError): self.build('p')
        (self.images/'0001.large.png').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.build('new')

    def test_identity_and_path_rejection(self):
        self.add(1); self.build('p')
        self.index['currentVersion'] = '26-01-02'
        with self.assertRaises(ValueError): self.build('p')
        self.index['artworks']['1']['large']['file'] = '../escape.png'
        with self.assertRaises(ValueError): self.build('unsafe')

    def test_actual_zip_overhead_triggers_split(self):
        for n in range(3): self.add(n)
        p = self.build('p', target=1100)
        self.assertEqual(len(p['baselineParts']), 3)
        self.assertTrue(all(x['bytes'] <= 1100 for x in p['baselineParts']))

if __name__ == '__main__': unittest.main()


def test_cli_v2_packaging_and_quarterly_rotation(tmp_path):
    import argparse
    from akdp import cli
    images = tmp_path / 'images-out'
    images.mkdir()
    cache = tmp_path / 'images-cache'
    cache.mkdir()
    data = b'png-fixture'
    filename = 'a.large.png'
    (images / filename).write_bytes(data)
    raw = {'currentVersion': '26-10-01', 'artworks': {'a': {'shard': 'chararts', 'large': {
        'file': filename, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}}
    (images / 'index.json').write_text(json.dumps(raw))
    (cache / 'hashes.json').write_text(json.dumps({'versionId': '26-10-01'}))
    args = argparse.Namespace(workdir=tmp_path, image_format='v2', rebaseline=False, part_target_bytes=700)
    assert cli.cmd_images_package(args) == 0
    baseline = json.loads((tmp_path / 'images-dist/26-10-01/index.json').read_text())
    assert baseline['schemaVersion'] == 'akdp-images/v2'
    assert not cli._image_baseline_needed(args, baseline, '26-12-31')
    assert cli._image_baseline_needed(args, baseline, '27-01-01')
    args.rebaseline = True
    assert cli._image_baseline_needed(args, baseline, '26-10-02')
    args.execute = True
    with __import__('pytest').raises(ValueError, match='refusing the v1 path'):
        cli.cmd_images_publish(args)


def test_asset_limit_reserves_build_state_and_signature(tmp_path, monkeypatch):
    from akdp import images_parts
    # Model a smaller release ceiling without producing thousands of fixtures.
    source = {'currentVersion': '26-10-01', 'artworks': {}}
    for n in range(998):
        filename = f'{n}.png'
        (tmp_path / filename).write_bytes(b'x')
        source['artworks'][str(n)] = {'shard': str(n), 'large': {
            'file': filename, 'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}}
    with __import__('pytest').raises(ValueError, match='asset limit'):
        package(tmp_path, source, tmp_path / 'dist', target=700)


def test_cli_incremental_pack_needs_only_changed_pngs(tmp_path):
    import argparse
    from akdp import cli
    images = tmp_path / 'images-out'
    images.mkdir()
    (tmp_path / 'images-cache').mkdir()
    def entry(name, data):
        (images / name).write_bytes(data)
        return {'shard': 'chararts', 'large': {'file': name, 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}}
    raw = {'currentVersion': '26-10-01', 'artworks': {'a': entry('a.png', b'a')}}
    def save(version):
        raw['currentVersion'] = version
        (images / 'index.json').write_text(json.dumps(raw))
        (tmp_path / 'images-cache/hashes.json').write_text(json.dumps({'versionId': version}))
    args = argparse.Namespace(workdir=tmp_path, image_format='v2', rebaseline=False, part_target_bytes=700)
    save('26-10-01')
    assert cli.cmd_images_package(args) == 0
    baseline = json.loads((tmp_path / 'images-dist/26-10-01/index.json').read_text())
    (tmp_path / 'images-prev').mkdir()
    (tmp_path / 'images-prev/index.json').write_text(json.dumps(baseline))
    (images / 'a.png').unlink()
    raw['artworks']['b'] = entry('b.png', b'b')
    save('26-10-02')
    assert cli.cmd_images_package(args) == 0
    dist = tmp_path / 'images-dist/26-10-02'
    current = json.loads((dist / 'index.json').read_text())
    assert current['baselineParts'] == baseline['baselineParts']
    assert verify(dist, baseline)['members'] == 1
