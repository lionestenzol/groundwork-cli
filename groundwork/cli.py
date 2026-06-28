#!/usr/bin/env python3
"""groundwork (gw) — map + plan any repo or local codebase, standalone.

A real CLI (no Claude Code harness needed) that packages the recon -> map -> plan
pipeline:

    gw run <repo>      full pipeline: index -> interactive system-map.html -> plan.md
    gw index <repo>    write <repo>/.groundwork/system-index.json
    gw map <repo>      write the interactive system map (Cytoscape graph + drilldown)
    gw plan <repo>     write plan.md  (--fest also scaffolds a festival)
    gw doctor          report which deterministic accelerators + LLM are available

Deterministic-first: needs only Python + git. Optional accelerators (fest, rg,
fd, ctags, delta-scp) and an LLM key enrich the output but are never required.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__, build_map, indexer, planner

DEFAULT_OUT = ".groundwork"


def _map_config(out_dir_name: str) -> dict:
    """Generic build_map config — no Pre-Atlas curation, output kept in <out>."""
    return {
        "schema_version": 1,
        "output_dir": out_dir_name,
        "lattice_mirror_dir": None,
        "substrate_pages_dir": None,
        "purposes": {},
        "new_since_audit": [],
        "retired": [],
        "service_edges": [],
        "preview_lines": 80,
        "preview_char_cap": 5500,
        "preview_file_cap": 150,
        "churn_days": 30,
    }


def _resolve(repo: str) -> Path:
    root = Path(repo).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"[error] not a directory: {root}")
    return root


def _canon_index(idx: dict) -> dict:
    """The content-stable view of a system-index: drop the wall-clock stamp and the
    absolute repo_root so the same tree yields the same hash run-to-run / machine-to-
    machine (repo_name basename is kept -- it's portable)."""
    return {k: v for k, v in idx.items() if k not in {"generated_at", "repo_root"}}


def _content_sha256(payload) -> str:
    """Canonical sha256 over any JSON-able payload (sort_keys -> stable join key).
    The single content-address used by every gw receipt -- shared by cmd_index and
    cmd_run so there is ONE addressing scheme, not two."""
    import hashlib
    import json
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _ensure_index(root: Path, out: str, groups: list[str] | None) -> dict:
    idx_path = root / out / "system-index.json"
    if idx_path.is_file():
        import json
        return json.loads(idx_path.read_text(encoding="utf-8"))
    return indexer.build_index(root, groups=groups, out_dir=out)


def cmd_index(args) -> int:
    root = _resolve(args.repo)
    groups = args.groups.split(",") if args.groups else None
    idx = indexer.build_index(root, groups=groups, out_dir=args.out)
    out_path = root / args.out / "system-index.json"
    if getattr(args, "json", False):
        import json
        # Stable content-address: hash the canonical index WITHOUT the wall-clock
        # generated_at (indexer stamps it) so the same tree yields the same sha256
        # join key run-to-run -- the key the integration seam lifts from this receipt.
        digest = _content_sha256(_canon_index(idx))
        print(json.dumps({
            "tool": "gw", "op": "index", "sha256": digest,
            "subsystem_count": idx.get("subsystem_count", 0),
            "artifacts": {"system_index": str(out_path).replace("\\", "/")},
        }))
        return 0
    print(f"OK indexed {idx['subsystem_count']} subsystems -> {out_path}")
    for e in idx["entries"][:40]:
        port = f" :{e['port']}" if e.get("port") else ""
        print(f"  - {e['path']:<32} {e['language']:<7} {e['framework']:<9} "
              f"{e['file_count']:>4} files  {e['total_loc']:>7} loc{port}")
    if idx["subsystem_count"] > 40:
        print(f"  ... and {idx['subsystem_count'] - 40} more")
    return 0


def cmd_map(args) -> int:
    root = _resolve(args.repo)
    groups = args.groups.split(",") if args.groups else None
    _ensure_index(root, args.out, groups)
    summary = build_map.build(root, _map_config(args.out))
    html = summary["out_primary_html"]
    print(f"OK map -> {html}")
    print(f"  - {summary['subsystems']} subsystems - {summary['running']} responding on a port")
    if args.open:
        _open(html)
    return 0


def cmd_plan(args) -> int:
    root = _resolve(args.repo)
    groups = args.groups.split(",") if args.groups else None
    _ensure_index(root, args.out, groups)
    res = planner.run(root / args.out, root, fest=args.fest)
    print(f"OK plan -> {res['plan_md']}")
    print(f"  - {res['missing_tests']} untested - {res['stale']} stale - {res['hotspots']} hotspots")
    if res["fest"]:
        print(f"  - fest: {res['fest']}")
    return 0


def cmd_run(args) -> int:
    root = _resolve(args.repo)
    groups = args.groups.split(",") if args.groups else None
    as_json = getattr(args, "json", False)
    idx = indexer.build_index(root, groups=groups, out_dir=args.out)
    if not as_json:
        print(f"OK indexed {idx['subsystem_count']} subsystems")
    summary = build_map.build(root, _map_config(args.out))
    if not as_json:
        print(f"OK map  -> {summary['out_primary_html']}")
    res = planner.run(root / args.out, root, fest=args.fest)
    if not as_json:
        print(f"OK plan -> {res['plan_md']}  "
              f"({res['missing_tests']} untested - {res['stale']} stale - {res['hotspots']} hotspots)")
        if res["fest"]:
            print(f"  - fest: {res['fest']}")

    if as_json:
        import json
        index_path = root / args.out / "system-index.json"
        plan_path = Path(res["plan_md"])
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
        festival = res.get("fest")
        recon_ledger = root / ".audit" / "recon-ledger.jsonl"
        ledger_content = (
            recon_ledger.read_text(encoding="utf-8") if recon_ledger.is_file() else None
        )
        # ONE content-address over the combined artifacts: the canonical index (no
        # wall-clock / abs repo_root), the plan text, the sorted festival file list,
        # and the recon ledger when present -- so the dossier hash is stable run-to-run
        # on the same tree. Reuses the same _content_sha256 join key as cmd_index.
        fest_files = (
            sorted(p.name for p in Path(festival).rglob("*") if p.is_file())
            if festival and Path(festival).is_dir()
            else []
        )
        digest = _content_sha256({
            "index": _canon_index(idx),
            "plan_md": plan_text,
            "festival_files": fest_files,
            "recon_ledger": ledger_content,
        })
        print(json.dumps({
            "tool": "gw", "op": "run", "sha256": digest,
            "artifacts": {
                "system_index": str(index_path).replace("\\", "/"),
                "plan_md": str(plan_path).replace("\\", "/"),
                "festival": str(festival).replace("\\", "/") if festival else None,
                "recon_ledger": (
                    str(recon_ledger).replace("\\", "/") if ledger_content is not None else None
                ),
            },
        }))
        return 0

    if args.open:
        _open(summary["out_primary_html"])
    return 0


def cmd_doctor(args) -> int:
    print(f"groundwork v{__version__}\n")
    print(f"  python   : {sys.version.split()[0]}  (required)")
    _report("git", "history -> churn/staleness findings (recommended)")
    print("\n  optional accelerators:")
    for tool, what in (
        ("fest", "scaffold proof-gated festivals from the plan"),
        ("rg", "faster text search (code-recon ladder)"),
        ("fd", "faster file find"),
        ("ctags", "symbol index for dependency edges"),
        ("es", "machine-wide prior-art search (Windows: Everything)"),
    ):
        _report(tool, what, indent=4)
    print("\n  llm enrichment (optional):")
    key = os.environ.get("ANTHROPIC_API_KEY")
    print(f"    {'OK' if key else '-'} ANTHROPIC_API_KEY {'set' if key else 'unset — deterministic narrative only'}")
    return 0


def _report(tool: str, what: str, indent: int = 2) -> None:
    ok = shutil.which(tool) is not None
    print(f"{' ' * indent}{'OK' if ok else '-'} {tool:<7} {'found' if ok else 'missing':<8} {what}")


def _open(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except OSError as exc:
        print(f"[warn] could not open {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gw", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"groundwork {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def add_common(sp, repo=True):
        if repo:
            sp.add_argument("repo", help="path to the repo / local codebase")
        sp.add_argument("--out", default=DEFAULT_OUT,
                        help=f"output dir relative to repo (default: {DEFAULT_OUT})")
        sp.add_argument("--groups", default=None,
                        help="comma-separated subsystem dirs (default: auto-detect)")

    sp = sub.add_parser("index", help="write system-index.json"); add_common(sp)
    sp.add_argument("--json", action="store_true",
                    help="print a content-addressed JSON receipt to stdout (for tooling/seam)")
    sp.set_defaults(func=cmd_index)
    sp = sub.add_parser("map", help="write the interactive system map"); add_common(sp)
    sp.add_argument("--open", action="store_true", help="open the HTML when done")
    sp.set_defaults(func=cmd_map)
    sp = sub.add_parser("plan", help="write plan.md"); add_common(sp)
    sp.add_argument("--fest", action="store_true", help="also scaffold a fest festival")
    sp.set_defaults(func=cmd_plan)
    sp = sub.add_parser("run", help="full pipeline: index + map + plan"); add_common(sp)
    sp.add_argument("--open", action="store_true", help="open the map when done")
    sp.add_argument("--fest", action="store_true", help="also scaffold a fest festival")
    sp.add_argument("--json", action="store_true",
                    help="print ONE content-addressed dossier receipt to stdout (for tooling/seam)")
    sp.set_defaults(func=cmd_run)
    sp = sub.add_parser("doctor", help="report available tools"); add_common(sp, repo=False)
    sp.set_defaults(func=cmd_doctor)

    for stream in (sys.stdout, sys.stderr):  # legacy Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
