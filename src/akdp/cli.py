"""akdp CLI: fetch / baseline / merge / validate / package / publish / run.

Image pipeline sub-commands: images-fetch / images-extract.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Load .env in CWD into os.environ (no external dependency)."""
    p = Path(".env")
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        os.environ.setdefault(key, val)


_load_dotenv()

from . import baseline as baseline_mod
from . import check as check_mod
from . import contract
from . import fetch as fetch_mod
from . import merge as merge_mod
from . import package as package_mod
from . import publish as publish_mod
from . import story as story_mod
from . import summarize as summarize_mod
from . import validate as validate_mod
from .normalize import normalize_extraction, policy_manifest


def _load_probes(workdir: Path, args: argparse.Namespace) -> dict:
    probes: dict = {}
    for pf in (getattr(args, "probes_file", None), workdir / "probes.json"):
        if pf is not None and pf.exists():
            probes.update(json.loads(pf.read_text(encoding="utf-8")))
    if args.probe_operator:
        probes.setdefault("operators", []).extend(args.probe_operator)
    if args.probe_event:
        probes.setdefault("events", []).extend(args.probe_event)
    return probes


def _tool_versions() -> dict:
    import importlib.metadata

    versions = {}
    for pkg in ("arkprts", "unitypy"):
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            pass
    astr = Path("vendor/ASTR-Script") / ".git"
    if astr.exists():
        proc = subprocess.run(
            ["git", "-C", str(astr.parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0:
            versions["ASTR-Script"] = {
                "sourceCommit": proc.stdout.strip(),
                "source": "050644zf/ASTR-Script",
            }
    flatc = shutil.which("flatc")
    if flatc:
        proc = subprocess.run([flatc, "--version"], capture_output=True, text=True, check=False)
        versions["flatc"] = {
            "version": proc.stdout.strip() or proc.stderr.strip(),
            "sourceCommit": contract.TORAPPU_FLATC_COMMIT,
        }
    return versions


def cmd_baseline(args: argparse.Namespace) -> int:
    baseline_mod.download_baseline(args.workdir / "baseline", clobber=args.clobber)
    print(f"baseline ready at {args.workdir / 'baseline'}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    extract = args.workdir / "extract"
    fetch_mod.run_arkprts(extract, server=args.server, attempts=args.attempts)
    problems = fetch_mod.check_extraction(extract, server=args.server)
    if problems:
        for p in problems:
            print(f"[fetch] ERROR: {p}", file=sys.stderr)
        return 1
    print(f"extraction ready at {extract}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    workdir = args.workdir
    zh_view = normalize_extraction(workdir / "extract", workdir / "normalized", server=args.server)
    stats = merge_mod.merge_trees(
        workdir / "baseline" / "zh_CN",
        zh_view,
        workdir / "candidate" / "zh_CN",
    )
    info = merge_mod.load_hot_update_version(workdir / "extract")
    (workdir / "merge.json").write_text(
        json.dumps({"stats": stats.to_dict(), "source": info}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    d = stats.to_dict()
    print(f"merged: +{d['added']} ~{d['changed']} ={d['unchanged']} kept {d['kept_from_baseline']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    remote = check_mod.fetch_remote_version(args.server)
    published = check_mod.latest_published_version()
    changed = check_mod.version_changed(remote["versionId"], published)
    print(f"remote versionId:    {remote['versionId']} (manifest {remote['manifestVersion']})")
    print(f"published versionId: {published}")
    print("CHANGED" if changed else "UNCHANGED")
    return 0 if changed else 2


def cmd_story(args: argparse.Namespace) -> int:
    stats = story_mod.convert_stories(args.workdir / "candidate", args.astr_path)
    (args.workdir / "story.json").write_text(
        json.dumps(stats.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"story: converted {len(stats.converted)}, "
          f"missing source {len(stats.missing_source)}, failed {len(stats.failed)}")
    for f in stats.failed:
        print(f"[story] FAIL {f['story']}: {f['error']}", file=sys.stderr)
    return 1 if stats.failed else 0


def cmd_summarize(args: argparse.Namespace) -> int:
    stats = summarize_mod.run_summarize(args.workdir / "candidate")
    (args.workdir / "summarize.json").write_text(
        json.dumps(stats.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if stats.failed_details else 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_mod.validate_candidate(
        args.workdir / "candidate",
        baseline=args.workdir / "baseline",
        probes=_load_probes(args.workdir, args),
    )
    (args.workdir / "validation.json").write_text(
        json.dumps(
            {"errors": result.errors, "warnings": result.warnings, "metrics": result.metrics},
            ensure_ascii=False, indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for w in result.warnings:
        print(f"[validate] WARN: {w}")
    for e in result.errors:
        print(f"[validate] ERROR: {e}", file=sys.stderr)
    print(f"validation {'PASS' if result.ok else 'FAIL'}")
    return 0 if result.ok else 1


def cmd_package(args: argparse.Namespace) -> int:
    workdir = args.workdir
    merge_info = json.loads((workdir / "merge.json").read_text(encoding="utf-8")) if (workdir / "merge.json").exists() else {}
    validation = json.loads((workdir / "validation.json").read_text(encoding="utf-8")) if (workdir / "validation.json").exists() else {}
    story_stats = json.loads((workdir / "story.json").read_text(encoding="utf-8")) if (workdir / "story.json").exists() else {}
    summarize_stats = json.loads((workdir / "summarize.json").read_text(encoding="utf-8")) if (workdir / "summarize.json").exists() else {}
    manifest = package_mod.package_candidate(
        workdir / "candidate",
        workdir / "dist",
        source_info=merge_info.get("source"),
        merge_stats=merge_info.get("stats"),
        validation=validation,
        story_stats=story_stats,
        summarize_stats=summarize_stats,
        tool_versions=_tool_versions(),
        normalization=policy_manifest(),
    )
    for name, meta in manifest["assets"].items():
        print(f"packaged {name} ({meta['size'] / 1e6:.1f} MB, sha256 {meta['sha256'][:12]}…)")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    workdir = args.workdir
    merge_info = json.loads((workdir / "merge.json").read_text(encoding="utf-8"))
    source = merge_info.get("source") or {}
    source_id = source.get("versionId") or "manual"
    publish_mod.publish(
        workdir / "dist", source_id=source_id,
        title_suffix=source_id, dry_run=not args.execute,
    )
    return 0


def cmd_images_fetch(args: argparse.Namespace) -> int:
    from . import images_fetch

    cache_dir = args.workdir / "images-cache"
    hashes_file = cache_dir / "hashes.json"
    prev_hashes: dict[str, str] = {}
    if hashes_file.exists():
        prev_hashes = json.loads(hashes_file.read_text(encoding="utf-8")).get("hashes", {})

    stats, info = images_fetch.fetch_image_bundles(
        cache_dir, server=args.server, prev_hashes=prev_hashes,
    )
    hashes_file.write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    d = stats.to_dict()
    print(f"images-fetch: downloaded {d['downloaded']}, "
          f"unchanged {d['skipped_unchanged']}, failed {d['failed']} "
          f"(versionId={info.get('versionId')})")
    for f in stats.failed:
        print(f"[images-fetch] FAIL {f['bundle']}: {f['error']}", file=sys.stderr)
    return 1 if stats.failed else 0


def cmd_images_extract(args: argparse.Namespace) -> int:
    from . import images_extract

    excel = args.excel_dir
    if not excel.is_dir():
        print(f"[images-extract] excel dir not found: {excel}", file=sys.stderr)
        print("  Pass --excel-dir <path> pointing at gamedata/excel/", file=sys.stderr)
        return 1

    stats = images_extract.extract_images(
        args.workdir / "images-cache",
        args.workdir / "images-out",
        excel,
    )
    (args.workdir / "images-extract.json").write_text(
        json.dumps(stats.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    d = stats.to_dict()
    print(f"images-extract: {d['extracted']} sprites extracted, "
          f"{d['skipped_not_skin']} skipped, {d['failed']} failed "
          f"({d['bundles_processed']} bundles)")
    for f in stats.failed:
        label = f.get('name') or f.get('bundle', '?')
        print(f"[images-extract] FAIL {label}: {f['error']}", file=sys.stderr)
    return 1 if stats.failed else 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.force:
        remote = check_mod.fetch_remote_version(args.server)
        published = check_mod.latest_published_version()
        if not check_mod.version_changed(
            remote["versionId"], published, force=args.force,
        ):
            print(f"[run] versionId unchanged ({published}), nothing to do")
            return 0
        print(f"[run] version changed: {published} -> {remote['versionId']}")
    for cmd in (cmd_baseline, cmd_fetch, cmd_merge, cmd_story, cmd_summarize, cmd_validate, cmd_package):
        rc = cmd(args)
        if rc != 0:
            print(f"[run] step {cmd.__name__} failed, stopping (fail-closed)", file=sys.stderr)
            return rc
    if getattr(args, "publish", False):
        print("[run] auto-publishing")
        args.execute = True
        return cmd_publish(args)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="akdp", description=__doc__)
    ap.add_argument("--workdir", type=Path, default=Path("work"))
    ap.add_argument("--server", default="cn")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("baseline", help="download current releases as baseline")
    p.add_argument("--clobber", action="store_true")
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("fetch", help="run arkprts extraction with retries")
    p.add_argument("--attempts", type=int, default=5)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("check", help="compare remote versionId with latest published release")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("story", help="convert story txt to JSON via ASTR-Script")
    p.add_argument("--astr-path", type=Path, default=Path("vendor/ASTR-Script"))
    p.set_defaults(func=cmd_story)

    p = sub.add_parser("summarize", help="generate incremental LLM story/event summaries")
    p.set_defaults(func=cmd_summarize)

    for name, func, help_ in (
        ("merge", cmd_merge, "normalize + merge into candidate tree"),
        ("package", cmd_package, "build distribution zips + manifest"),
    ):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)

    p = sub.add_parser("validate", help="run validation gates")
    p.add_argument("--probes-file", type=Path, default=Path("config/probes.json"))
    p.add_argument("--probe-operator", action="append")
    p.add_argument("--probe-event", action="append")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("publish", help="publish releases (dry-run by default)")
    p.add_argument("--execute", action="store_true", help="actually create GitHub releases")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("images-fetch", help="download changed chararts/skinpack AB bundles")
    p.set_defaults(func=cmd_images_fetch)

    p = sub.add_parser("images-extract", help="extract Sprite PNGs from cached AB bundles")
    p.add_argument("--excel-dir", type=Path, required=True,
                   help="path to gamedata/excel/ (containing skin_table.json + character_table.json)")
    p.set_defaults(func=cmd_images_extract)

    p = sub.add_parser("run", help="check -> baseline -> fetch -> merge -> story -> summarize -> validate -> package")
    p.add_argument("--clobber", action="store_true")
    p.add_argument("--force", action="store_true", help="run even if versionId is unchanged")
    p.add_argument("--publish", action="store_true", help="auto-publish after successful run")
    p.add_argument("--attempts", type=int, default=5)
    p.add_argument("--astr-path", type=Path, default=Path("vendor/ASTR-Script"))
    p.add_argument("--probes-file", type=Path, default=Path("config/probes.json"))
    p.add_argument("--probe-operator", action="append")
    p.add_argument("--probe-event", action="append")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
