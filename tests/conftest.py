"""Shared pytest fixtures: deterministically-built dummy-org graphs.

Both graphs are built once per session (building runs git + shell scripts, so it's
worth caching) via the shared eval harness under the LLM mock. `broken_graph`
includes the scenario-break commit; `seeded_graph` stops at the seeded history.

We make the repo root importable so tests can `from eval.harness import ...`
(orgctx itself is installed editable, so it imports directly).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.harness import build_graph  # noqa: E402 (after sys.path tweak)


@pytest.fixture(scope="session")
def broken_graph(tmp_path_factory):
    """(graph, org_path, central_path) with the breaking rename applied."""
    workdir = tmp_path_factory.mktemp("broken")
    return build_graph(workdir, break_change=True)


@pytest.fixture(scope="session")
def seeded_graph(tmp_path_factory):
    """(graph, org_path, central_path) at the seeded history (no break)."""
    workdir = tmp_path_factory.mktemp("seeded")
    return build_graph(workdir, break_change=False)
