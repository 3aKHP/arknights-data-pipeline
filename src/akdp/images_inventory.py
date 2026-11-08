"""Resource declarations and positive evidence for battle-only characters."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from .images_extract import UnityPy

SCANNER_VERSION = 1


def _references(value, field=""):
    if isinstance(value, dict):
        if value.get("m_PathID"):
            if value.get("m_FileID") == 0:
                yield value["m_PathID"]
            elif field not in ("m_Script", "m_Shader"):
                raise ValueError("unresolved external battle resource reference")
        for key, child in value.items():
            yield from _references(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child, field)


def scan_bundle(data: bytes, bundle_hash: str) -> dict:
    env = UnityPy.load(data)
    declarations = {}
    for path, obj in env.container.items():
        normalized = path.replace("\\", "/").lower()
        if normalized in declarations:
            raise ValueError("ambiguous resource declaration")
        declarations[normalized] = obj
    art = sorted(path for path in declarations if path.startswith("dyn/arts/"))
    battle = {
        match[1]: obj for path, obj in declarations.items()
        if (match := re.fullmatch(r"dyn/battle/prefabs/skins/character/(char_[a-z0-9_]+)/defaultskin.prefab", path))
    }
    battle_only = []
    # Absence is established only for a fully understood battle-only package.
    if len(battle) == 1 and len(declarations) == 1:
        owner, root = next(iter(battle.items()))
        objects = {obj.path_id: obj for obj in env.objects}
        pending, visited, textures, atlases, skeletons = [root.path_id], set(), set(), set(), set()
        while pending:
            identifier = pending.pop()
            if identifier == 0 or identifier in visited:
                continue
            if identifier not in objects:
                raise ValueError("unresolved local battle resource reference")
            visited.add(identifier)
            obj = objects[identifier]
            tree = obj.read_typetree()
            pending.extend(_references(tree))
            if obj.type.name in ("Texture2D", "Sprite", "TextAsset"):
                item = obj.read()
                name = item.m_Name
                if obj.type.name == "Sprite":
                    raise ValueError("unclassified Sprite in battle-only package")
                if obj.type.name == "Texture2D":
                    textures.add(name)
                elif name.endswith(".skel"):
                    skeletons.add(name[:-5])
                elif name.endswith(".atlas"):
                    script = item.m_Script
                    if isinstance(script, bytes):
                        script = script.decode("utf-8")
                    atlases.update(line.strip()[:-4] for line in script.splitlines() if line.strip().endswith(".png"))
        # Spine may resolve atlas pages by name rather than a direct PPtr.
        for obj in env.objects:
            if obj.type.name == "Texture2D" and obj.path_id not in visited:
                textures.add(obj.read().m_Name)
        sprites = {obj.path_id for obj in env.objects if obj.type.name == "Sprite"}
        if not sprites and textures and textures <= atlases and owner in skeletons:
            battle_only.append(owner)
    return {"hash": bundle_hash, "declarations": sorted(declarations), "art": art,
            "battleOnlyOwners": battle_only}


def inventory_usable(state) -> bool:
    if not state or state.scanner_version != SCANNER_VERSION or not state.source_fingerprint:
        return False
    return inventory_complete(state.inventory, state.bundle_hashes)


def inventory_complete(inventory: dict, hashes: dict) -> bool:
    return isinstance(inventory, dict) and bool(hashes) and set(inventory) == set(hashes) and all(
        isinstance(record, dict) and record.get("hash") == hashes[name]
        and all(isinstance(record.get(key), list) and all(isinstance(v, str) for v in record[key])
                for key in ("declarations", "art", "battleOnlyOwners"))
        for name, record in inventory.items()
    )


def classify(excel: Path, valid: set[str], inventory: dict) -> dict:
    skins = json.loads((excel / "skin_table.json").read_text())["charSkins"]
    resources = {}
    for bundle, record in inventory.items():
        for path in record["art"]:
            if path.endswith(".prefab"):
                resources.setdefault(PurePosixPath(path).stem, []).append((bundle, path))
    result = {}
    for skin in sorted(valid):
        info = skins[skin]
        resource = (info.get("illustId") or "").lower()
        matches = resources.get(resource, [])
        owner = info["charId"]
        canonical = inventory.get(f"chararts/{owner}.ab", {})
        owner_has_art = any(f"/{owner}/" in path or PurePosixPath(path).name.startswith(f"illust_{owner}_")
                            for record in inventory.values() for path in record["art"])
        if len(matches) == 1:
            status, reason = "available", "declared_static_illustration"
        elif (not matches and "@" not in skin and resource
              and owner in canonical.get("battleOnlyOwners", []) and not owner_has_art):
            status, reason = "no_standalone_art", "verified_battle_spine_only"
            matches = [(f"chararts/{owner}.ab", canonical["declarations"][0])]
        else:
            status, reason = "unresolved", "missing_or_ambiguous_illustration_evidence"
        result[skin] = {"status": status, "reason": reason, "sources": matches}
    return result


def validate_coverage(coverage: dict, artworks: dict, previous: dict, valid: set[str]) -> None:
    for skin in sorted(valid):
        status = coverage[skin]["status"]
        if status == "unresolved" or (status == "available" and skin not in artworks):
            raise ValueError(f"unresolved artwork coverage: {skin} ({coverage[skin]['reason']})")
        if skin in previous and skin not in artworks:
            raise ValueError(f"previously published artwork disappeared: {skin}")
