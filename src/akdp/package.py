"""Package the candidate tree into the three distribution zips + manifest."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import __version__, contract
from .normalize import policy_manifest

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _write_deterministic(zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """Write a file with stable ZIP metadata so identical inputs hash alike."""
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9)


def _zip_tree(src_root: Path, arc_prefix: str, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(src_root.rglob("*")):
            if p.is_file():
                _write_deterministic(zf, p, f"{arc_prefix}/{p.relative_to(src_root)}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_provenance() -> dict[str, object]:
    """Capture the pipeline revision and whether local files were modified."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": bool(dirty)}


def _tool_provenance(tool_versions: dict | None) -> dict:
    """Add reproducibility metadata without assuming every tool is installed."""
    tools = dict(tool_versions or {})
    flatc = shutil.which("flatc")
    if flatc:
        actual_sha = _sha256(Path(flatc))
        tools.setdefault("flatc", {})
        if isinstance(tools["flatc"], dict):
            tools["flatc"].setdefault("sha256", actual_sha)
            tools["flatc"].setdefault("sourceCommit", contract.TORAPPU_FLATC_COMMIT)
            if actual_sha != contract.TORAPPU_FLATC_SHA256:
                tools["flatc"]["mismatched"] = True
    else:
        tools.setdefault("flatc", {"availableAtPackageTime": False})
    return tools


def _llm_digest(ledger_path: Path | None) -> dict:
    """Sanitized public digest of the per-run LLM call ledger.

    The ledger itself is private evidence with full provider detail; this
    digest carries only counts, hashes and irreversible fingerprints.
    """
    if ledger_path is None or not ledger_path.is_file():
        return {}
    from collections import Counter

    records = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    if not records:
        return {}
    return {
        "attempts": len(records),
        "verdicts": dict(sorted(Counter(
            str(r.get("verdict")) for r in records).items())),
        "endpoint_fingerprints": sorted({
            r["endpoint_fingerprint"] for r in records if r.get("endpoint_fingerprint")}),
        "model_fingerprints": sorted({
            r["model_fingerprint"] for r in records if r.get("model_fingerprint")}),
        "ledger_sha256": _sha256(ledger_path),
        "prompt_version": records[0].get("prompt_version"),
        "gate_version": records[0].get("gate_version"),
    }


def package_candidate(
    candidate: Path,
    dist: Path,
    *,
    source_info: dict | None = None,
    merge_stats: dict | None = None,
    validation: dict | None = None,
    story_stats: dict | None = None,
    summarize_stats: dict | None = None,
    tool_versions: dict | None = None,
    normalization: dict | None = None,
    revision: int = 1,
    llm_ledger: Path | None = None,
) -> dict:
    """Build zh_CN-excel.zip / zh_CN-levels.zip / zh_CN.zip + manifest.json.

    Returns the manifest dict. `revision` becomes manifest
    publicationRevision: 1 for normal releases, N >= 2 for datarev repair
    revisions of the same source versionId.
    """
    zh = candidate / "zh_CN"
    dist.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Path] = {
        contract.EXCEL_ASSET: dist / contract.EXCEL_ASSET,
        contract.LEVELS_ASSET: dist / contract.LEVELS_ASSET,
        contract.STORY_ASSET: dist / contract.STORY_ASSET,
    }

    _zip_tree(zh / "gamedata/excel", "zh_CN/gamedata/excel", assets[contract.EXCEL_ASSET])
    _zip_tree(zh / "gamedata/levels", "zh_CN/gamedata/levels", assets[contract.LEVELS_ASSET])

    # story zip = full excel + story JSONs + ASTR index files
    with zipfile.ZipFile(assets[contract.STORY_ASSET], "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted((zh / "gamedata/excel").rglob("*")):
            if p.is_file():
                _write_deterministic(
                    zf, p,
                    f"zh_CN/gamedata/excel/{p.relative_to(zh / 'gamedata/excel')}",
                )
        story_dir = zh / "gamedata/story"
        if story_dir.exists():
            for p in sorted(story_dir.rglob("*.json")):
                _write_deterministic(
                    zf, p, f"zh_CN/gamedata/story/{p.relative_to(story_dir)}",
                )
        for name in contract.STORY_INDEX_FILES:
            p = zh / name
            if p.exists():
                _write_deterministic(zf, p, f"zh_CN/{name}")

    manifest = {
        "pipelineVersion": __version__,
        "publicationRevision": revision,
        "pipeline": _git_provenance(),
        "contractVersion": contract.CONTRACT_VERSION,
        "source": source_info or {},
        "tools": _tool_provenance(tool_versions),
        "normalization": normalization or policy_manifest(),
        "merge": merge_stats or {},
        "story": story_stats or {},
        "summarize": summarize_stats or {},
        "validation": validation or {},
        "llm": _llm_digest(llm_ledger),
        "assets": {
            name: {"sha256": _sha256(p), "size": p.stat().st_size}
            for name, p in assets.items()
        },
    }
    (dist / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
