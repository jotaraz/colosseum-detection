from __future__ import annotations

"""What a judge sweep actually cost, read correctly off its verdict files.

    python -m experiments.agent2.sweep_cost 'experiments/agent1/outputs/v15/inf_ask_r1*.deception_jv10_*.json'
    python -m experiments.agent2.sweep_cost <glob> --by-arm

**Why this module exists rather than a one-line sum.** ``lie_over_agent1`` records
``usage_totals`` = the caller's snapshot at the moment each file was written, and that caller
accumulates for the whole life of its process. So the files of a single pass carry a running
total, not per-file costs, and summing them multiplies the real spend by roughly the number of
files — it once reported $576 and 29,618 calls for a sweep that made ~800 calls costing $17.

Two readings, and the module picks per file:

* ``usage_this_file`` (written since 2026-08-26) is the per-file delta. Sum it.
* ``usage_totals`` alone means an older file. The pass total is then the **maximum** snapshot
  in that pass, since the counter only grows. A pass is identified by (replicate, repaired):
  each is a separate process invocation with its own fresh counter.

The max-per-pass reading is a lower bound when a later ``--repair`` pass overwrote the file
that happened to hold the pass maximum. That is reported rather than hidden.
"""

import argparse
import glob as globmod
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

FIELDS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd")


def _blank() -> Dict[str, float]:
    return {f: 0.0 for f in FIELDS}


def cost_of(paths: List[str]) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """(totals, provenance) for a set of verdict files."""
    exact = _blank()
    n_exact = 0
    # pass key -> the largest snapshot seen in it
    passes: Dict[Tuple[Any, Any], Dict[str, float]] = defaultdict(_blank)
    n_legacy = 0

    for p in paths:
        try:
            j = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        per_file = j.get("usage_this_file")
        if per_file:
            for f in FIELDS:
                exact[f] += float(per_file.get(f) or 0.0)
            n_exact += 1
            continue
        u = j.get("usage_totals") or {}
        if not u:
            continue                      # transcoded verdicts made no calls at all
        n_legacy += 1
        key = (j.get("replicate"), bool(j.get("repaired")))
        if float(u.get("cost_usd") or 0.0) > passes[key]["cost_usd"]:
            passes[key] = {f: float(u.get(f) or 0.0) for f in FIELDS}

    totals = dict(exact)
    for snap in passes.values():
        for f in FIELDS:
            totals[f] += snap[f]
    return totals, {"files": len(paths), "exact": n_exact, "legacy": n_legacy,
                    "passes": len(passes)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", help="glob over verdict files (quote it)")
    ap.add_argument("--by-arm", action="store_true",
                    help="split by ask arm, parsed from the filename")
    args = ap.parse_args()
    paths = sorted(globmod.glob(args.pattern))
    if not paths:
        raise SystemExit(f"no files matched {args.pattern!r}")

    groups: Dict[str, List[str]] = {"ALL": paths}
    if args.by_arm:
        groups = defaultdict(list)
        for p in paths:
            m = re.match(r"inf_(ask[A-Za-z0-9_]*?)_(?:kimi|glm|deepseek)", Path(p).name)
            groups[m.group(1) if m else "?"].append(p)

    for name, ps in sorted(groups.items()):
        t, prov = cost_of(ps)
        note = (f"{prov['exact']} exact" if prov["exact"] else "") + \
               (f", {prov['legacy']} legacy over {prov['passes']} pass(es)" if prov["legacy"] else "")
        print(f"{name:14s} {int(t['calls']):6d} calls  ${t['cost_usd']:8.2f}  "
              f"{int(t['total_tokens']):10d} tok   [{prov['files']} files: {note}]")
    if any(not json.loads(Path(p).read_text()).get("usage_this_file")
           for p in paths[:1]):
        print("\nNOTE: legacy files priced by max-per-pass — a lower bound if a --repair pass "
              "overwrote the file holding a pass maximum.")


if __name__ == "__main__":
    main()
