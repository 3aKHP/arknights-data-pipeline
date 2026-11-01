"""Preview or refresh explicit releases' descriptions without touching assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

from . import contract
from .check import parse_release_tag
from .release_notes import (
    DEFAULT_REPO, data_changes, image_changes, read_json, render_data, render_image, write_preview,
)


def api(*args, body=None):
    command = ['gh', 'api', *args]
    if body is not None:
        command += ['--input', '-']
    proc = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def invariant(release: dict) -> dict:
    """Public identity and payload fields a description edit must preserve."""
    return {**{k: release.get(k) for k in ('id', 'tag_name', 'target_commitish', 'draft',
            'prerelease', 'created_at', 'published_at', 'immutable')},
            'assets': sorted(({k: a.get(k) for k in ('id', 'name', 'size', 'digest', 'updated_at')}
                              for a in release.get('assets', [])), key=lambda a: a['id'])}


def _unchanged(original, current):
    return invariant(original) == invariant(current) and all(
        original.get(k) == current.get(k) for k in ('name', 'body', 'updated_at'))


class Sources:
    def __init__(self, repo: str, root: Path, releases: list[dict]):
        self.repo, self.root, self.releases = repo, root, releases

    def download(self, release: dict, name: str) -> Path:
        asset = next(a for a in release['assets'] if a['name'] == name)
        directory = self.root / 'sources' / str(release['id'])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        if not path.exists():
            subprocess.run(['gh', 'release', 'download', release['tag_name'], '-R', self.repo,
                            '-p', name, '--dir', str(directory)], capture_output=True, text=True, check=True)
        digest = asset.get('digest')
        if not isinstance(digest, str) or not digest.startswith('sha256:'):
            raise ValueError(f"missing authoritative asset digest: {release['tag_name']}/{name}")
        if path.stat().st_size != asset['size'] or 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"asset digest mismatch: {path}")
        return path

    def manifest(self, release):
        value = read_json(self.download(release, 'manifest.json'))
        if (value.get('contractVersion') != contract.CONTRACT_VERSION or
                (value.get('source', {}).get('versionId'), value.get('publicationRevision', 1)) !=
                parse_release_tag(release['tag_name'])):
            raise ValueError('manifest identity mismatch')
        return value

    def tables(self, release):
        manifest = self.manifest(release)
        path = self.download(release, contract.EXCEL_ASSET)
        expected = manifest['assets'][contract.EXCEL_ASSET]
        if path.stat().st_size != expected['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('excel archive differs from manifest')
        root = path.parent / 'tables'
        excel = root / 'zh_CN/gamedata/excel'
        excel.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if re.fullmatch(r'zh_CN/gamedata/excel/[^/]+\.json', name):
                    (excel / name.rsplit('/', 1)[1]).write_bytes(z.read(name))
        return root

    def data_release(self, version):
        candidates = [r for r in self.releases if (parsed := parse_release_tag(r['tag_name'])) and parsed[0] == version]
        return max(candidates, key=lambda r: parse_release_tag(r['tag_name']), default=None)

    def image_index(self, release):
        index = read_json(self.download(release, 'index.json'))
        prefix = 'images2-' if index.get('schemaVersion') == 'akdp-images/v2' else 'images-'
        if release['tag_name'] != prefix + index.get('currentVersion', ''):
            raise ValueError('image index identity mismatch')
        return index

    def describe(self, release):
        tag = release['tag_name']
        if parsed := parse_release_tag(tag):
            manifest = self.manifest(release)
            previous = max((r for r in self.releases if (p := parse_release_tag(r['tag_name'])) and p < parsed),
                           key=lambda r: parse_release_tag(r['tag_name']), default=None)
            changes = data_changes(self.tables(release), self.tables(previous)) if previous else {}
            changes['manifestBytes'] = next(a['size'] for a in release['assets'] if a['name'] == 'manifest.json')
            return render_data(manifest, changes, repo=self.repo)
        prefix = 'images2-' if tag.startswith('images2-') else 'images-'
        baseline_mode = tag.startswith(prefix + 'baseline-')
        version = tag[len(prefix + 'baseline-'):] if baseline_mode else tag[len(prefix):]
        indexed_release = next(r for r in self.releases if r['tag_name'] == prefix + version)
        index = self.image_index(indexed_release)
        if index.get('schemaVersion') == 'akdp-images/v2' and baseline_mode:
            if read_json(self.download(release, 'index.json')) != index:
                raise ValueError('baseline index differs from companion index')
        mode = 'baseline' if baseline_mode else 'index' if index['baselineVersion'] == version else 'delta'
        previous = None
        if mode == 'delta':
            candidates = [r for r in self.releases if r['tag_name'].startswith(prefix) and
                          not r['tag_name'].startswith(prefix + 'baseline-') and r['tag_name'][len(prefix):] < version]
            prior = max(candidates, key=lambda r: r['tag_name'], default=None)
            if prior:
                previous = self.image_index(prior)
                if previous['baselineVersion'] != index['baselineVersion']:
                    previous = None
        data = self.data_release(version)
        excel = self.tables(data) / 'zh_CN/gamedata/excel' if data else None
        changes = image_changes(index, previous, excel)
        return render_image(index, mode, {a['name']: a['size'] for a in release['assets']}, changes, repo=self.repo)


def refresh(tags: list[str], root: Path, *, repo=DEFAULT_REPO, execute=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('repo must be owner/name')
    if not tags or len(tags) != len(set(tags)) or any(
            not re.fullmatch(r'(?:data-|datarev-|images-|images2-)[A-Za-z0-9_.-]+', t) for t in tags):
        raise ValueError('provide unique explicit data/image release tags')
    root.mkdir(parents=True, exist_ok=True)
    pages = api('--paginate', '--slurp', f'repos/{repo}/releases?per_page=100')
    releases = [r for page in pages for r in page if not r['draft'] and not r['prerelease']]
    sources = Sources(repo, root, releases)
    latest = api(f'repos/{repo}/releases/latest')['id']
    prepared = []
    for tag in tags:
        release = next(r for r in releases if r['tag_name'] == tag)
        backup = root / (tag + '.original.json')
        if backup.exists():
            if not _unchanged(read_json(backup), release):
                raise ValueError(f'release changed since preview: {tag}; use a new workdir')
        else:
            backup.write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        title, body = sources.describe(release)
        path = write_preview(root, title, body, tag + '.md')
        prepared.append((release, title, body, path))
    if not execute:
        return [str(path) for _, _, _, path in prepared]
    # Preflight every target before making the first edit.
    for release, _, _, _ in prepared:
        current = api(f"repos/{repo}/releases/{release['id']}")
        if not _unchanged(release, current):
            raise ValueError(f"concurrent release change: {release['tag_name']}")
    if api(f'repos/{repo}/releases/latest')['id'] != latest:
        raise ValueError('Latest changed during preview')
    completed = []
    for release, title, body, path in prepared:
        current = api(f"repos/{repo}/releases/{release['id']}")
        if not _unchanged(release, current):
            raise ValueError(f"concurrent release change: {release['tag_name']}")
        api('--method', 'PATCH', f"repos/{repo}/releases/{release['id']}", body={'name': title, 'body': body})
        after = api(f"repos/{repo}/releases/{release['id']}")
        if invariant(after) != invariant(release) or after['name'] != title or after['body'] != body:
            raise RuntimeError('release edit verification failed')
        completed.append(release['tag_name'])
        (root / 'completed.json').write_text(json.dumps(completed, indent=2) + '\n')
    if api(f'repos/{repo}/releases/latest')['id'] != latest:
        raise RuntimeError('Latest changed during description edits')
    return completed
