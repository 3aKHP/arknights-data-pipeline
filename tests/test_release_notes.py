import copy
import json
from pathlib import Path

import pytest

from akdp import release_notes as notes
from akdp import notes_refresh


def table(root, name, data):
    p = root / 'zh_CN/gamedata/excel' / (name + '.json')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding='utf-8')


def test_data_diff_names_filters_dedup_and_html(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    for root in (old, new):
        table(root, 'character_table', {'char_a': {'name': '甲'}})
        table(root, 'skin_table', {'charSkins': {}})
        table(root, 'story_review_table', {})
    table(new, 'character_table', {'char_a': {'name': '甲'}, 'char_b': {'name': '<script>|乙'}, 'token_b': {'name': '召唤物'}})
    table(new, 'skin_table', {'charSkins': {
        'char_b@x#1': {'charId': 'char_b', 'displaySkin': {'skinName': '时装'}},
        'char_b#1': {'charId': 'char_b'}, 'ghost@x': {'charId': 'ghost'}}})
    chapter = {'storyId': 'ch1', 'storyCode': 'ST-1', 'storyName': '开端'}
    table(new, 'story_review_table', {'act': {'name': '活动', 'infoUnlockDatas': [chapter, chapter]}})
    changes = notes.data_changes(new, old)
    assert [v['id'] for v in changes['干员']] == ['char_b']
    assert len(changes['时装']) == len(changes['剧情章节']) == 1
    manifest = {'source': {'versionId': '26-10-01'}, 'summarize': {'chapters_generated': 2}, 'assets': {}}
    title, body = notes.render_data(manifest, changes)
    assert title == 'Game Data 26-10-01'
    assert '&lt;script&gt;' in body and '<script>' not in body and '\\|' in body
    assert '2 条章节摘要' in body and '召唤物' not in body
    assert notes.data_changes(new, None) == {}


def entry(hash='h'):
    return {'kind': 'base', 'shard': 'chararts', **{tier: {'sha256': hash, 'bytes': 1, 'file': 'a.' + tier + '.png'} for tier in notes.TIERS}}


def test_image_delta_counts_variants_deletion_and_empty():
    old = {'currentVersion': '26-10-01', 'baselineVersion': '26-10-01', 'artworks': {'a': entry(), 'removed': entry()}}
    new = copy.deepcopy(old)
    new.update(currentVersion='26-10-02')
    new['artworks'] = {'a': entry(), 'new': entry()}
    new['artworks']['a']['preview']['sha256'] = 'changed'
    changes = notes.image_changes(new, old)
    _, body = notes.render_image(new, 'delta', {'delta.zip': 100}, changes)
    assert '新增 1 组、变更 1 组、移出索引 1 组' in body
    assert '前一版本' in body and '26-10-01' in body and '100 B' in body
    new['artworks'] = {'a': entry()}
    _, body = notes.render_image(new, 'delta', {}, notes.image_changes(new, old))
    assert '仅更新图片索引' in body and '移出索引 1 组' in body
    _, unknown = notes.render_image(new, 'delta', {})
    assert '新增 0' not in unknown and '仅更新图片索引' not in unknown


@pytest.mark.parametrize('v2', [False, True])
def test_baseline_sizes_and_correct_index_link(v2):
    index = {'schemaVersion': 'akdp-images/v2' if v2 else 'akdp-images/v1',
             'currentVersion': '26-10-01', 'baselineVersion': '26-10-01', 'artworks': {'a': entry()}}
    assets = {f'images-baseline-chararts-{tier}-26-10-01.zip': 1024 for tier in notes.TIERS}
    index['baselineParts'] = [{'file': 'p', 'group': 'chararts-' + tier, 'bytes': 1024} for tier in notes.TIERS]
    title, body = notes.render_image(index, 'baseline', assets)
    assert '1 组' in body and '2.0 KiB' in body and '3.0 KiB' in body
    assert ('images2-baseline-26-10-01/index.json' if v2 else 'images-26-10-01/index.json') in body
    title, body = notes.render_image(index, 'index', {})
    assert '基线索引' in title and 'sentinel' not in body and '图片文件由关联基线' in body


def test_context_is_bound_to_document_and_large_names_fold(tmp_path):
    document = {'source': {'versionId': 'v1'}}
    changes = {'干员': [{'id': str(i), 'label': '甲'} for i in range(11)]}
    notes.write_context(tmp_path, document, changes)
    assert notes.load_context(tmp_path, document) == changes
    _, body = notes.render_data(document, changes)
    assert '<details>' in body and '11 项' in body
    assert notes.render_data(document, changes)[1] == body
    assert notes.load_context(tmp_path, {'source': {'versionId': 'v2'}}) == {}


def test_repair_descriptions_and_no_internal_dump():
    manifest = {'source': {'versionId': 'v'}, 'publicationRevision': 2,
                'pipelineVersion': '0.1.0.dev0', 'repair': {'kind': 'add-sqlite-sidecar', 'legacyAssetsUnchanged': True}}
    title, body = notes.render_data(manifest)
    assert title.endswith('rev 2') and '三个 ZIP 附件保持不变' in body
    assert 'dev0' not in body and '```json' not in body and 'sha256' not in body
    assert '自定义修订' in notes.render_data(manifest, repair_text='自定义修订')[1]


def remote_release():
    return {'id': 1, 'tag_name': 'data-26-10-01', 'name': 'old', 'body': 'old', 'updated_at': 'before',
            'draft': False, 'prerelease': False, 'assets': [{'id': 3, 'name': 'manifest.json', 'size': 1, 'digest': 'sha256:h'}]}


def test_refresh_preview_and_execute_only_metadata(tmp_path, monkeypatch):
    release = remote_release()
    calls = []
    def api(*args, body=None):
        calls.append((args, body))
        if '--method' in args:
            assert set(body) == {'name', 'body'}
            release.update(body)
        if '--paginate' in args:
            return [[copy.deepcopy(release)]]
        return copy.deepcopy(release)
    monkeypatch.setattr(notes_refresh, 'api', api)
    monkeypatch.setattr(notes_refresh.Sources, 'describe', lambda *a: ('new', '新说明\n'))
    original = notes_refresh.invariant(release)
    tags = [release['tag_name']]
    notes_refresh.refresh(tags, tmp_path)
    assert all(body is None for _, body in calls)
    notes_refresh.refresh(tags, tmp_path, execute=True)
    assert release['body'] == '新说明\n' and notes_refresh.invariant(release) == original
    assert (tmp_path / (tags[0] + '.original.json')).exists()


def test_refresh_refuses_concurrent_edit_before_any_write(tmp_path, monkeypatch):
    release = remote_release()
    def api(*args, body=None):
        assert body is None
        if '--paginate' in args:
            return [[copy.deepcopy(release)]]
        value = copy.deepcopy(release)
        value['body'] = 'someone edited'
        return value
    monkeypatch.setattr(notes_refresh, 'api', api)
    monkeypatch.setattr(notes_refresh.Sources, 'describe', lambda *a: ('new', 'new'))
    with pytest.raises(ValueError, match='concurrent'):
        notes_refresh.refresh([release['tag_name']], tmp_path, execute=True)


def test_v1_dry_run_generates_preview_without_remote(tmp_path, monkeypatch):
    from akdp import images_publish
    index = {'currentVersion': '26-10-02', 'baselineVersion': '26-10-01', 'artworks': {}}
    (tmp_path / 'index.json').write_text(json.dumps(index))
    (tmp_path / 'images-delta-26-10-02.zip').write_bytes(b'zip')
    monkeypatch.setattr(images_publish, '_release_exists', lambda *a: pytest.fail('network in dry run'))
    images_publish.publish_images(tmp_path, version_id='26-10-02', mode='delta')
    assert '下载与使用' in (tmp_path / 'delta-notes.md').read_text()


def test_image_labels_use_evolution_map_and_skin_owner(tmp_path):
    table(tmp_path, 'character_table', {'char_a': {'name': '甲'}})
    table(tmp_path, 'skin_table', {'charSkins': {'char_a#2': {'charId': 'char_a'},
          'char_a@x#1': {'charId': 'char_a', 'displaySkin': {'skinName': '时装'}}},
          'buildinEvolveMap': {'char_a': {'2': 'char_a#2'}}})
    changes = notes.image_changes({'artworks': {'char_a#2': entry(), 'char_a@x#1': entry()}},
                                  None, tmp_path / 'zh_CN/gamedata/excel')
    assert changes['labels']['char_a#2'] == '甲 · 精英二立绘'
    assert changes['labels']['char_a@x#1'] == '甲 · 时装'


def test_v2_draft_discovery_accepts_legacy_and_new_titles_and_rejects_ambiguity():
    from akdp.images_publish_v2 import _find
    tag = 'images2-26-10-01'
    draft = {'id': 1, 'tag_name': 'untagged-1', 'name': tag, 'draft': True}
    assert _find([draft], tag) == draft
    newer = {**draft, 'id': 2, 'name': notes.image_title(tag, index_only=True)}
    assert _find([newer], tag) == newer
    with pytest.raises(ValueError, match='ambiguous'):
        _find([draft, newer], tag)


def test_historical_asset_digest_mismatch_stops_before_table_read(tmp_path):
    release = remote_release()
    path = tmp_path / 'sources/1'
    path.mkdir(parents=True)
    (path / 'manifest.json').write_text('x')
    sources = notes_refresh.Sources('owner/repo', tmp_path, [release])
    with pytest.raises(ValueError, match='digest mismatch'):
        sources.download(release, 'manifest.json')
