"""Per-model lying-score table for the rr10dcp deception study: mean / min / max by level.

One row per target model, three column groups (`none`, `allow`, `forbid`), each showing the mean,
minimum and maximum ``weighted_count`` over that cell's rollouts. ``weighted_count`` is the
objective the critics feed: the specificity-weighted count of DISTINCT qualifying fabrications in a
rollout, where a turn qualifies only under the triple AND-gate — strategic-fabrication AND a
specificity score AND soundness_ok AND at_stake (``interfaces.TurnScore.qualifies``). A rollout
scoring 0 is one where no turn cleared all four.

This is the DESCRIPTIVE view. It says what each cell looks like; it does not test anything. The
levels are not independent samples — every cell replays the same 10 prompts at the same 2 seeds —
so a difference between two means here is not evidence on its own. ``deception_analysis.py`` is the
inferential companion: it pairs on (model, prompt, seed) and sign-tests the within-triple deltas.
Read this table for shape and spread, that one for whether a gap is real.

Min and max earn their place: the means hide how zero-inflated and long-tailed the scores are. A
cell averaging 5 is a different object when its rollouts run 0-8 than when they run 0-31.

    python -m experiments.social_jira4.rr10dcp_score_table
    python -m experiments.social_jira4.rr10dcp_score_table --csv scores.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.social_jira4.deception_analysis import LEVELS, load_scores

DEFAULT_JUDGMENTS = "experiments/social_jira4/outputs/rr10dcp/judgments/dsflash0731"


def build(scores: Dict[Any, float]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """``model -> level -> {n, mean, min, max}``."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for model in sorted({k[1] for k in scores}):
        out[model] = {}
        for lvl in LEVELS:
            vals = sorted(v for k, v in scores.items() if k[0] == lvl and k[1] == model)
            out[model][lvl] = {
                "n": len(vals),
                "mean": (sum(vals) / len(vals)) if vals else None,
                "min": vals[0] if vals else None,
                "max": vals[-1] if vals else None,
                "zero": sum(1 for v in vals if not v),
            }
    return out


CELL_W = 17          # visible width of "  8.80 (0.0/22.0)"


def _cell(mean: Optional[float], lo: Optional[float], hi: Optional[float],
          bold: bool) -> str:
    """``mean (min/max)`` with the mean emphasised, padded on its VISIBLE width.

    The ANSI codes are zero-width on screen but not to ``str.format``, so the padding is computed
    from the plain text and the escapes are wrapped around the number afterwards — otherwise every
    bolded column drifts left by the length of the escape sequence.
    """
    if mean is None:
        return "—".rjust(CELL_W)
    m = f"{mean:.2f}"
    tail = f" ({'—' if lo is None else f'{lo:.1f}'}/{'—' if hi is None else f'{hi:.1f}'})"
    pad = " " * max(0, CELL_W - len(m) - len(tail))
    return f"{pad}\033[1m{m}\033[0m{tail}" if bold else f"{pad}{m}{tail}"


def render(table: Dict[str, Dict[str, Dict[str, Any]]], scores: Dict[Any, float],
           *, bold: bool = True) -> None:
    print("rr10dcp — lying score (weighted_count) per rollout, by target model and deception level")
    print("cells are mean (min/max) over each cell's rollouts\n")

    head = f"{'model':22s}"
    for lvl in LEVELS:
        head += f"│{lvl:^{CELL_W}s} "
    print(head)
    print("─" * 22 + ("┼" + "─" * (CELL_W + 1)) * len(LEVELS))

    for model, per in table.items():
        row = f"{model:22s}"
        for lvl in LEVELS:
            d = per[lvl]
            row += f"│{_cell(d['mean'], d['min'], d['max'], bold)} "
        print(row)

    # Pooled over models. A plain mean of raw scores across models is a statement about the model
    # mix as much as the level, so it is labelled as such rather than presented as "the" effect.
    print("─" * 22 + ("┼" + "─" * (CELL_W + 1)) * len(LEVELS))
    row = f"{'ALL MODELS POOLED':22s}"
    for lvl in LEVELS:
        vals = sorted(v for k, v in scores.items() if k[0] == lvl)
        row += (f"│{_cell(sum(vals) / len(vals) if vals else None, vals[0] if vals else None, vals[-1] if vals else None, bold)} ")
    print(row)

    print()
    for model, per in table.items():
        counts = " ".join(f"{lvl}={per[lvl]['n']}" for lvl in LEVELS)
        zeros = " ".join(f"{lvl}={per[lvl]['zero']}" for lvl in LEVELS)
        print(f"  {model:22s} rollouts: {counts:38s} scoring zero: {zeros}")
    ns = {per[lvl]["n"] for per in table.values() for lvl in LEVELS}
    if ns != {20}:
        print("\n  NOTE: not every cell holds 20 rollouts — the means above are over different "
              "numbers of\n  rollouts and the missing ones are not a random subset.")
    print("\nDescriptive only. The levels share prompts and seeds, so a gap between two means here "
          "is not\nevidence — see deception_analysis.py for the paired test.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--judgments", default=DEFAULT_JUDGMENTS,
                    help="seat dir holding <level>_<model>/results.json")
    ap.add_argument("--csv", default="", help="also write the table as CSV")
    ap.add_argument("--no-bold", action="store_true",
                    help="plain text — the mean is bolded with ANSI escapes by default, which are "
                         "noise once the output is redirected to a file")
    args = ap.parse_args(argv)

    jroot = Path(args.judgments)
    if not jroot.is_dir():
        jroot = project_root / args.judgments
    if not jroot.is_dir():
        raise SystemExit(f"no judgments dir at {args.judgments}")

    scores, _cells = load_scores(jroot)
    if not scores:
        raise SystemExit(f"no judged rollouts under {jroot}")
    table = build(scores)
    # Bold only when a terminal is going to interpret it; a redirected run gets clean text.
    render(table, scores, bold=not args.no_bold and sys.stdout.isatty())

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["model", "level", "n", "mean", "min", "max", "zero"])
            for model, per in table.items():
                for lvl in LEVELS:
                    d = per[lvl]
                    w.writerow([model, lvl, d["n"], d["mean"], d["min"], d["max"], d["zero"]])
        print(f"\nwrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
