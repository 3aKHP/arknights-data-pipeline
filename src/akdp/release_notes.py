"""Deterministic public release descriptions; machine contracts stay in assets."""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

CONTEXT_FILE = "release-notes-context.json"
DEFAULT_REPO = "3aKHP/arknights-data-pipeline"
TIERS = ("original", "large", "preview")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def text(value) -> str:
    value = html.escape(str(value), quote=False)
    value = re.sub(r"[\r\n\t]+", " ", value)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def size(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024
    raise AssertionError("unreachable")


def release_url(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/releases/tag/{quote(tag, safe='')}"


def asset_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"


def _identity(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_context(dist: Path, document: dict, changes: dict) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / CONTEXT_FILE).write_text(json.dumps(
        {"documentSha256": _identity(document), "changes": changes},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_context(dist: Path, document: dict) -> dict:
    context = read_json(dist / CONTEXT_FILE)
    return context.get("changes", {}) if context.get("documentSha256") == _identity(document) else {}


def _items(values: dict, ids, label) -> list[dict]:
    return [{"id": key, "label": label(key, values[key])} for key in sorted(ids)]


def _excel(root: Path) -> Path:
    return root / "zh_CN/gamedata/excel"


def data_changes(candidate: Path, baseline: Path | None) -> dict:
    """Compare stable record IDs; no inferred game launch dates or balance claims."""
    if baseline is None or not _excel(baseline).is_dir():
        return {}
    now, old = _excel(candidate), _excel(baseline)
    characters = read_json(now / 'character_table.json')
    result = {}
    for name, category, select, label in (
        ("character_table.json", "干员", lambda d: {k: v for k, v in d.items() if k.startswith("char_")},
         lambda k, v: v.get("name") or k),
        ("skin_table.json", "时装", lambda d: {k: v for k, v in d.get("charSkins", {}).items() if "@" in k},
         lambda k, v: ' · '.join(n for n in (characters.get(v.get('charId'), {}).get('name'),
                                            (v.get('displaySkin') or {}).get('skinName') or k) if n)),
        ("story_review_table.json", "剧情活动", lambda d: d, lambda k, v: v.get("name") or k),
    ):
        if not (old / name).is_file() or not (now / name).is_file():
            continue
        a, b = select(read_json(now / name)), select(read_json(old / name))
        if category == "时装":
            chars = read_json(now / 'character_table.json')
            a = {k: v for k, v in a.items() if v.get('charId', '').startswith('char_') and v.get('charId') in chars}
        result[category] = _items(a, a.keys() - b.keys(), label)
    def chapters(table):
        return {c["storyId"]: c for e in table.values() for c in e.get("infoUnlockDatas", []) if c.get("storyId")}
    name = "story_review_table.json"
    if (old / name).is_file() and (now / name).is_file():
        a, b = chapters(read_json(now / name)), chapters(read_json(old / name))
        result["剧情章节"] = _items(a, a.keys() - b.keys(), lambda k, v: " ".join(
            str(v.get(f) or "") for f in ("storyCode", "storyName", "avgTag")).strip() or k)
    result["tables"] = [p.name for p in sorted(now.glob("*.json"))
                        if (old / p.name).is_file() and p.read_bytes() != (old / p.name).read_bytes()]
    return result


def image_changes(index: dict, previous: dict | None, excel: Path | None = None) -> dict:
    chars = read_json(excel / "character_table.json") if excel else {}
    skin_table = read_json(excel / "skin_table.json") if excel else {}
    skins = skin_table.get("charSkins", {})
    stages = {}
    for mapping in skin_table.get('buildinEvolveMap', {}).values():
        for stage, skin_id in mapping.items():
            stages.setdefault(skin_id, []).append(int(stage))
    def label(key, _):
        skin = skins.get(key, {})
        char = chars.get(skin.get("charId", key.split("@")[0].split("#")[0]), {})
        title = (skin.get("displaySkin") or {}).get("skinName")
        if not title and key in stages:
            title = {0: '初始立绘', 1: '精英一立绘', 2: '精英二立绘'}.get(min(stages[key]))
        return " · ".join(v for v in (char.get("name"), title or key) if v)
    artworks = index.get("artworks", {})
    result = {"labels": {k: label(k, v) for k, v in sorted(artworks.items())}}
    if previous is None:
        return result
    old = previous.get("artworks", {})
    def members(entry):
        return {tier: (entry[tier].get("file"), entry[tier].get("bytes"), entry[tier].get("sha256"))
                for tier in TIERS if tier in entry}
    added, removed = artworks.keys() - old.keys(), old.keys() - artworks.keys()
    changed = {k for k in artworks.keys() & old.keys() if members(artworks[k]) != members(old[k])}
    result.update(previousVersion=previous.get("currentVersion"),
                  added=_items(artworks, added, label), changed=_items(artworks, changed, label),
                  removed=_items(old, removed, label))
    return result


def _list(label: str, items: list[dict]) -> str:
    if not items:
        return ""
    lines = [f"- {text(item['label'])}" for item in items]
    if len(items) > 10:
        return f"<details>\n<summary>{label}：{len(items)} 项</summary>\n\n" + "\n".join(lines) + "\n\n</details>"
    return f"**{label}（{len(items)} 项）**\n\n" + "\n".join(lines)


def render_data(manifest: dict, changes: dict | None = None, *, repo=DEFAULT_REPO,
                repair_text: str | None = None) -> tuple[str, str]:
    changes = changes or {}
    version = manifest.get("source", {}).get("versionId", "manual")
    revision = manifest.get("publicationRevision", 1)
    tag = f"data-{version}" if revision == 1 else f"datarev-{version}-r{revision}"
    title = f"Game Data {version}" + (f" rev {revision}" if revision > 1 else "")
    lines = ["明日方舟国服游戏数据更新，包含数值表、关卡与剧情数据。",
             f"数据版本：{text(version)}；发布修订：{revision}。"]
    if revision > 1:
        repair = manifest.get("repair", {})
        description = repair_text
        if not description and repair.get("kind") == "add-sqlite-sidecar":
            description = "本次补充 SQLite 数据包。"
            if repair.get("legacyAssetsUnchanged") is True:
                description += "原有三个 ZIP 附件保持不变。"
        lines += ["**本次修订**", text(description or "本次为同一游戏版本的数据包修订，附件范围见下表。")]
    sections = [_list("新增收录" + category, changes.get(category, []))
                for category in ("干员", "时装", "剧情活动", "剧情章节")]
    lines.extend(section for section in sections if section)
    if changes.get("tables"):
        lines.append(_list('调整的数据表', [{'id': n, 'label': n} for n in changes['tables']]))
    summaries = manifest.get("summarize", {})
    counts = [("章节摘要", summaries.get("chapters_generated", 0)), ("活动摘要", summaries.get("events_generated", 0))]
    if any(n for _, n in counts):
        lines.append("本次生成：" + "、".join(f"{n} 条{label}" for label, n in counts if n) + "。")
    lines += ["**下载与使用**", "本包累积保留历史内容。PRTS-MCP 已启用自动同步时，无需手动下载。",
              "| 附件 | 内容 | 下载大小 |\n| --- | --- | --- |"]
    uses = {"zh_CN.sqlite.gz": "SQLite 游戏数据与查询索引", "zh_CN-excel.zip": "JSON 数值表",
            "zh_CN-levels.zip": "关卡与敌人数据", "zh_CN.zip": "数值表、剧情、索引及摘要",
            "manifest.json": "文件校验与构建信息"}
    rows = []
    for name, use in uses.items():
        asset = manifest.get("assets", {}).get(name)
        if asset or name == "manifest.json":
            length = asset['size'] if asset else changes.get('manifestBytes')
            rows.append(f"| [{name}]({asset_url(repo, tag, name)}) | {use} | {size(length) if length is not None else '—'} |")
    lines[-1] += "\n" + "\n".join(rows)
    lines.append("完整文件摘要、SHA-256 与构建来源见附件 "
                 f"[manifest.json]({asset_url(repo, tag, 'manifest.json')})。")
    return title, "\n\n".join(lines) + "\n"


def image_title(tag: str, *, index_only=False) -> str:
    prefix = "images2-" if tag.startswith("images2-") else "images-"
    baseline = tag.startswith(prefix + "baseline-")
    version = tag[len(prefix + "baseline-"):] if baseline else tag[len(prefix):]
    return f"Image {'Baseline' if baseline else 'Delta'} {version}" + ("（基线索引）" if index_only else "")


def render_image(index: dict, mode: str, assets: dict[str, int], changes: dict | None = None,
                 *, repo=DEFAULT_REPO) -> tuple[str, str]:
    changes = changes or {}
    version, baseline = index["currentVersion"], index["baselineVersion"]
    prefix = "images2-" if index.get("schemaVersion") == "akdp-images/v2" else "images-"
    baseline_tag, version_tag = prefix + "baseline-" + baseline, prefix + version
    tag = baseline_tag if mode == "baseline" else version_tag
    title = image_title(tag, index_only=mode == "index")
    count = len(index.get("artworks", {}))
    baseline_link = f"[{text(baseline)}]({release_url(repo, baseline_tag)})"
    lines = []
    if mode == "index":
        lines += [f"本版本为新基线的图片索引，共收录 {count:,} 组立绘与时装图片。",
                  f"图片文件由关联基线 {baseline_link} 提供，请前往该发布下载。"]
    elif mode == "baseline":
        base = sum(e.get("kind", "base" if e.get("shard") == "chararts" else "skin") == "base"
                   for e in index.get("artworks", {}).values())
        lines += [f"本版本的完整立绘快照，共 {count:,} 组，其中基础立绘 {base:,} 组、时装 {count-base:,} 组。",
                  "可用于首次安装或重建本地图片库。",
                  "**下载选择**",
                  "| 尺寸 | 用途 | 下载大小 |\n| --- | --- | --- |"]
        totals = {tier: sum(n for name, n in assets.items() if name.endswith('.zip') and
                           (f"-{tier}-" in name or name.endswith(f"-{tier}.zip"))) for tier in TIERS}
        # v2 descriptors provide the authoritative logical group when names vary.
        if prefix == "images2-":
            totals = {tier: sum(p['bytes'] for p in index.get('baselineParts', [])
                                if p['group'].endswith('-' + tier)) for tier in TIERS}
        lines[-1] += "\n" + "\n".join(f"| {tier} | {use} | {size(totals[tier])} |" for tier, use in (
            ("large", "长边不超过 1024 px，常规展示"), ("preview", "长边不超过 256 px，缩略图"),
            ("original", "原始分辨率，按需下载")))
        lines += [f"large＋preview 合计 {size(totals['large']+totals['preview'])}；包含原图共 {size(sum(totals.values()))}。",
                  "同一类别有多个 ZIP 分片时，需要下载齐全。后续更新由图片版本发布提供。"]
    else:
        lines += [f"明日方舟国服图片增量更新，当前完整索引共收录 {count:,} 组立绘与时装图片。"]
        if "added" in changes:
            lines.append(f"本次新增 {len(changes['added'])} 组、变更 {len(changes['changed'])} 组、移出索引 {len(changes['removed'])} 组。")
            if not changes['added'] and not changes['changed']:
                lines.append("本次仅更新图片索引，没有新增或变更的 PNG。")
            for key, label in (("added", "新增图片"), ("changed", "文件变更"), ("removed", "移出索引")):
                section = _list(label, changes[key])
                if section:
                    lines.append(section)
        zips = {k: v for k, v in assets.items() if k.endswith('.zip')}
        lines += ["**下载与使用**", f"本次增量共 {len(zips)} 个 ZIP，合计 {size(sum(zips.values()))}。",
                  f"对应基线：{baseline_link}。"]
        previous = changes.get("previousVersion") or index.get("previousVersion")
        if previous:
            lines.append(f"前一版本：[{text(previous)}]({release_url(repo, prefix + previous)})。")
        lines.append("增量包仅包含本次新增或变更的图片。首次使用需要基线；跨越多个版本时，需要补齐必要增量。PRTS-MCP 已启用本地图片同步时会自动处理。")
    index_tag = tag if mode == "baseline" and prefix == "images2-" else version_tag
    lines += [f"图片协议：{text(index.get('schemaVersion', 'akdp-images/v1'))}。",
              f"完整图片目录、文件尺寸与 SHA-256 见 [index.json]({asset_url(repo, index_tag, 'index.json')})。"]
    return title, "\n\n".join(lines) + "\n"


def write_preview(dist: Path, title: str, body: str, name="release-notes.md") -> Path:
    path = dist / name
    path.write_text(body, encoding="utf-8")
    print(f"Release notes preview: {path} ({title})")
    return path


def prepare_data(candidate: Path, baseline: Path | None, dist: Path, manifest: dict) -> None:
    changes = data_changes(candidate, baseline)
    changes['manifestBytes'] = (dist / 'manifest.json').stat().st_size
    write_context(dist, manifest, changes)
    write_preview(dist, *render_data(manifest, changes))


def prepare_images(dist: Path, index: dict, previous: dict | None, excel: Path | None = None) -> None:
    changes = image_changes(index, previous, excel)
    write_context(dist, index, changes)
    baseline = index['baselineVersion'] == index['currentVersion']
    if index.get('schemaVersion') == 'akdp-images/v2':
        parts = index['baselineParts'] if baseline else index['deltaParts']
        assets = {p['file']: p['bytes'] for p in parts}
    else:
        names = index.get('shards', {}).values() if baseline else [f"images-delta-{index['currentVersion']}.zip"]
        assets = {name: (dist / name).stat().st_size for name in names}
    write_preview(dist, *render_image(index, 'baseline' if baseline else 'delta', assets, changes))
    if baseline:
        write_preview(dist, *render_image(index, 'index', {}, changes), name='index-notes.md')
