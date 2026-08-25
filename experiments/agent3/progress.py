from __future__ import annotations

"""One line per candidate of an agent3 run, with the running cost.

    python -m experiments.agent3.progress experiments/agent3/outputs/run02 [--since N]

Cost = the judge (and prompter, where the caller tracks it) usage from the step records, plus
each rollout's own ``cost`` field. Step schema 2: a step is a batch, so a step prints one header
line and one line per tier.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def step_cost(step: Dict[str, Any]) -> float:
    total = sum(float(v.get("cost_usd") or 0) for v in (step.get("usage") or {}).values())
    total += float(((step.get("prompter") or {}).get("usage") or {}).get("cost") or 0.0)
    for a in step.get("attempts") or []:
        for run in a.get("runs") or []:
            try:
                total += float(json.loads(Path(run["run_path"]).read_text()).get("cost") or 0)
            except Exception:  # noqa: BLE001
                pass
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--since", type=int, default=0, help="only print steps after N")
    args = ap.parse_args()
    out = Path(args.out_dir)
    cumulative = 0.0
    for f in sorted((out / "steps").glob("step_*.json")):
        s = json.loads(f.read_text())
        cost = step_cost(s)
        cumulative += cost
        opt = int(s.get("opt_step") or 0)
        if opt <= args.since:
            continue
        prompter = s.get("prompter") or {}
        print(f"step {opt}: {s.get('duration_s', 0) / 60:.0f} min | "
              f"{prompter.get('n_tool_calls', 0)} tool calls | "
              f"step ${cost:.2f}, cumulative ${cumulative:.2f}"
              + (f" | {s['failure'][:120]}" if not s.get("ran") else ""))
        for a in s.get("attempts") or []:
            ra = str((a.get("candidate") or {}).get("reward_agent") or "Priya")
            counts: Dict[str, int] = {}
            turns, cached = [], []
            for run in a.get("runs") or []:
                summ = (run.get("summary") or {}).get(ra, {}) or {}
                # jv7 records categories; jv8 records lie turns and the weight they carried.
                for cat, n in (summ.get("categories") or {}).items():
                    counts[cat] = counts.get(cat, 0) + n
                if summ.get("lie_turns"):
                    counts["lie-turns"] = counts.get("lie-turns", 0) + summ["lie_turns"]
                for d, n in (summ.get("deception") or {}).items():
                    counts[d] = counts.get(d, 0) + n
                turns.append(run.get("n_turns"))
                try:
                    tok = json.loads(Path(run["run_path"]).read_text()).get("tokens") or {}
                    if tok.get("prompt"):
                        cached.append(round(100 * (tok.get("cached") or 0) / tok["prompt"]))
                except Exception:  # noqa: BLE001
                    pass
            per_run = [r["reward"] for r in (a.get("reward_detail") or {}).get("per_seed", [])]
            status = (f"reward {a['reward']:.2f} per-run {per_run} turns {turns}"
                      if a.get("ran") else f"DID NOT RUN: {str(a.get('failure'))[:100]}")
            print(f"    {str(a.get('tier') or '?'):<13} {status} | {ra} {counts}"
                  + (f" | cache {cached}%" if cached else ""))


if __name__ == "__main__":
    main()
