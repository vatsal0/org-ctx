"""`python -m eval.run_eval` — build the dummy graph, run all metrics, plot the plateau.

This is the single command that answers "is the tool working on the ground-truth
dummies?" It is Arguments-driven (per CLAUDE.md) and pretty-prints its config, then:
  1. builds the graph (with the breaking change applied),
  2. reports edge recall (all-source + heuristic-only),
  3. reports impact precision/recall + the noise (silent-service) check,
  4. computes the state-size-over-commits series and writes the plateau plot.

Everything runs under the deterministic LLM mock, so the numbers are reproducible.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

# Allow `python -m eval.run_eval` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import metrics
from eval.harness import build_graph, state_size_series
from orgctx.impact import compute_impact
from orgctx.policy import load_policy


@dataclass
class EvalArgs:
    """Config for an eval run. `plot` toggles writing the matplotlib figure;
    `out` is where artifacts land."""

    out: str = "eval/out"
    plot: bool = True
    verbose: bool = False


def _print_config(args: EvalArgs) -> None:
    print("[eval] config:")
    for f in fields(args):
        print(f"    {f.name} = {getattr(args, f.name)!r}")


def _hr(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main() -> int:
    args = EvalArgs()
    _print_config(args)

    repo_root = Path(__file__).resolve().parents[1]
    expected_edges = yaml.safe_load((repo_root / "dummy-org" / "expected_edges.yaml").read_text())["edges"]
    labels = yaml.safe_load((repo_root / "eval" / "labels.yaml").read_text())["break_diff"]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        graph, org, central = build_graph(Path(tmp), break_change=True)

        # --- Edge recall. -------------------------------------------------------
        _hr("EDGE RECALL (vs dummy-org/expected_edges.yaml)")
        all_src = metrics.edge_recall(graph, expected_edges)
        heur = metrics.edge_recall(graph, expected_edges, heuristic_only=True)
        print(f"  all-source     : {all_src.recovered}/{all_src.total} = {all_src.value:.2f}")
        print(f"  heuristic-only : {heur.recovered}/{heur.total} = {heur.value:.2f}")

        # --- Impact precision / recall + noise. --------------------------------
        _hr("IMPACT PRECISION / RECALL + NOISE (break diff)")
        policy = load_policy(None)
        flagged, flag_counts = set(), {}
        for svc in labels["all_services"]:
            upstream, downstream = compute_impact(graph, str(org), svc, labels["ref"], policy.impact_hops)
            n = len(upstream) + sum(len(v) for v in downstream.values())
            flag_counts[svc] = n
            if upstream:
                flagged.add(svc)
        pr = metrics.impact_precision_recall(flagged, set(labels["upstream_affected"]))
        print(f"  upstream-affected flagged: {sorted(flagged)}")
        print(f"  precision={pr.precision:.2f}  recall={pr.recall:.2f}")
        print(f"  flags per service: {flag_counts}")
        print(f"  noise_ratio (avg flags/service): {metrics.noise_ratio(flag_counts):.2f}")
        for svc in labels["must_be_silent"]:
            status = "OK" if flag_counts.get(svc, 0) == 0 else "FAIL"
            print(f"  silence check [{svc}]: {flag_counts.get(svc, 0)} flags -> {status}")

    # --- State size over commits (plateau). ------------------------------------
    _hr("STATE SIZE OVER COMMITS (must plateau)")
    with tempfile.TemporaryDirectory() as tmp:
        compacted, naive = state_size_series(Path(tmp), break_change=True)
    print(f"  compacted series: {compacted}")
    print(f"  naive series    : {naive}")
    # Plateau test: measure per-commit growth. The signal is that INTERNAL churn
    # commits add ZERO tokens to the compacted state (they are dropped), whereas the
    # naive "one line per event" baseline grows on essentially every commit. So we
    # count zero-growth commits in each series: a healthy policy shows a long flat
    # run (the churn stretch) that the naive curve never has. A real breaking change
    # legitimately adds tokens — that is signal, not leakage.
    comp_deltas = [b - a for a, b in zip(compacted, compacted[1:])]
    naive_deltas = [b - a for a, b in zip(naive, naive[1:])]
    comp_flat = sum(1 for d in comp_deltas if d == 0)
    naive_flat = sum(1 for d in naive_deltas if d == 0)
    print(f"  zero-growth commits: compacted={comp_flat}  naive={naive_flat}  (of {len(comp_deltas)})")
    longest = _longest_flat_run(compacted)
    print(f"  longest flat run (compacted): {longest} commits")
    holds = longest >= 10 and naive_flat == 0
    print(f"  -> compression {'HOLDS (churn plateaus)' if holds else 'may be LEAKING'}: "
          f"a {longest}-commit flat run in compacted vs a strictly-growing naive curve")

    if args.plot:
        _plot(compacted, naive, out_dir / "state_size.png")
        print(f"\n  wrote plateau plot -> {out_dir / 'state_size.png'}")

    return 0


def _longest_flat_run(series: list[int]) -> int:
    """Length of the longest run of consecutive equal values — the churn plateau."""
    best = run = 1 if series else 0
    for a, b in zip(series, series[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best


def _plot(compacted: list[int], naive: list[int], path: Path) -> None:
    """Plot compacted vs naive state size over commits — the load-bearing figure."""
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    x = list(range(1, len(compacted) + 1))
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x, naive, "--", color="tab:red", label="naive (one line per event)")
    ax.plot(x, compacted, "-o", color="tab:blue", label="compacted (policy)")
    ax.set_xlabel("commit #")
    ax.set_ylabel("total state_summary tokens")
    ax.set_title("State size over commits — compression must plateau")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)


if __name__ == "__main__":
    raise SystemExit(main())
