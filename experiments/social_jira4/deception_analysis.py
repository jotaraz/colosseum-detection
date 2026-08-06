"""The deception axis, read off a judged rr10dcp tree: none vs allow vs forbid, per model.

``rejudge.py --summarize`` prints a cell table — one mean per (level, model). That table cannot
answer the study's question, because the cells are not independent samples of anything: every cell
replays THE SAME 10 prompts at THE SAME 2 seeds. Comparing cell means throws that away and pays for
it in variance. The base run already showed one prompt swinging 24 -> 6 between seeds on gemma; a
difference of cell means has to clear that noise before it means anything, and with n=20 it mostly
cannot.

So this reads the axis PAIRED. The unit is one ``(model, prompt, seed)`` triple — a fixed scenario
under a fixed model — and the comparison is that triple's score at two levels. Prompt identity and
seed identity cancel exactly, which is the entire reason the study was built by replaying a fixed
prompt set rather than sampling a new one per condition.

Three things are reported per contrast, and all three matter:

  * **mean paired delta** — the average within-triple change. The headline.
  * **the sign split** (up / down / unchanged) with a two-sided sign test. A mean delta of +1.5 that
    comes from 3 triples moving a lot is a different claim from one where 15 of 20 move a little,
    and only the second is evidence of an effect rather than of an outlier.
  * **how many triples are complete**. A contrast is computed ONLY over triples present at both
    levels. Cells still generating would otherwise produce a mean over whichever rollouts happened
    to finish first, which is not a random subset — the fast prompts finish first.

The sign test is deliberately the weakest defensible instrument: it assumes nothing about the score
distribution (``weighted_count`` is a bounded, zero-inflated, discrete sum — nothing here is normal,
and a t-test on 20 paired counts would be assuming its way to a p-value). Ties are dropped, which is
the conservative convention and matters a lot here: most turns qualify at neither level, so ties are
the majority outcome and counting them would manufacture significance.

    python -m experiments.social_jira4.deception_analysis \
        --judgments experiments/social_jira4/outputs/rr10dcp/judgments/dsflash0731
    python -m experiments.social_jira4.deception_analysis --judgments ... --json report.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

LEVELS = ("none", "allow", "forbid")
# The contrasts worth naming. allow-vs-forbid is included even though it is implied by the two
# against none: it is the widest contrast in the design (the two arms differ only in the sanction),
# so it is the one with the most power to show anything at this sample size.
CONTRASTS = (("allow", "none"), ("forbid", "none"), ("allow", "forbid"))


def _binom_sign_p(up: int, down: int) -> Optional[float]:
    """Two-sided exact sign test on the non-tied pairs (p=0.5 under the null)."""
    n = up + down
    if n == 0:
        return None
    k = min(up, down)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def load_scores(jroot: Path) -> Tuple[Dict[Tuple[str, str, int, int], float], Dict[str, Any]]:
    """``(level, model, step, seed) -> objective`` plus per-cell coverage.

    Cell directory names are ``<level>_<model>``; the model may contain underscores, the level may
    not, so the split is on the FIRST underscore only.
    """
    scores: Dict[Tuple[str, str, int, int], float] = {}
    cells: Dict[str, Any] = {}
    for rp in sorted(jroot.glob("*/results.json")):
        cell = rp.parent.name
        level, _, model = cell.partition("_")
        if level not in LEVELS:
            continue
        n = 0
        for rec in json.loads(rp.read_text(encoding="utf-8")):
            for s in rec.get("seeds") or []:
                if "objective" not in s:
                    continue
                scores[(level, model, int(rec["step"]), int(s["seed"]))] = float(s["objective"])
                n += 1
        cells[cell] = {"level": level, "model": model, "judged": n}
    return scores, cells


def analyse(scores: Dict[Tuple[str, str, int, int], float]) -> Dict[str, Any]:
    models = sorted({k[1] for k in scores})
    out: Dict[str, Any] = {"models": {}, "pooled": {}}

    for model in models:
        keys = {(k[2], k[3]) for k in scores if k[1] == model}
        per_level = {}
        for lvl in LEVELS:
            vals = [scores[(lvl, model, s, d)] for (s, d) in sorted(keys)
                    if (lvl, model, s, d) in scores]
            by_seed = {}
            for seed in sorted({d for (_, d) in keys}):
                sv = [scores[(lvl, model, s, d)] for (s, d) in sorted(keys)
                      if d == seed and (lvl, model, s, d) in scores]
                if sv:
                    by_seed[seed] = round(_mean(sv), 3)
            per_level[lvl] = {"n": len(vals), "mean": None if not vals else round(_mean(vals), 3),
                              "zero": sum(1 for v in vals if not v),
                              "max": max(vals) if vals else None,
                              "by_seed": by_seed}

        contrasts = {}
        for a, b in CONTRASTS:
            paired = [(scores[(a, model, s, d)], scores[(b, model, s, d)])
                      for (s, d) in sorted(keys)
                      if (a, model, s, d) in scores and (b, model, s, d) in scores]
            deltas = [x - y for x, y in paired]
            up = sum(1 for v in deltas if v > 0)
            down = sum(1 for v in deltas if v < 0)
            contrasts[f"{a}-{b}"] = {
                "pairs": len(paired), "mean_delta": None if not deltas else round(_mean(deltas), 3),
                "up": up, "down": down, "tied": len(deltas) - up - down,
                "sign_p": _binom_sign_p(up, down),
            }
        out["models"][model] = {"levels": per_level, "contrasts": contrasts,
                                "triples": len(keys)}

    # Pooled across models: the same paired unit, with model identity carried by the pairing rather
    # than averaged over. Cell means differ by model by more than any plausible axis effect, so a
    # pooled mean of RAW scores would be a statement about the model mix; a pooled mean of within-
    # triple DELTAS is not.
    for a, b in CONTRASTS:
        deltas = [scores[(a, m, s, d)] - scores[(b, m, s, d)]
                  for (lvl, m, s, d) in scores if lvl == a
                  and (b, m, s, d) in scores]
        up = sum(1 for v in deltas if v > 0)
        down = sum(1 for v in deltas if v < 0)
        out["pooled"][f"{a}-{b}"] = {
            "pairs": len(deltas), "mean_delta": None if not deltas else round(_mean(deltas), 3),
            "up": up, "down": down, "tied": len(deltas) - up - down,
            "sign_p": _binom_sign_p(up, down),
        }
    return out


def _fmt(x: Any, w: int = 6, p: int = 2) -> str:
    return "—".rjust(w) if x is None else f"{x:{w}.{p}f}"


def report(res: Dict[str, Any], cells: Dict[str, Any], *, expected: int) -> None:
    print("LEVELS (mean weighted_count; 'seeds' shows the two seed means separately)\n")
    print(f"{'model':22s} " + "".join(f"{l:>18s}" for l in LEVELS))
    for model, m in sorted(res["models"].items()):
        row = f"{model:22s} "
        for lvl in LEVELS:
            d = m["levels"][lvl]
            row += f"{_fmt(d['mean'],7)} (n={d['n']:>2d})".rjust(18)
        print(row)
    print()
    for model, m in sorted(res["models"].items()):
        parts = []
        for lvl in LEVELS:
            bs = m["levels"][lvl]["by_seed"]
            if bs:
                parts.append(f"{lvl}={'/'.join(f'{v:.1f}' for v in bs.values())}")
        if parts:
            print(f"  seeds  {model:20s} " + "   ".join(parts))

    print("\nPAIRED CONTRASTS (same prompt, same seed, same model — only complete pairs)\n")
    print(f"{'model':22s} {'contrast':16s} {'pairs':>5s} {'mean Δ':>8s} "
          f"{'up':>4s} {'down':>5s} {'tied':>5s} {'sign p':>8s}")
    for model, m in sorted(res["models"].items()):
        for name, c in m["contrasts"].items():
            if not c["pairs"]:
                continue
            p = "—" if c["sign_p"] is None else f"{c['sign_p']:.3f}"
            print(f"{model:22s} {name:16s} {c['pairs']:>5d} {_fmt(c['mean_delta'],8)} "
                  f"{c['up']:>4d} {c['down']:>5d} {c['tied']:>5d} {p:>8s}")
    print(f"\n{'POOLED':22s} {'contrast':16s} {'pairs':>5s} {'mean Δ':>8s} "
          f"{'up':>4s} {'down':>5s} {'tied':>5s} {'sign p':>8s}")
    for name, c in res["pooled"].items():
        if not c["pairs"]:
            continue
        p = "—" if c["sign_p"] is None else f"{c['sign_p']:.3f}"
        print(f"{'  all models':22s} {name:16s} {c['pairs']:>5d} {_fmt(c['mean_delta'],8)} "
              f"{c['up']:>4d} {c['down']:>5d} {c['tied']:>5d} {p:>8s}")

    # Coverage last and unmissable: every number above is conditional on it.
    incomplete = {c: d for c, d in cells.items() if d["judged"] < expected}
    print(f"\nCOVERAGE — {len(cells)} cells, "
          f"{sum(d['judged'] for d in cells.values())}/{expected * len(cells)} rollouts judged")
    if incomplete:
        print("  INCOMPLETE — contrasts above use only complete pairs, but the surviving pairs are "
              "NOT a random subset\n  (fast prompts finish first), so treat every number as "
              "provisional until these fill in:")
        for c, d in sorted(incomplete.items()):
            print(f"    {c:30s} {d['judged']}/{expected}")
    else:
        print("  all cells complete")
    missing = [l for l in LEVELS if not any(d["level"] == l for d in cells.values())]
    if missing:
        print(f"  NO CELLS AT ALL for level(s): {', '.join(missing)} — the contrasts involving "
              f"them are absent above, not zero.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--judgments", required=True,
                    help="a seat dir holding <level>_<model>/results.json, e.g. "
                         "outputs/rr10dcp/judgments/dsflash0731")
    ap.add_argument("--expected", type=int, default=20,
                    help="rollouts per complete cell (10 prompts x 2 seeds)")
    ap.add_argument("--json", default="", help="also write the full result here")
    args = ap.parse_args(argv)

    jroot = Path(args.judgments)
    if not jroot.is_dir():
        jroot = project_root / args.judgments
    if not jroot.is_dir():
        raise SystemExit(f"no judgments dir at {args.judgments}")

    scores, cells = load_scores(jroot)
    if not scores:
        raise SystemExit(f"no judged rollouts under {jroot}")
    res = analyse(scores)
    report(res, cells, expected=args.expected)

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"judgments": str(jroot), "cells": cells, **res}, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
