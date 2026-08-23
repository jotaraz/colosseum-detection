from __future__ import annotations

"""One line per completed attempt of an agent3 run, with the running cost.

    python -m experiments.agent3.progress experiments/agent3/outputs/run01 [--since N]

Cost = prompter + judge usage from the step records, plus each rollout's own ``cost`` field.
"""

import argparse
import json
from pathlib import Path


def step_cost(step: dict) -> float:
    total = sum(float(v.get("cost_usd") or 0) for v in (step.get("usage") or {}).values())
    for sd in step.get("seeds") or []:
        try:
            total += float(json.loads(Path(sd["run_path"]).read_text()).get("cost") or 0)
        except Exception:  # noqa: BLE001
            pass
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--since", type=int, default=0, help="only print attempts with step > N")
    args = ap.parse_args()
    out = Path(args.out_dir)
    cumulative = 0.0
    for f in sorted((out / "steps").glob("step_*.json")):
        s = json.loads(f.read_text())
        cumulative += step_cost(s)
        if s["step"] <= args.since:
            continue
        ra = str((s.get("candidate") or {}).get("reward_agent") or "Priya")
        priya = {}
        fabs = {}
        for sd in s.get("seeds") or []:
            for agent, summ in (sd.get("summary") or {}).items():
                tgt = priya if agent == ra else fabs.setdefault(agent, {})
                for cat, n in summ["categories"].items():
                    if agent == ra or "fab" in cat:
                        tgt[cat] = tgt.get(cat, 0) + n
        fabs = {a: v for a, v in fabs.items() if v}
        per_seed = [r["reward"] for r in (s.get("reward_detail") or {}).get("per_seed", [])]
        turns = [sd.get("n_turns") for sd in s.get("seeds") or []]
        status = f"reward {s['reward']:.2f} per-seed {per_seed} turns {turns}" if s["ran"] else f"DID NOT RUN: {s['failure'][:120]}"
        print(f"step {s['step']} (opt {s.get('opt_step')}): {status} | {ra} {priya}"
              f"{' | other fabs ' + str(fabs) if fabs else ''} | {s['duration_s']/60:.0f} min"
              f" | step ${step_cost(s):.2f}, cumulative ${cumulative:.2f}")


if __name__ == "__main__":
    main()
