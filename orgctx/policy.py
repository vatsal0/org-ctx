"""Loader for policy.yaml.

A tiny typed wrapper so the rest of the code reads `policy.token_cap` instead of
dict-indexing a raw YAML blob (and so a typo in a key name fails at load, not deep
inside compact). We resolve the default policy to the packaged orgctx/policy.yaml
via importlib.resources so it works from an installed wheel as well as a source
checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .models import ChangeKind


@dataclass(frozen=True)
class Policy:
    """The compaction + impact knobs, parsed from policy.yaml."""

    promote_kinds: list[ChangeKind]
    promote_added_if_inbound: bool
    fold_kinds: list[ChangeKind]
    drop_kinds: list[ChangeKind]
    require_no_inbound_to_drop: bool
    token_cap: int
    keep_on_overflow: list[str]
    impact_hops: int


def load_policy(path: str | Path | None = None) -> Policy:
    """Load a Policy from `path`, or from the packaged default when path is None."""
    if path is None:
        text = resources.files("orgctx").joinpath("policy.yaml").read_text()
    else:
        text = Path(path).read_text()
    raw = yaml.safe_load(text)
    return Policy(
        promote_kinds=[ChangeKind(k) for k in raw["promote_kinds"]],
        promote_added_if_inbound=bool(raw["promote_added_if_inbound"]),
        fold_kinds=[ChangeKind(k) for k in raw["fold_kinds"]],
        drop_kinds=[ChangeKind(k) for k in raw["drop_kinds"]],
        require_no_inbound_to_drop=bool(raw["require_no_inbound_to_drop"]),
        token_cap=int(raw["token_cap"]),
        keep_on_overflow=list(raw["keep_on_overflow"]),
        impact_hops=int(raw["impact_hops"]),
    )
