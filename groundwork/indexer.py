#!/usr/bin/env python3
"""Repo-agnostic system indexer.

Walks a repo's subsystem directories and emits ``<out>/system-index.json`` — the
input both the map generator (``build_map``) and the planner consume.

Ported from Pre Atlas's ``audit/build_system_index.py`` and de-hardcoded:
- repo root is a parameter (no machine-specific constant);
- subsystem groups are auto-detected (monorepo dirs → top-level code dirs →
  single-package fallback) or supplied explicitly;
- ports are detected generically by scanning entry points / config, instead of
  parsing one project's bespoke start script.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Monorepo group dirs we recognise, in priority order.
GROUP_CANDIDATES = ["services", "apps", "tools", "packages", "libs", "crates", "cmd", "modules"]

EXCLUDE_DIRS = {
    "node_modules", ".venv", "venv", "dist", "build",
    ".git", ".next", "target", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "coverage", ".turbo",
    ".groundwork", ".audit", ".idea", ".vscode",
}
SOURCE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".html", ".css", ".scss", ".json", ".vue",
    ".rs", ".go", ".java", ".cs", ".rb", ".php", ".kt", ".swift",
}
ENTRY_CANDIDATES = [
    "src/api/server.ts", "src/server.ts", "server.ts", "src/index.ts", "index.ts",
    "src/main.ts", "main.ts",
    "server.mjs", "server.js", "index.js", "src/index.js",
    "server.py", "main.py", "app.py", "run.py", "serve.py", "__main__.py",
    "src/server.py", "src/main.py", "src/app.py", "__init__.py",
    "index.html", "Cargo.toml", "go.mod", "manifest.json",
]
PORT_RE = re.compile(r"(?:PORT|port|listen|--port|EXPOSE)\D{0,12}(\d{4,5})")

# Counted as files but NOT toward LOC — data formats, not authored code effort.
DATA_EXT = {".json", ".geojson"}
# Excluded entirely — generated / minified / backup / lock artifacts that distort counts.
SKIP_FILE = re.compile(r"(\.min\.(js|css)|\.bundle\.js|-data\.js|\.bak(\.[\w.-]+)?|\.lock|-lock\.json)$", re.IGNORECASE)
# Vendored-code dir names pruned during the walk (alongside nested-.git detection).
VENDOR_DIRS = {"vendor", "vendored", "third_party", "third-party", "_repos"}
# Files larger than this are treated as generated data, not source (skip LOC).
LOC_SIZE_CAP = 2 * 1024 * 1024  # 2 MB


# ── dependency readers (generic) ────────────────────────────────────────────
def read_package_json(p: Path) -> list[str]:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        d = data.get(key)
        if isinstance(d, dict):
            out.update(d.keys())
    return sorted(out)


def read_requirements_txt(p: Path) -> list[str]:
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    deps: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if m:
            deps.append(m.group(1))
    return deps


def read_pyproject(p: Path) -> list[str]:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    deps: list[str] = []
    m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().strip(",").strip('"').strip("'")
            if not line:
                continue
            name = re.match(r"^([A-Za-z0-9_.\-]+)", line)
            if name:
                deps.append(name.group(1))
    return deps


def read_cargo_toml(p: Path) -> list[str]:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    deps: list[str] = []
    m = re.search(r"\[dependencies\](.*?)(?:\n\[|\Z)", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            nm = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*=", line)
            if nm:
                deps.append(nm.group(1))
    return deps


def detect_language(node_files: list[str], py_files: list[str], html_files: list[str],
                    other: dict[str, int]) -> str:
    has_ts = any(f.endswith((".ts", ".tsx")) for f in node_files)
    has_js = any(f.endswith((".js", ".jsx", ".mjs", ".cjs")) for f in node_files)
    has_py = len(py_files) > 0
    has_html = len(html_files) > 0
    if has_ts and has_py:
        return "mixed"
    if has_ts:
        return "ts"
    if has_py:
        return "py"
    if has_js:
        return "js"
    for lang, count in sorted(other.items(), key=lambda kv: -kv[1]):
        if count > 0:
            return lang
    if has_html:
        return "html"
    return "unknown"


def detect_framework(deps: list[str], language: str, files_seen: set[str]) -> str:
    dl = {d.lower() for d in deps}
    if "express" in dl:
        return "express"
    if "next" in dl or any(d.startswith("next") for d in dl):
        return "next"
    if "fastapi" in dl:
        return "fastapi"
    if "flask" in dl:
        return "flask"
    if "django" in dl:
        return "django"
    if "uvicorn" in dl and language == "py":
        return "fastapi"
    if "react" in dl and "next" not in dl:
        return "react"
    if "vue" in dl:
        return "vue"
    if "index.html" in files_seen and not deps:
        return "vanilla"
    if language == "html":
        return "vanilla"
    return "unknown"


def detect_port(subsystem_dir: Path, entry_points: list[str]) -> int | None:
    """Scan entry points + common config for a literal port."""
    scan: list[Path] = []
    for ep in entry_points[:6]:
        p = subsystem_dir / ep
        if p.is_file():
            scan.append(p)
    for name in ("docker-compose.yml", "docker-compose.yaml", ".env",
                 ".env.example", "launch.json", "Dockerfile"):
        p = subsystem_dir / name
        if p.is_file():
            scan.append(p)
    for p in scan:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = PORT_RE.search(text)
        if m:
            try:
                port = int(m.group(1))
            except ValueError:
                continue
            if 1024 <= port <= 65535:
                return port
    return None


def _has_source(d: Path, limit: int = 2000) -> bool:
    seen = 0
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS and not x.startswith(".")]
        for f in files:
            if Path(f).suffix.lower() in SOURCE_EXT:
                return True
            seen += 1
            if seen > limit:
                return False
    return False


def walk_subsystem(subsystem_dir: Path) -> dict[str, Any]:
    deps: set[str] = set()
    file_count = 0
    total_loc = 0
    files_seen: set[str] = set()
    node_files: list[str] = []
    py_files: list[str] = []
    html_files: list[str] = []
    other_langs: dict[str, int] = {"go": 0, "rust": 0, "java": 0, "cs": 0, "rb": 0, "php": 0}
    entry_points: list[str] = []

    for cand in ENTRY_CANDIDATES:
        if (subsystem_dir / cand).is_file():
            entry_points.append(cand.replace("\\", "/"))

    for root, dirs, files in os.walk(subsystem_dir):
        # Prune excluded dirs, vendor dirs, and nested git repos (vendored clones).
        dirs[:] = [d for d in dirs
                   if d not in EXCLUDE_DIRS and not d.startswith(".")
                   and d.lower() not in VENDOR_DIRS
                   and not (Path(root) / d / ".git").exists()]
        rel_root = Path(root).relative_to(subsystem_dir)
        for f in files:
            fp = Path(root) / f
            files_seen.add(f)
            if rel_root == Path("."):
                if f == "package.json":
                    deps.update(read_package_json(fp))
                elif f == "requirements.txt":
                    deps.update(read_requirements_txt(fp))
                elif f == "pyproject.toml":
                    deps.update(read_pyproject(fp))
                elif f == "Cargo.toml":
                    deps.update(read_cargo_toml(fp))
            ext = fp.suffix.lower()
            if ext in SOURCE_EXT:
                if SKIP_FILE.search(f):
                    continue  # generated / minified / backup / lock — exclude entirely
                file_count += 1
                if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    node_files.append(f)
                elif ext == ".py":
                    py_files.append(f)
                elif ext == ".html":
                    html_files.append(f)
                elif ext == ".go":
                    other_langs["go"] += 1
                elif ext == ".rs":
                    other_langs["rust"] += 1
                elif ext == ".java":
                    other_langs["java"] += 1
                elif ext == ".cs":
                    other_langs["cs"] += 1
                elif ext == ".rb":
                    other_langs["rb"] += 1
                elif ext == ".php":
                    other_langs["php"] += 1
                # LOC = authored code only: skip data formats and large generated files.
                if ext not in DATA_EXT:
                    try:
                        if fp.stat().st_size <= LOC_SIZE_CAP:
                            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                                total_loc += sum(1 for _ in fh)
                    except OSError:
                        pass

    language = detect_language(node_files, py_files, html_files, other_langs)
    framework = detect_framework(sorted(deps), language, files_seen)
    return {
        "deps": sorted(deps),
        "entry_points": entry_points,
        "file_count": file_count,
        "total_loc": total_loc,
        "language": language,
        "framework": framework,
        "port": detect_port(subsystem_dir, entry_points),
    }


def discover_systems(root: Path, groups: list[str] | None) -> list[tuple[str, str, Path, str]]:
    """Return (group, name, dir, rel_path) tuples for each subsystem.

    Strategy: configured/known monorepo dirs → each top-level code dir →
    single-package fallback (the repo itself).
    """
    found = [g for g in (groups or GROUP_CANDIDATES) if (root / g).is_dir()]
    if found:
        systems = []
        for g in found:
            for entry in sorted((root / g).iterdir()):
                if entry.is_dir() and entry.name not in EXCLUDE_DIRS and not entry.name.startswith("."):
                    systems.append((g, entry.name, entry, f"{g}/{entry.name}"))
        if systems:
            return systems
    subdirs = [d for d in sorted(root.iterdir())
               if d.is_dir() and d.name not in EXCLUDE_DIRS and not d.name.startswith(".")]
    code_subdirs = [d for d in subdirs if _has_source(d)]
    if code_subdirs:
        return [(d.name, d.name, d, d.name) for d in code_subdirs]
    return [(".", root.name, root, ".")]


def build_index(root: Path, *, groups: list[str] | None = None,
                out_dir: str = ".groundwork") -> dict[str, Any]:
    """Walk `root`, write <root>/<out_dir>/system-index.json, return the index."""
    root = root.resolve()
    systems = discover_systems(root, groups)
    entries: list[dict[str, Any]] = []
    for group, name, sdir, rel_path in systems:
        data = walk_subsystem(sdir)
        entries.append({
            "path": rel_path,
            "group": group,
            "name": name,
            "language": data["language"],
            "framework": data["framework"],
            "deps": data["deps"],
            "entry_points": data["entry_points"],
            "file_count": data["file_count"],
            "total_loc": data["total_loc"],
            "port": data["port"],
            "in_autostart": False,
        })

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo_root": str(root),
        "repo_name": root.name,
        "subsystem_count": len(entries),
        "autostart_count": 0,
        "entries": entries,
    }
    out_path = root / out_dir / "system-index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output
