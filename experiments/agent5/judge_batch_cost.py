"""What a judging batch has cost so far and what it will cost when it finishes.

    .venv/bin/python -m experiments.agent5.judge_batch_cost \
        --runs-file experiments/agent5/outputs/njv3_sol_35runs.txt --judge gpt-5.6-sol --reps "" _r2

Reads the sidecars on disk — no calls. Both stages of a gated batch are priced the same way:
the work is one call per (run, seat) for the pg1 gate and one per emitting seat-step for njv3,
so "done" is counted in calls and the projection is the mean cost of the calls already made
times the calls still owed. The denominator comes from the runs themselves
(``emitting_steps_public``), not from an assumed steps-per-run, because the models differ by
3x in how often they post. ``--key`` also reads the OpenRouter key counter, which is the
number that actually bills: the sidecars only see what the API reported per call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent5 import turn_context5 as tc  # noqa: E402
from experiments.agent5.deception_njv1 import _slug, emitting_steps_public  # noqa: E402
from experiments.agent5.preference_judge import _fixture_for  # noqa: E402


def _cost(u: Dict[str, Any]) -> float:
    return float((u or {}).get("cost_usd") or 0.0)


def gate_stage(dirs: List[Path], judge: str, reps: List[str], seats: int) -> Tuple[int, int, float]:
    """(calls done, calls owed, cost so far) for the pg1 pursuing-goal gate."""
    done, cost = 0, 0.0
    for d in dirs:
        for r in reps:
            f = d / f"run.pursuing_goal_{_slug(judge)}{r}.json"
            if not f.exists():
                continue
            for _, row in (json.loads(f.read_text()).get("seats") or {}).items():
                done += 1
                cost += _cost((row.get("meta") or {}).get("usage"))
    return done, len(dirs) * seats * len(reps), cost


def njv3_stage(dirs: List[Path], judge: str, reps: List[str], seats: Tuple[str, ...]
               ) -> Tuple[int, int, float, int]:
    """(calls done, calls owed, cost so far, errors) for njv3 per-step judging."""
    done, owed, cost, errors = 0, 0, 0.0, 0
    for d in dirs:
        run = json.loads((d / "run.json").read_text())
        _, fx = _fixture_for(run)
        if fx is not None:
            names = tc.Names(run, fx)
            owed += sum(len(emitting_steps_public(run, names, s)) for s in seats) * len(reps)
        for r in reps:
            f = d / f"run.deception_njv3_{_slug(judge)}{r}.json"
            if not f.exists():
                continue
            for _, row in (json.loads(f.read_text()).get("seats") or {}).items():
                for step in row.get("steps") or []:
                    done += 1
                    cost += _cost(step.get("usage"))
                    errors += bool(step.get("judge_error"))
    return done, owed, cost, errors


def line(name: str, done: int, owed: int, cost: float) -> str:
    per = cost / done if done else 0.0
    left = max(0, owed - done) * per
    return (f"{name:5s} {done:4d}/{owed:4d} calls  ${cost:7.2f} so far  "
            f"${per:.3f}/call  → ${cost + left:7.2f} at finish"
            + ("  (done)" if done >= owed else f"  (+${left:.2f} to go)"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-file", required=True)
    ap.add_argument("--judge", default="gpt-5.6-sol")
    ap.add_argument("--reps", nargs="*", default=["", "_r2"])
    ap.add_argument("--seats", nargs="*", default=["Priya", "Nadia"])
    ap.add_argument("--baseline", type=float, default=None,
                    help="key counter before the batch started, to report spend since launch")
    ap.add_argument("--key", action="store_true", help="also read the OpenRouter key counter")
    args = ap.parse_args()
    dirs = [Path(l.strip()) for l in Path(args.runs_file).read_text().splitlines() if l.strip()]
    g_done, g_owed, g_cost = gate_stage(dirs, args.judge, args.reps, len(args.seats))
    n_done, n_owed, n_cost, n_err = njv3_stage(dirs, args.judge, args.reps, tuple(args.seats))
    print(line("gate", g_done, g_owed, g_cost))
    print(line("njv3", n_done, n_owed, n_cost) + (f"  {n_err} errors" if n_err else ""))
    tot = g_cost + n_cost
    proj = (g_cost + (g_owed - g_done) * (g_cost / g_done if g_done else 0)
            + n_cost + (n_owed - n_done) * (n_cost / n_done if n_done else 0))
    print(f"total ${tot:7.2f} so far  → ${proj:7.2f} projected")
    if args.key:
        import urllib.request
        k = [l.strip() for l in (Path(__file__).parent / ".env3").read_text().splitlines()
             if l.strip().startswith("sk-or-")][0]
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {k}"})))["data"]
        spent = d["usage"] - args.baseline if args.baseline is not None else None
        print(f"key   ${d['usage']:.2f} used, ${d['limit_remaining']:.2f} left"
              + (f"  (${spent:.2f} since launch)" if spent is not None else ""))


if __name__ == "__main__":
    main()
