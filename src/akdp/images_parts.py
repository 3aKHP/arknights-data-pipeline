"""Deterministic v2 baseline/delta ZIP parts with verified member coverage."""
import copy
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from .images_index import _sha256 as digest

SCHEMA = 'akdp-images/v2'
HARD_LIMIT = 2 * 1024**3
TIERS = ('original', 'large', 'preview')


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    os.replace(tmp, path)


def flat(name):
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..') or '\x00' in name or ':' in name:
        raise ValueError('unsafe member name')
    return name


def entries(index):
    result = {}
    for artwork in index['artworks'].values():
        for tier in TIERS:
            if tier not in artwork:
                continue
            item = artwork[tier]
            name = flat(item['file'])
            if name in result:
                raise ValueError('duplicate member')
            result[name] = {'bytes': item['bytes'], 'sha256': item['sha256'],
                            'group': artwork['shard'] + '-' + tier}
    return result


def zip_part(images, names, output):
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in names:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with (images / name).open('rb') as source, z.open(info, 'w', force_zip64=True) as dest:
                shutil.copyfileobj(source, dest, 1024 * 1024)


def package(images, source_index, output, previous=None, target=1024**3):
    if not 0 < target < HARD_LIMIT:
        raise ValueError('invalid target size')
    index = copy.deepcopy(source_index)
    current = index['currentVersion']
    flat(current)
    all_members = entries(index)
    prior_members = entries(previous) if previous else {}
    if previous and (previous['schemaVersion'] != SCHEMA or previous['currentVersion'] >= current):
        raise ValueError('invalid predecessor')
    selected = {n: v for n, v in all_members.items() if not previous or prior_members.get(n) != v}
    for name, value in selected.items():
        path = images / name
        if not path.is_file() or path.stat().st_size != value['bytes'] or digest(path) != value['sha256']:
            raise ValueError('input bytes differ: ' + name)
    identity = hashlib.sha256(json.dumps({'index': index, 'previous': previous, 'target': target}, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    identity_path = output / 'candidate.json'
    if identity_path.exists():
        if json.loads(identity_path.read_text()) != {'identity': identity}:
            raise ValueError('candidate identity changed')
    else:
        atomic_json(identity_path, {'identity': identity})
    if (output / 'index.json').exists():
        verify(output, previous)
        return json.loads((output / 'index.json').read_text())
    parts = []
    for group in sorted({v['group'] for v in selected.values()}):
        names = sorted(n for n, v in selected.items() if v['group'] == group)
        batches, batch, size = [], [], 0
        for name in names:
            if batch and size + selected[name]['bytes'] > target:
                batches.append(batch); batch, size = [], 0
            batch.append(name); size += selected[name]['bytes']
        if batch:
            batches.append(batch)
        number = 0
        while batches:
            batch = batches.pop(0)
            name = f"images2-{'delta' if previous else 'baseline'}-{current}-{group}-{number:05d}.zip"
            path = output / name
            tmp = output / (name + '.tmp')
            zip_part(images, batch, tmp)
            length = tmp.stat().st_size
            if length > target and len(batch) > 1:
                mid = len(batch) // 2
                batches[0:0] = [batch[:mid], batch[mid:]]
                tmp.unlink()
                continue
            if length >= HARD_LIMIT:
                tmp.unlink()
                raise ValueError('single member exceeds ZIP limit')
            checksum = digest(tmp)
            if path.exists():
                if path.stat().st_size != length or digest(path) != checksum:
                    tmp.unlink()
                    raise ValueError('existing candidate part differs')
                tmp.unlink()
            else:
                os.replace(tmp, path)
            parts.append({'file': name, 'bytes': length, 'sha256': checksum, 'group': group,
                          'members': {n: selected[n] for n in batch}})
            number += 1
    index['schemaVersion'] = SCHEMA
    index.pop('shards', None)
    index['baselineVersion'] = previous['baselineVersion'] if previous else current
    index['baselineParts'] = previous['baselineParts'] if previous else parts
    index['deltaParts'] = parts if previous else []
    index['previousVersion'] = previous['currentVersion'] if previous else None
    # Reserve index, durable build state and a future detached signature.
    if len(parts) + 3 > 1000:
        raise ValueError('release asset limit exceeded')
    atomic_json(output / 'index.json', index)
    verify(output, previous)
    return index


def verify(output, previous=None):
    index = json.loads((output / 'index.json').read_text())
    if index['schemaVersion'] != SCHEMA:
        raise ValueError('schema mismatch')
    is_delta = index['baselineVersion'] != index['currentVersion']
    expected = entries(index)
    if is_delta:
        if previous is None or previous['currentVersion'] != index['previousVersion'] or previous['baselineVersion'] != index['baselineVersion'] or previous['baselineParts'] != index['baselineParts']:
            raise ValueError('predecessor mismatch')
        prior = entries(previous)
        expected = {n: v for n, v in expected.items() if prior.get(n) != v}
    parts = index['deltaParts'] if is_delta else index['baselineParts']
    seen = {}
    for part in parts:
        path = output / flat(part['file'])
        if path.stat().st_size != part['bytes'] or path.stat().st_size >= HARD_LIMIT or digest(path) != part['sha256']:
            raise ValueError('part digest mismatch')
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(set(names)) != len(names) or set(names) != set(part['members']):
                raise ValueError('member set mismatch')
            for name in names:
                flat(name)
                if name in seen:
                    raise ValueError('duplicate member across parts')
                with z.open(name) as f:
                    h = hashlib.sha256(); size = 0
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(chunk); size += len(chunk)
                meta = {'bytes': size, 'sha256': h.hexdigest(), 'group': part['group']}
                if meta != part['members'][name]:
                    raise ValueError('member mismatch')
                seen[name] = meta
    if seen != expected:
        raise ValueError('coverage mismatch')
    if {p.name for p in output.glob('*.zip')} != {p['file'] for p in parts}:
        raise ValueError('unexpected ZIP')
    return {'verified': True, 'parts': len(parts), 'members': len(seen)}
