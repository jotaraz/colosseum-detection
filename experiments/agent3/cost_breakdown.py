"""Where a step's money actually goes: prompter / rollouts / judge, split input vs output.

    python -m experiments.agent3.cost_breakdown experiments/agent3/outputs/run02 [...]

Token counts are **recorded**, not estimated: the OpenRouter client asks for usage accounting on
every call, so each rollout record carries `tokens.{prompt,completion,cached}` and its charged
`cost`, the prompter's own usage rides on the step's `prompter.usage`, and the judge caller keeps
a per-step snapshot.

The input/output *split within* a component is derived from list prices, because OpenRouter bills
one total per call and does not itemise it. The derivation is checked against the recorded total
and the residual is printed — a residual that is not near zero means a price below is stale, so
read the split with the same suspicion you would read any reconstruction.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

#: USD per token. From OpenRouter's model endpoints, 2026-08-23.
PRICES = {
    # deepseek-v4-flash-0731 on GMICloud — the target and the judge.
    "target": {"in": 0.0672e-6, "cached": 0.01344e-6, "out": 0.1344e-6},
    # z-ai/glm-5.3 — the prompter. Cache-read price is not published; it is solved for below.
    "prompter": {"in": 1.40e-6, "out": 4.40e-6},
}


def _money(x: float) -> str:
    return f"${x:8.4f}"


def breakdown(out: Path) -> Dict[str, Any]:
    steps = [json.loads(f.read_text(encoding="utf-8"))
             for f in sorted((out / "steps").glob("step_*.json"))]
    steps = [s for s in steps if s.get("ran")]
    if not steps:
        return {}
    acc: Dict[str, Any] = {k: dict(prompt=0, completion=0, cached=0, cost=0.0, calls=0)
                           for k in ("prompter", "rollouts", "judge")}
    n_rollouts = 0
    for s in steps:
        pu = (s.get("prompter") or {}).get("usage") or {}
        acc["prompter"]["prompt"] += pu.get("prompt_tokens", 0)
        acc["prompter"]["completion"] += pu.get("completion_tokens", 0)
        acc["prompter"]["cached"] += pu.get("cached_tokens", 0)
        acc["prompter"]["cost"] += float(pu.get("cost") or 0.0)
        acc["prompter"]["calls"] += int((s.get("prompter") or {}).get("hops") or 1)

        ju = (s.get("usage") or {}).get("judge") or {}
        acc["judge"]["prompt"] += ju.get("prompt_tokens", 0)
        acc["judge"]["completion"] += ju.get("completion_tokens", 0)
        acc["judge"]["cost"] += float(ju.get("cost_usd") or 0.0)
        acc["judge"]["calls"] += int(ju.get("calls") or 0)

        for a in s.get("attempts") or []:
            for run in a.get("runs") or []:
                try:
                    r = json.loads(Path(run["run_path"]).read_text(encoding="utf-8"))
                except (OSError, ValueError, KeyError):
                    continue
                tok = r.get("tokens") or {}
                acc["rollouts"]["prompt"] += tok.get("prompt", 0)
                acc["rollouts"]["completion"] += tok.get("completion", 0)
                acc["rollouts"]["cached"] += tok.get("cached", 0)
                acc["rollouts"]["cost"] += float(r.get("cost") or 0.0)
                n_rollouts += 1
    return {"n_steps": len(steps), "n_rollouts": n_rollouts, "acc": acc,
            "reward": (json.loads((out / "metadata.json").read_text(encoding="utf-8"))
                       .get("reward") or {}).get("name", "?")}


def report(out: Path) -> None:
    b = breakdown(out)
    if not b:
        print(f"{out.name}: no completed steps yet")
        return
    n, acc = b["n_steps"], b["acc"]
    print(f"\n=== {out.name} ({b['reward']}) — average of {n} completed step(s), "
          f"{b['n_rollouts'] / n:.1f} rollouts per step ===")
    print(f"{'component':<22}{'in (tok)':>12}{'cached':>12}{'out (tok)':>11}"
          f"{'in $':>11}{'out $':>10}{'total $':>11}")

    total = 0.0
    for key, label, price in (("prompter", "prompter (glm-5.3)", "prompter"),
                              ("rollouts", "rollouts (target)", "target"),
                              ("judge", "judge x3 (target)", "target")):
        a = acc[key]
        p, c, o, cost = (a["prompt"] / n, a["cached"] / n, a["completion"] / n, a["cost"] / n)
        pr = PRICES[price]
        if key == "judge":
            # No cached count is recorded for the judge; solve for the one that reconciles the
            # charged total, which also tells us the judge's cache hit rate.
            denom = pr["in"] - pr["cached"]
            c = max(0.0, min(p, (p * pr["in"] + o * pr["out"] - cost) / denom)) if denom else 0.0
        if key == "prompter":
            # Same trick the other way: solve for glm-5.3's unpublished cache-read price.
            cache_price = ((cost - (p - c) * pr["in"] - o * pr["out"]) / c) if c else 0.0
            cost_in = (p - c) * pr["in"] + c * max(0.0, cache_price)
        else:
            cost_in = (p - c) * pr["in"] + c * pr["cached"]
        cost_out = o * pr["out"]
        total += cost
        print(f"{label:<22}{p:>12,.0f}{c:>12,.0f}{o:>11,.0f}"
              f"{_money(cost_in):>11}{_money(cost_out):>10}{_money(cost):>11}")
        resid = cost - (cost_in + cost_out)
        if abs(resid) > max(0.002, 0.05 * cost):
            print(f"{'':<22}(split does not reconcile: residual {_money(resid)} — price stale?)")
    print(f"{'TOTAL / step':<22}{'':>35}{'':>21}{_money(total):>11}")
    print(f"{'calls / step':<22}prompter {acc['prompter']['calls'] / n:.1f}   "
          f"judge {acc['judge']['calls'] / n:.0f}   rollouts {b['n_rollouts'] / n:.1f}")
    cach = acc["rollouts"]
    if cach["prompt"]:
        print(f"{'rollout prompt cache':<22}{100 * cach['cached'] / cach['prompt']:.0f}% of input "
              f"served from cache — without it the rollout input alone would cost "
              f"{_money((cach['prompt'] / n) * PRICES['target']['in'])}/step")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dirs", nargs="+")
    for d in ap.parse_args().out_dirs:
        report(Path(d))


if __name__ == "__main__":
    main()
