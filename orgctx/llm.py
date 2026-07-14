"""The single, mockable LLM boundary.

Every LLM call in the whole tool goes through this module, so tests and the eval
harness can force a deterministic mock and never touch the network. There are two
implementations behind one function surface:

  - **Mock (default).** A rule-based classifier/summarizer. It is deliberately NOT
    a canned-response lookup keyed on exact input; instead it derives its answer
    from the *structure* of the change (did a field disappear? did a param get
    added?). That makes it robust to superficial input changes (line numbers,
    whitespace) so the golden scenarios stay stable as the dummy-org evolves.

  - **Real (opt-in).** Uses the `anthropic` SDK. Activated ONLY when the
    environment variable ANTHROPIC_API_KEY is set AND the caller has not forced
    the mock. The real path exists so the demo can produce genuine prose summaries;
    the tests never rely on it.

The public surface is two functions — `summarize_change` and `recompact` — plus a
module-level toggle `force_mock` the test fixture flips on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .models import ChangeKind


# The test fixture sets this True to guarantee determinism regardless of whether a
# key happens to be present in the developer's shell. Production/demo leaves it
# False and lets key-presence decide.
force_mock: bool = False

# Model ids for the real path (see plan.md). Opus for the (rarer) classification
# judgment; Haiku for cheap bulk summaries. Only used when the real path is active.
CLASSIFY_MODEL = "claude-opus-4-8"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class ChangeVerdict:
    """What the LLM boundary returns for one touched entity.

    `change_kind` may *refine* the structural guess ingest already made (e.g.
    promote a bare MODIFIED to a BEHAVIOR_NOTE when the semantics changed).
    `breaking` is the model's opinion; ingest still applies the deterministic
    "breaking requires an inbound consumer" guard on top, so a hallucinated
    breaking flag on an unconsumed entity cannot create noise.
    """

    summary: str
    change_kind: ChangeKind
    breaking: bool


def _use_real() -> bool:
    """Real path iff a key is present and the mock has not been forced."""
    return (not force_mock) and bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- #
# Public surface.
# --------------------------------------------------------------------------- #
def summarize_change(
    *,
    entity_id: str,
    kind: str,
    old_signature: str | None,
    new_signature: str | None,
    structural_kind: ChangeKind,
) -> ChangeVerdict:
    """Produce a one-line summary + (possibly refined) change_kind + breaking flag.

    `structural_kind` is ingest's own diff-of-entity-tables verdict (ADDED /
    REMOVED / SIGNATURE_CHANGE / MODIFIED). The LLM's job is to (a) write a
    human-readable one-liner and (b) optionally refine the kind (only the
    MODIFIED -> BEHAVIOR_NOTE promotion is meaningful; structural ADD/REMOVE/SIG
    are already certain). We keep the contract identical across mock and real so
    callers never branch on which ran.
    """
    if _use_real():
        return _real_summarize(entity_id, kind, old_signature, new_signature, structural_kind)
    return _mock_summarize(entity_id, kind, old_signature, new_signature, structural_kind)


def recompact(*, entity_id: str, lines: list[str], token_cap: int) -> str:
    """Compress an over-cap state_summary down toward the token cap.

    The caller (compact.py) has already guaranteed the "must-keep" lines (origin,
    latest, unresolved breaking) sit at the front of `lines`; our job is only to
    shorten the *tail*. The mock does this deterministically by truncation; the
    real path asks the model to rewrite more gracefully.
    """
    if _use_real():
        return _real_recompact(entity_id, lines, token_cap)
    return _mock_recompact(lines, token_cap)


# --------------------------------------------------------------------------- #
# Mock implementation — deterministic, rule-based, network-free.
# --------------------------------------------------------------------------- #
def _mock_summarize(
    entity_id: str,
    kind: str,
    old_signature: str | None,
    new_signature: str | None,
    structural_kind: ChangeKind,
) -> ChangeVerdict:
    """Rule-based summary + classification.

    The summary is templated from the structural facts so it reads naturally and
    stays stable across runs. Classification FAITHFULLY passes ingest's structural
    verdict through — the mock does not invent a BEHAVIOR_NOTE (that semantic
    judgment is the real LLM's escape hatch, per plan.md §11). This faithfulness is
    what keeps internal churn classified as INTERNAL, so the compression policy can
    drop it. `breaking` is a hint set for the two contract-breaking kinds; ingest's
    inbound-edge guard has the final say on whether it actually surfaces.
    """
    from .ids import parse_id

    _, _, local = parse_id(entity_id)  # human-facing tail of the id

    if structural_kind is ChangeKind.ADDED:
        return ChangeVerdict(f"Added {kind} `{local}` ({new_signature}).", ChangeKind.ADDED, breaking=False)

    if structural_kind is ChangeKind.REMOVED:
        return ChangeVerdict(f"Removed {kind} `{local}` (was {old_signature}).", ChangeKind.REMOVED, breaking=True)

    if structural_kind is ChangeKind.SIGNATURE_CHANGE:
        return ChangeVerdict(
            f"Signature of `{local}` changed: {old_signature} -> {new_signature}.",
            ChangeKind.SIGNATURE_CHANGE, breaking=True,
        )

    if structural_kind is ChangeKind.INTERNAL:
        return ChangeVerdict(f"Internal change to `{local}` (contract unchanged).", ChangeKind.INTERNAL, breaking=False)

    # MODIFIED: contract touched but shape preserved (e.g. currency ISO-4217 validator).
    return ChangeVerdict(f"Modified `{local}`: {old_signature} -> {new_signature}.", ChangeKind.MODIFIED, breaking=False)


def _mock_recompact(lines: list[str], token_cap: int) -> str:
    """Truncate to fit the cap while keeping earliest (must-keep) lines.

    compact.py front-loads the lines that must survive, so a simple greedy
    accumulate-until-cap over the ordered list preserves exactly them and drops
    the tail. Token counting is delegated to the shared `count_tokens` proxy.
    """
    kept: list[str] = []
    running = 0
    for line in lines:
        cost = count_tokens(line)
        if running + cost > token_cap and kept:
            break
        kept.append(line)
        running += cost
    return "\n".join(kept)


# --------------------------------------------------------------------------- #
# Real implementation — anthropic SDK. Imported lazily so the package works with
# no key and no SDK installed (the mock path never touches these).
# --------------------------------------------------------------------------- #
def _client():
    import anthropic  # lazy: only needed on the real path

    return anthropic.Anthropic()


def _real_summarize(entity_id, kind, old_signature, new_signature, structural_kind) -> ChangeVerdict:
    prompt = (
        "You are labeling a change to a software interface for a change-tracking "
        "system. Return a single JSON object with keys: summary (a one-line, "
        "human-readable description), change_kind (one of: modified, "
        "signature_change, behavior_note), breaking (boolean).\n\n"
        f"entity: {entity_id}\nkind: {kind}\n"
        f"old_signature: {old_signature}\nnew_signature: {new_signature}\n"
        f"structural_verdict: {structural_kind.value}\n"
    )
    msg = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    import json

    text = msg.content[0].text
    data = json.loads(text)
    try:
        refined = ChangeKind(data.get("change_kind", structural_kind.value))
    except ValueError:
        refined = structural_kind
    return ChangeVerdict(
        summary=data["summary"],
        change_kind=refined,
        breaking=bool(data.get("breaking", structural_kind in (ChangeKind.SIGNATURE_CHANGE, ChangeKind.REMOVED))),
    )


def _real_recompact(entity_id, lines, token_cap) -> str:
    prompt = (
        "Compress the following change-history lines to roughly "
        f"{token_cap} tokens. PRESERVE the first lines verbatim (they are the "
        "origin, the latest change, and any unresolved breaking change). Summarize "
        "or drop only the later lines. Return only the compressed lines.\n\n"
        + "\n".join(lines)
    )
    msg = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


# --------------------------------------------------------------------------- #
# Token counting proxy. Shared by compact.py and the eval's state-size metric so
# both agree on "size". tiktoken is NOT Claude's tokenizer — we only need a stable
# deterministic count; the eval gates on the plateau *shape*, not absolute tokens.
# We cache the encoder and fall back to a whitespace-ish estimate if tiktoken is
# unavailable, so the package never hard-fails on a missing optional dep.
# --------------------------------------------------------------------------- #
_encoder = None


def count_tokens(text: str) -> int:
    global _encoder
    if not text:
        return 0
    if _encoder is None:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = False  # sentinel: tiktoken unavailable, use the fallback
    if _encoder is False:
        # Rough proxy: ~1 token per 4 characters, min 1 for non-empty text.
        return max(1, len(text) // 4)
    return len(_encoder.encode(text))
