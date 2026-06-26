# Changelog

All notable changes to `groundwork` (`gw`) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [0.1.0] — 2026-06-25

Initial release. Standalone recon → map → plan CLI for any repo.

### Added
- **`gw index`** — `system-index.json` (subsystems, language, deps/ports) via `indexer.py`.
- **`gw map`** — interactive `system-map.html` (Cytoscape graph, file tree, source
  previews) via `build_map.py`; vendors its assets for offline render.
- **`gw plan`** — `plan.md` with proof-gated Definitions of Done via `planner.py`;
  findings (untested/stale/high-churn subsystems) derived first-hand from fs + git.
- **`gw run`** — the full pipeline. **`gw doctor`** — accelerator/LLM readiness report.
- Shared options `--out`, `--groups`; `--open` (map/run), `--fest` (plan/run).
- Deterministic-first: needs only Python ≥3.10 + git. Optional accelerators
  (`fest`, `rg`, `fd`, `ctags`, `es`) auto-detected, never required.

### Notes
- Lifted from Pre Atlas audit tooling (`audit/build_system_index.py` +
  `audit/imports/_build_map.py`), de-hardcoded to run against any repo.

### Roadmap
- Inter-subsystem dependency edges (import-graph inference).
- Full fest task-file population from `plan.md`.
- Single-binary distribution (PyInstaller / Nuitka).
