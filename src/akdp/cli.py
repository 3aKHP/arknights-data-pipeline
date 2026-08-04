"""akdp CLI: fetch / baseline / merge / validate / package / publish / run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import baseline as baseline_mod
from . import fetch as fetch_mod
from . import merge as merge_mod
from . import package as package_mod
from . import publish as publish_mod
from . import validate as validate_mod
from .normalize import normalize_extraction


def _load_probes(workdir: Path, args: argparse.Namespace) -> dict:
    probes: dict = {}
    pf = workdir / "probes.json"
    if pf.exists():
        probes = json.loads(pf.read_text(encoding="utf-8"))
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
    manifest = package_mod.package_candidate(
        workdir / "candidate",
        workdir / "dist",
        source_info=merge_info.get("source"),
        merge_stats=merge_info.get("stats"),
        validation=validation,
        tool_versions=_tool_versions(),
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


def cmd_run(args: argparse.Namespace) -> int:
    for cmd in (cmd_baseline, cmd_fetch, cmd_merge, cmd_validate, cmd_package):
        rc = cmd(args)
        if rc != 0:
            print(f"[run] step {cmd.__name__} failed, stopping (fail-closed)", file=sys.stderr)
            return rc
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

    for name, func, help_ in (
        ("merge", cmd_merge, "normalize + merge into candidate tree"),
        ("package", cmd_package, "build distribution zips + manifest"),
    ):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)

    p = sub.add_parser("validate", help="run validation gates")
    p.add_argument("--probe-operator", action="append")
    p.add_argument("--probe-event", action="append")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("publish", help="publish releases (dry-run by default)")
    p.add_argument("--execute", action="store_true", help="actually create GitHub releases")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("run", help="baseline -> fetch -> merge -> validate -> package")
    p.add_argument("--clobber", action="store_true")
    p.add_argument("--attempts", type=int, default=5)
    p.add_argument("--probe-operator", action="append")
    p.add_argument("--probe-event", action="append")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
