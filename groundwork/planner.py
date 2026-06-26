#!/usr/bin/env python3
"""Turn a system-index.json into a prioritised, proof-gated plan.

Reads ``<out>/system-index.json``, derives findings (untested / stale / hotspot
systems) from the filesystem + git history, and writes ``<out>/plan.md`` where
every task carries a *verifiable* Definition of Done — the same discipline as a
fest ``done_condition``. Optionally scaffolds a fest festival shell when the
``fest`` binary is on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import build_map  # reuse compute_churn + compute_insights (no re-invention)


def load_index(out_dir: Path) -> dict[str, Any]:
    p = out_dir / "system-index.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8"))


def derive(root: Path, index: dict[str, Any]) -> tuple[list[dict], dict, dict]:
    """Build the (services, insights, churn) triple compute_insights needs."""
    services = []
    for e in index.get("entries", []):
        path = e.get("path", e["name"])
        services.append({
            "name": e["name"],
            "group": e.get("group") or path.split("/")[0],
            "path": path,
            "files": e.get("file_count", 0),
            "loc": e.get("total_loc", 0),
        })
    churn = build_map.compute_churn(root, days=30)
    # No curated edges / import graph in standalone mode → edge-derived findings
    # (orphans, hotspots) are unreliable, so the plan leans on filesystem + git
    # signals (missing tests, staleness, churn) which are computed first-hand.
    insights = build_map.compute_insights(services, {}, churn, root, set(), [])
    return services, insights, churn


def _tasks(index: dict[str, Any], services: list[dict], insights: dict) -> list[dict]:
    by_name = {s["name"]: s for s in services}
    tasks: list[dict] = []

    for name in insights.get("missing_tests", []):
        s = by_name.get(name, {})
        tasks.append({
            "theme": "Test coverage",
            "title": f"Add test coverage to `{name}`",
            "why": f"{s.get('files', '?')} source files, no test dir or *_test/*.spec files found.",
            "path": s.get("path", name),
            "dod": [
                f"a test dir (tests/ | __tests__/) or *_test/*.spec files exist under `{s.get('path', name)}`",
                "the project's test runner passes for this subsystem",
            ],
        })

    for name in insights.get("stale", []):
        s = by_name.get(name, {})
        tasks.append({
            "theme": "Staleness triage",
            "title": f"Triage stale subsystem `{name}`",
            "why": f"0 commits in the last 30 days across {s.get('files', '?')} files — confirm it is live, not abandoned.",
            "path": s.get("path", name),
            "dod": [
                "a keep/retire decision is recorded for this subsystem",
                "if retire: it is removed or moved to an archive dir",
            ],
        })

    for hot in insights.get("top_churn", [])[:6]:
        tasks.append({
            "theme": "Hotspot stabilisation",
            "title": f"Stabilise hotspot `{hot['path']}`",
            "why": f"changed {hot['count']}× in 30 days (subsystem `{hot.get('svc', '?')}`) — high-churn = high-risk.",
            "path": hot["path"],
            "dod": [
                "the cause of the churn is identified (feature flux vs instability)",
                "tests cover the churned code path",
            ],
        })

    return tasks


def write_plan_md(out_dir: Path, root: Path, index: dict, services: list[dict],
                  insights: dict) -> Path:
    tasks = _tasks(index, services, insights)
    total_loc = sum(e.get("total_loc", 0) for e in index.get("entries", []))
    total_files = sum(e.get("file_count", 0) for e in index.get("entries", []))
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    repo = index.get("repo_name", root.name)

    lines: list[str] = []
    lines.append(f"# Groundwork plan — {repo}")
    lines.append("")
    lines.append(f"> Generated {gen} · {index.get('subsystem_count', 0)} subsystems · "
                 f"{total_files} source files · {total_loc:,} LOC")
    lines.append("")
    lines.append("Every task below is derived **first-hand** from the filesystem and git "
                 "history (not a hunch), and carries a verifiable Definition of Done.")
    lines.append("")

    if not tasks:
        lines.append("_No test-coverage, staleness, or hotspot findings surfaced. "
                     "The repo looks healthy on those axes._")
    else:
        by_theme: dict[str, list[dict]] = {}
        for t in tasks:
            by_theme.setdefault(t["theme"], []).append(t)
        n = 0
        for theme, group in by_theme.items():
            lines.append(f"## {theme}")
            lines.append("")
            for t in group:
                n += 1
                lines.append(f"### {n}. {t['title']}")
                lines.append(f"- **Path:** `{t['path']}`")
                lines.append(f"- **Why:** {t['why']}")
                lines.append("- **Definition of Done:**")
                for d in t["dod"]:
                    lines.append(f"  - [ ] {d}")
                lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Follow-ups (out of standalone scope)")
    lines.append("- Inter-subsystem dependency edges + import graph (enables orphan / "
                 "hotspot-by-fan-in findings). Supply a `_combined.json` import map or "
                 "wire ctags/rg edge inference.")
    lines.append("- Optional LLM enrichment of the *why* narrative (set `ANTHROPIC_API_KEY`).")
    lines.append("")

    out = out_dir / "plan.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def scaffold_fest(festival_name: str, goal: str, plan_md: Path) -> tuple[bool, str]:
    """Best-effort: create a fest festival shell (flag form) when fest is present."""
    fest = shutil.which("fest")
    if not fest:
        return False, "fest not on PATH — wrote plan.md only"
    workspace = Path.home() / "festival-project"
    if not (workspace / "festivals").is_dir():
        return False, f"no fest workspace at {workspace} — wrote plan.md only"
    try:
        res = subprocess.run(
            [fest, "create", "festival", "--name", festival_name,
             "--type", "implementation", "--goal", goal],
            cwd=str(workspace), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"fest create failed: {exc}"
    if res.returncode != 0:
        return False, f"fest create returned {res.returncode}: {res.stderr.strip()[:200]}"
    return True, (f"created festival '{festival_name}' — populate its tasks from {plan_md.name} "
                  f"(see the fest skill for task-file authoring)")


def run(out_dir: Path, root: Path, *, fest: bool = False) -> dict[str, Any]:
    index = load_index(out_dir)
    services, insights, _churn = derive(root, index)
    plan_md = write_plan_md(out_dir, root, index, services, insights)
    result: dict[str, Any] = {
        "plan_md": str(plan_md),
        "missing_tests": len(insights.get("missing_tests", [])),
        "stale": len(insights.get("stale", [])),
        "hotspots": len(insights.get("top_churn", [])),
        "fest": None,
    }
    if fest:
        ok, msg = scaffold_fest(
            f"groundwork-{index.get('repo_name', root.name)}".lower().replace(" ", "-"),
            f"Address groundwork findings for {index.get('repo_name', root.name)}",
            plan_md,
        )
        result["fest"] = msg
    return result
