"""Small declared-resource inputs for CLI packaging tests."""
import json


def prepare_inventory(work):
    index = json.loads((work / 'images-out/index.json').read_text())
    previous_path = work / 'images-prev/index.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    valid = set(index['artworks']) | set(previous.get('artworks', {}))
    excel = work / 'inventory-excel'
    excel.mkdir(exist_ok=True)
    (excel / 'skin_table.json').write_text(json.dumps({'charSkins': {
        skin: {'charId': skin.split('#')[0], 'illustId': 'illust_' + skin}
        for skin in valid}}))
    plan_path = work / 'images-plan.json'
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {
        'incremental': bool(previous), 'currentValidSkinIds': sorted(valid),
        'bundleHashes': {'chararts/fixture.ab': 'fixture-hash'}, 'downloadedBundles': []}
    plan.update(excelDir=str(excel), sourceFingerprint='fixture-source', currentValidSkinIds=sorted(valid), incremental=bool(previous))
    plan_path.write_text(json.dumps(plan))
    hashes = plan['bundleHashes']
    first = next(iter(hashes))
    (work / 'images-inventory.json').write_text(json.dumps({
        bundle: {'hash': value, 'declarations': [], 'battleOnlyOwners': [],
                 'art': [f'dyn/arts/characters/fixture/illust_{skin}.prefab' for skin in valid] if bundle == first else []}
        for bundle, value in hashes.items()}))
    extract = work / 'images-extract.json'
    if not extract.exists():
        extract.write_text(json.dumps({'bundle_candidates': {}}))
