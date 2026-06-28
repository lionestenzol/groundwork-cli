"""Prove `gw run <repo> --json` emits ONE content-addressed dossier whose sha256 is
stable run-to-run on the same tree (wall-clock + abs repo_root stripped), and that
mutating a tracked source file changes the hash.

The proof IS this passing test: it drives the real CLI (groundwork.cli.main) over a
tmp git fixture, captures stdout twice, and compares the parsed sha256 values.
"""
from __future__ import annotations

import json
import subprocess
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

from groundwork import cli


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_fixture(repo: Path) -> None:
    """A couple of source files + a real git repo so churn/index logic has history."""
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "util.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _run_json(repo: Path) -> dict:
    buf = StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["run", str(repo), "--json"])
    assert rc == 0, "gw run --json should exit 0"
    # The dossier is the LAST stdout line (the only JSON object printed in --json mode).
    line = buf.getvalue().strip().splitlines()[-1]
    return json.loads(line)


def test_dossier_shape_and_determinism(tmp_path: Path) -> None:
    repo = tmp_path / "fixture"
    repo.mkdir()
    _make_fixture(repo)

    first = _run_json(repo)
    second = _run_json(repo)

    # Shape: a gw run dossier with the documented artifact keys.
    assert first["tool"] == "gw"
    assert first["op"] == "run"
    assert len(first["sha256"]) == 64
    assert set(first["artifacts"]) == {"system_index", "plan_md", "festival", "recon_ledger"}

    # Determinism: same tree -> same content-address across runs.
    assert first["sha256"] == second["sha256"], (
        f"hash drifted run-to-run: {first['sha256']} != {second['sha256']}"
    )


def test_dossier_hash_changes_on_source_edit(tmp_path: Path) -> None:
    repo = tmp_path / "fixture"
    repo.mkdir()
    _make_fixture(repo)

    before = _run_json(repo)["sha256"]

    # Modify a tracked source file so the indexed content changes (more lines ->
    # higher total_loc), which must flow through to the dossier content-address.
    (repo / "app.py").write_text(
        "def main():\n    return 42  # changed\n\n\ndef extra():\n    return 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "edit app.py")

    after = _run_json(repo)["sha256"]

    assert before != after, "editing a tracked source file should change the dossier hash"
