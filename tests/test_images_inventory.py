import argparse
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from akdp import cli, images_fetch, images_inventory as inventory
from akdp.images_state import BuildState, load_build_state, write_build_state


def record(art=(), battle=(), hash="hash"):
    return dict(hash=hash, declarations=list(art) + [f'dyn/battle/prefabs/skins/character/{owner}/defaultskin.prefab' for owner in battle], art=list(art), battleOnlyOwners=list(battle))


def tables(path):
    skins = {f'char_999_fixture#{n}': dict(charId='char_999_fixture', illustId=f'illust_char_999_fixture_{n}')
             for n in (1, 2)}
    (path / 'skin_table.json').write_text(json.dumps({'charSkins': skins}))
    (path / 'character_table.json').write_text(json.dumps({'char_999_fixture': {'isNotObtainable': True}}))
    return set(skins)


def test_battle_only_then_added_art(tmp_path):
    valid = tables(tmp_path)
    bundles = {'chararts/char_999_fixture.ab': record(battle=['char_999_fixture'])}
    coverage = inventory.classify(tmp_path, valid, bundles)
    assert {c['status'] for c in coverage.values()} == {'no_standalone_art'}
    inventory.validate_coverage(coverage, {}, {}, valid)
    with pytest.raises(ValueError, match='disappeared'):
        inventory.validate_coverage(coverage, {}, {next(iter(valid)): {}}, valid)
    bundles['chararts/char_999_fixture.ab'] = record(art=[
        f'dyn/arts/characters/char_999_fixture/illust_char_999_fixture_{n}.prefab' for n in (1, 2)])
    coverage = inventory.classify(tmp_path, valid, bundles)
    assert {c['status'] for c in coverage.values()} == {'available'}
    with pytest.raises(ValueError, match='coverage'):
        inventory.validate_coverage(coverage, {}, {}, valid)
    inventory.validate_coverage(coverage, dict.fromkeys(valid), {}, valid)


def test_missing_and_ambiguous_declarations_fail_closed(tmp_path):
    valid = tables(tmp_path)
    for bundles in ({}, {'a': record(), 'b': record()}, {
        'a': record(['dyn/arts/a/illust_char_999_fixture_1.prefab']),
        'b': record(['dyn/arts/b/illust_char_999_fixture_1.prefab'])}):
        with pytest.raises(ValueError):
            inventory.validate_coverage(inventory.classify(tmp_path, valid, bundles), {}, {}, valid)


def test_state_evidence_invalidated_by_hash_or_scanner(tmp_path):
    state = BuildState('version', {'a': 'hash'}, {}, frozenset(),
                       {'a': record()}, inventory.SCANNER_VERSION, 'source')
    assert inventory.inventory_usable(state)
    assert not inventory.inventory_usable(replace(state, bundle_hashes={'a': 'new'}))
    assert not inventory.inventory_usable(replace(state, scanner_version=0))
    write_build_state(tmp_path / 'state', state)
    assert inventory.inventory_usable(load_build_state(tmp_path / 'state'))
    raw = state.to_dict()
    raw['schemaVersion'] = 'akdp-images-build-state/v1'
    for key in ('inventory', 'scannerVersion', 'sourceFingerprint'):
        raw.pop(key)
    (tmp_path / 'state').write_text(json.dumps(raw))
    assert not inventory.inventory_usable(load_build_state(tmp_path / 'state'))


@pytest.mark.parametrize('allow,actions,runner', [(False, 'true', 'github-hosted'),
                                                (True, '', ''), (True, 'true', 'self-hosted')])
def test_rebuild_guard_precedes_network(tmp_path, monkeypatch, allow, actions, runner):
    monkeypatch.setenv('GITHUB_ACTIONS', actions)
    monkeypatch.setenv('RUNNER_ENVIRONMENT', runner)
    monkeypatch.setattr(images_fetch, 'fetch_image_bundles', lambda *a, **k: pytest.fail('network called'))
    assert cli.cmd_images_fetch(argparse.Namespace(workdir=tmp_path, allow_inventory_rebuild=allow)) == 1


def test_reference_errors_are_not_absence():
    with pytest.raises(ValueError, match='external'):
        list(inventory._references({'texture': {'m_PathID': 1, 'm_FileID': 1}}))


@pytest.mark.parametrize('change_reference', [False, True])
def test_incremental_planner_reuses_json_evidence_and_refreshes_changed_reference(tmp_path, monkeypatch, change_reference):
    valid = tables(tmp_path)
    bundle = 'chararts/char_999_fixture.ab'
    records = {bundle: record(art=[
        f'dyn/arts/characters/char_999_fixture/illust_char_999_fixture_{n}.prefab' for n in (1, 2)])}
    previous = tmp_path / 'images-prev'
    previous.mkdir()
    (previous / 'index.json').write_text(json.dumps({'currentVersion': 'version'}))
    state = BuildState('version', {bundle: 'hash'}, {}, frozenset(valid), records,
                       inventory.SCANNER_VERSION, 'source', inventory.classify(tmp_path, valid, records))
    write_build_state(previous / 'images-build-state.json', state)
    if change_reference:
        path = tmp_path / 'skin_table.json'
        data = json.loads(path.read_text())
        data['charSkins']['char_999_fixture#1']['illustId'] = 'illust_char_999_fixture_2'
        path.write_text(json.dumps(data))
    def fetch(cache, **kwargs):
        assert kwargs['required_bundles'] == ({bundle} if change_reference else set())
        assert kwargs['prev_hashes'] == {bundle: 'hash'}
        cache.mkdir()
        return images_fetch.FetchStats(), {'hashes': {bundle: 'hash'}, 'versionId': 'version', 'contentFingerprint': 'source'}
    monkeypatch.setattr(images_fetch, 'fetch_image_bundles', fetch)
    assert cli.cmd_images_fetch(argparse.Namespace(workdir=tmp_path, excel_dir=tmp_path, server='cn')) == 0


def test_structural_scanner_with_generic_unit(monkeypatch):
    def obj(ident, kind, tree, **data):
        return SimpleNamespace(path_id=ident, type=SimpleNamespace(name=kind),
                               read_typetree=lambda: tree, read=lambda: SimpleNamespace(**data))
    owner = 'char_999_fixture'
    root = obj(1, 'GameObject', {'refs': [{'m_FileID': 0, 'm_PathID': n} for n in (2, 3)]})
    atlas = obj(2, 'TextAsset', {}, m_Name=owner+'.atlas', m_Script=owner+'.png\nsize: 64,64')
    skeleton = obj(3, 'TextAsset', {}, m_Name=owner+'.skel')
    texture = obj(4, 'Texture2D', {}, m_Name=owner)
    objects = [root, atlas, skeleton, texture]
    monkeypatch.setattr(inventory.UnityPy, 'load', lambda data: SimpleNamespace(
        container={f'dyn/battle/prefabs/skins/character/{owner}/defaultskin.prefab': root}, objects=objects))
    assert inventory.scan_bundle(b'fixture', 'hash')['battleOnlyOwners'] == [owner]
    objects.append(obj(5, 'Texture2D', {}, m_Name='unexplained'))
    assert inventory.scan_bundle(b'fixture', 'hash')['battleOnlyOwners'] == []
