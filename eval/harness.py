"""Shared pipeline for building a graph from the dummy org.

Both the eval runner and the pytest suite need the same thing: a fresh, isolated,
deterministically-seeded dummy org with its graph fully built (extract -> ingest ->
compact) under the LLM MOCK. This module centralizes that so the two never drift.

Isolation: we copy the dummy-org SOURCE (never its .git) into a scratch directory,
then run seed-history.sh (and optionally scenario-break.sh) there. The checked-in
dummy-org stays clean; each build is hermetic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from orgctx import compact, extract, ingest, llm
from orgctx.db import Graph
from orgctx.gitutil import list_commits
from orgctx.policy import load_policy

# Repo root = two levels up from this file (eval/harness.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[1]
DUMMY_SRC = REPO_ROOT / "dummy-org"

_IGNORE = shutil.ignore_patterns(".git", ".orgcontext", "__pycache__", "*.pyc")


def _copy_dummy(dest: Path) -> Path:
    """Copy the clean dummy-org source into `dest/dummy-org`; return that path."""
    org = dest / "dummy-org"
    shutil.copytree(DUMMY_SRC, org, ignore=_IGNORE)
    return org


def _run(script: Path, cwd: Path) -> None:
    subprocess.run(["bash", str(script)], cwd=cwd, check=True, capture_output=True, text=True)


def build_graph(workdir: Path, *, break_change: bool) -> tuple[Graph, Path, Path]:
    """Seed a fresh dummy org under `workdir` and build its graph end to end.

    Returns (graph, org_path, central_path). The LLM mock is forced on for
    determinism regardless of the caller's environment.
    """
    llm.force_mock = True
    org = _copy_dummy(workdir)
    _run(org / "scripts" / "seed-history.sh", org)
    if break_change:
        _run(org / "scripts" / "scenario-break.sh", org)

    central = workdir / "central"
    graph = Graph(central / "graph.db")
    policy = load_policy(None)

    extract.extract_org(graph, str(org))
    ingest.ingest_range(graph, str(org), "HEAD")
    compact.compact_all(graph, policy, central)
    return graph, org, central


def state_size_series(workdir: Path, *, break_change: bool) -> tuple[list[int], list[int]]:
    """Build the graph commit-by-commit, recording total state_summary tokens after
    each commit — the plateau curve. Also returns the NAIVE (no-compression) series
    for contrast. Uses a separate scratch dir so it never clobbers `build_graph`.
    """
    from eval.metrics import naive_state_size, total_state_size

    llm.force_mock = True
    org = _copy_dummy(workdir)
    _run(org / "scripts" / "seed-history.sh", org)
    if break_change:
        _run(org / "scripts" / "scenario-break.sh", org)

    central = workdir / "central"
    graph = Graph(central / "graph.db")
    policy = load_policy(None)

    # Extract once at HEAD so entities + edges (needed for the promotion rules)
    # exist; then ingest one commit at a time, compacting and measuring after each.
    extract.extract_org(graph, str(org))
    compacted, naive = [], []
    for commit in list_commits(str(org), "HEAD"):
        ingest._process_commit(graph, str(org), commit)
        compact.compact_all(graph, policy, central)
        compacted.append(total_state_size(graph))
        naive.append(naive_state_size(graph))
    return compacted, naive
