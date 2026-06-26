# groundwork (`gw`)

**Point it at any repo or local codebase. Get back a current, accurate system map *and* a proof-gated plan.** No Claude Code harness, no LLM key, no config required — just Python + git.

```
gw run <repo>      # full pipeline: index -> interactive system-map.html -> plan.md
gw index <repo>    # write <repo>/.groundwork/system-index.json
gw map <repo>      # write the interactive system map (Cytoscape graph + file drilldown)
gw plan <repo>     # write plan.md   (--fest also scaffolds a festival)
gw doctor          # report which deterministic accelerators + LLM are available
```

## Why

Hand-authored architecture docs rot the moment the code moves. `groundwork` *generates* the map from the source on every run, so it can't drift. It replaces the "someone typed a `system-map.html` once and it now says 3 services when there are 35" failure mode with a deterministic generator.

## The pipeline

| Stage | Module | Question it answers | Output |
|-------|--------|---------------------|--------|
| 1 · INDEX | `indexer.py` | "What subsystems exist, in what language, with what deps/ports?" | `system-index.json` |
| 2 · MAP | `build_map.py` | "How does it all fit together?" | interactive `system-map.html` (Cytoscape graph, file tree, source previews, insights) |
| 3 · PLAN | `planner.py` | "What's the work, and how do we *prove* it's done?" | `plan.md` — tasks with verifiable Definitions of Done (optionally a `fest` festival) |

Findings in the plan are derived **first-hand** from the filesystem and git history (untested subsystems, stale subsystems with no recent commits, high-churn hotspots) — not guessed.

## Install

```bash
pipx install ./groundwork-cli      # or: pip install -e .
gw doctor
```

Until installed you can run it in place: `python -m groundwork.cli run <repo>`.

## Requirements

- **Required:** Python ≥ 3.10, and git (for churn/staleness findings).
- **Optional accelerators** (auto-detected, never required): `fest` (festival scaffolding), `rg`/`fd`/`ctags` (code-recon ladder), `es` (machine-wide prior-art). `gw doctor` shows what's present.
- **Optional LLM:** set `ANTHROPIC_API_KEY` to enable narrative enrichment (roadmap). Off by default — output is fully deterministic.

## Output

Everything lands in `<repo>/.groundwork/` (add it to `.gitignore`). The map vendors its Cytoscape assets there so it renders offline; if the vendored copy is missing it falls back to the CDN.

## Subsystem detection

Auto-detects, in order: known monorepo dirs (`services/ apps/ tools/ packages/ libs/ …`) → top-level code dirs → single-package fallback (the repo itself). Override with `--groups a,b,c`.

## Command reference

Every subcommand takes a `repo` path (except `doctor`) and these options:

| Option | Applies to | Default | Meaning |
|--------|-----------|---------|---------|
| `--out OUT` | all | `.groundwork` | output dir, relative to the repo |
| `--groups GROUPS` | all | auto-detect | comma-separated subsystem dirs to map |
| `--open` | `map`, `run` | off | open the generated HTML when done |
| `--fest` | `plan`, `run` | off | also scaffold a `fest` festival from the plan |

| Command | Usage | Produces |
|---------|-------|----------|
| `gw index` | `gw index <repo> [--out] [--groups]` | `<out>/system-index.json` |
| `gw map` | `gw map <repo> [--out] [--groups] [--open]` | `<out>/system-map.html` (+ vendored cytoscape) |
| `gw plan` | `gw plan <repo> [--out] [--groups] [--fest]` | `<out>/plan.md` |
| `gw run` | `gw run <repo> [--out] [--groups] [--open] [--fest]` | index + map + plan |
| `gw doctor` | `gw doctor [--out] [--groups]` | a readiness report (no repo needed) |

## Examples

```bash
gw run .                              # map + plan the current repo
gw run "C:\path\to\repo" --open       # full pipeline, then open the map
gw map . --groups services,apps,tools # only these subsystems
gw plan . --fest                      # scaffold a fest festival from the plan
gw index . --out .gw                  # write the index to a custom dir
gw doctor                             # which accelerators + LLM are available
```

## Troubleshooting

- **Map is blank / falls back to CDN offline** — the vendored Cytoscape assets in `<out>/` are missing; re-run `gw map <repo>`.
- **"no subsystems found"** — auto-detection found nothing; pass them explicitly with `--groups a,b,c`.
- **Churn / staleness findings are empty** — the target isn't a git repo (or is a shallow clone); those findings need git history.
- **`gw` not found after install** — open a new shell so PATH refreshes, or run `python -m groundwork.cli ...` in place.

## Provenance

Lifted from Pre Atlas's audit tooling (`audit/build_system_index.py` + `audit/imports/_build_map.py`) and de-hardcoded so it runs against any repo. The map generator and its Cytoscape view are reused wholesale; the indexer was generalized and the plan stage is new. This is the `groundwork` / `code-recon` + `fest` pipeline, lifted out of the Claude Code skill harness into a standalone tool.

## Roadmap

- Inter-subsystem dependency **edges** (import-graph inference via ctags/rg) → enables orphan + fan-in-hotspot findings the standalone mode currently skips.
- Full **fest task-file population** from `plan.md` (today `--fest` creates the festival shell).
- **LLM enrichment** of the *why* narrative behind a key.
- Package to a **single binary** (PyInstaller / Nuitka) for zero-runtime distribution.
