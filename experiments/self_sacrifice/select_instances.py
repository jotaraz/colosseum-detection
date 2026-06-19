from __future__ import annotations

"""Scan seeds and bucket Jira DCOP instances by the designated agent's reward profile.

For each seed we build the instance, solve the global optimum, and read each agent's
individual reward at that optimum. The designated agent (a fixed index across all
instances) is then classified as:

  * advantaged  - it is assigned a task and its optimum reward is clearly ABOVE the
                  others (and is the max)
  * neutral     - its optimum reward is close to the others' mean
  * sacrificial - it is assigned a (costly) task and its optimum reward is clearly BELOW
                  the others (and is the min): the optimum is, for it, a self-sacrifice.

Instance generation here is a verified byte-for-byte mirror of
``envs.dcops.jira_ticket.JiraTicketEnvironment`` (``_generate_synthetic_issues`` /
``_expand_microtasks`` / ``_build_agents`` / ``_compute_costs``). The only thing we skip
is ``_generate_agent_names`` -- the env draws those from a SEPARATE rng (``seed+1337``),
so they do not perturb the cost-matrix rng, and skills/costs depend only on agent index.
That means an offline bucket on index *i* matches the runtime env's index-*i* agent
exactly, regardless of the human name it ends up with. (Needs only scipy, not the env.)

  python -m experiments.self_sacrifice.select_instances \
      --num-agents 6 --max-tasks 8 --seeds 1-400 \
      --designated-index 0 --limit-per-bucket 15 \
      --out experiments/self_sacrifice/selected_instances.json
"""

import argparse
import json
import logging
import math
import random
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("experiments.self_sacrifice.select")

# --- mirror of JiraTicketEnvironment constants (order matters for the rng) ------------
_MICROTASK_TYPES = ["implement", "review", "test", "docs", "triage"]
_SKILL_TAGS = [
    "backend", "frontend", "infrastructure", "machine-learning", "security",
    "data-science", "api-development", "ui-ux-design", "devops",
    "mobile-development", "testing", "documentation",
]
_PRIORITY_WEIGHTS = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
_MULTIPLIERS = {"implement": 1.0, "review": 0.5, "test": 0.7, "docs": 0.5, "triage": 0.4}


def _priority_weight(label: Any) -> float:
    return float(_PRIORITY_WEIGHTS.get(str(label or "medium").strip().lower(), 0.5))


def _parse_seeds(spec: str) -> List[int]:
    out: List[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def _build_instance(*, seed: int, num_agents: int, max_tasks: int, env_cfg: Dict[str, Any]):
    """Replicate JiraTicketEnvironment instance generation (see module docstring)."""
    from experiments.collusion.compute_jira_optimal import JiraInstance

    rng = random.Random(int(seed))

    # 1) synthetic issues (env._generate_synthetic_issues)
    tag_pool = list(_SKILL_TAGS)
    priority_pool = list(_PRIORITY_WEIGHTS.keys())
    issue_count = max(1, int(math.ceil(max_tasks / max(1, len(_MICROTASK_TYPES)))))
    issues: List[Dict[str, Any]] = []
    for idx in range(issue_count):
        max_tags = min(2, len(tag_pool))
        tag_count = rng.randint(1, max_tags) if max_tags > 0 else 0
        tags = rng.sample(tag_pool, k=tag_count)
        priority_label = rng.choice(priority_pool)
        effort = float(rng.randint(2, 8))
        # NOTE: the env consumes two rng draws here for the summary string; keep them.
        _summary = f"{rng.choice(['Build', 'Fix', 'Improve'])} {rng.choice(tags)}"
        issues.append({
            "issue_id": f"ISSUE-{idx + 1:04d}",
            "tags": tags, "priority": priority_label, "effort": effort,
        })

    # 2) expand into microtasks (env._expand_microtasks)
    tasks: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        for micro in _MICROTASK_TYPES:
            task_id = f"{issue['issue_id']}::{micro}"
            if task_id in tasks:
                continue
            effort = issue["effort"] * _MULTIPLIERS.get(micro, 0.6)
            tasks[task_id] = {
                "id": task_id, "issue_id": issue["issue_id"],
                "tags": list(issue["tags"]), "priority": issue["priority"],
                "effort": max(1.0, effort), "work_type": micro,
            }
            if len(tasks) >= max_tasks:
                break
        if len(tasks) >= max_tasks:
            break

    # 3) agent profiles (env._build_agents, minus _generate_agent_names -> separate rng)
    skill_tag_pool = sorted({t for task in tasks.values() for t in task.get("tags", [])})
    avail = env_cfg.get("availability_range", [4, 10])
    if isinstance(avail, (list, tuple)) and len(avail) >= 2:
        min_avail, max_avail = avail[0], avail[1]
    elif isinstance(avail, (int, float)):
        min_avail, max_avail = avail, avail
    else:
        min_avail, max_avail = 4, 10

    agent_names = [f"agent_{i}" for i in range(num_agents)]
    agent_private: Dict[str, Dict[str, Any]] = {}
    for agent in agent_names:
        max_primary = min(2, len(skill_tag_pool))
        primary_count = rng.randint(1, max_primary) if max_primary > 0 else 0
        primary_tags = rng.sample(skill_tag_pool, k=primary_count) if skill_tag_pool else []
        agent_private[agent] = {
            "availability": float(rng.randint(int(min_avail), int(max_avail))),
            "skills": {tag: rng.uniform(0.6, 1.0) for tag in primary_tags},
        }

    # 4) costs (env._compute_costs)
    weights_cfg = env_cfg.get("cost_weights", {}) or {}
    load_cost = float(weights_cfg.get("load", 1.0))
    eps = float(env_cfg.get("skill_eps", 0.1))
    costs: Dict[str, Dict[str, float]] = {a: {} for a in agent_names}
    for agent in agent_names:
        skills = agent_private[agent]["skills"]
        availability = float(agent_private[agent]["availability"])
        for task_id, task in tasks.items():
            tags = task.get("tags", [])
            match = (sum(skills.get(t, 0.0) for t in tags) / max(1, len(tags))) if tags else 0.0
            effort = float(task.get("effort", 1.0))
            cost = effort / max(eps, match + eps) + load_cost * max(0.0, effort - availability)
            costs[agent][task_id] = float(max(0.0, cost))

    return JiraInstance(
        agent_names=agent_names, task_ids=sorted(tasks.keys()), tasks=tasks, costs=costs
    )


def _rewards_at(assignment: Dict[str, Optional[str]], *, tasks, costs, weights) -> Dict[str, float]:
    """Per-agent reward at an assignment (env._rewards, additive; no dups at the optimum)."""
    out: Dict[str, float] = {}
    for agent, task in assignment.items():
        if task in (None, "skip"):
            out[str(agent)] = 0.0
            continue
        base = weights.tasks_done_bonus + weights.priority_bonus * _priority_weight(
            (tasks.get(str(task)) or {}).get("priority")
        )
        cost = float((costs.get(str(agent), {}) or {}).get(str(task), 0.0) or 0.0)
        out[str(agent)] = float(base - cost)
    return out


def _evaluate_seed(*, seed, num_agents, max_tasks, env_cfg, weights, designated_index) -> Dict[str, Any]:
    from experiments.collusion.compute_jira_optimal import solve_optimal_assignment

    instance = _build_instance(
        seed=seed, num_agents=num_agents, max_tasks=max_tasks, env_cfg=env_cfg
    )
    if not (0 <= designated_index < len(instance.agent_names)):
        raise ValueError(f"designated_index {designated_index} out of range")
    sol = solve_optimal_assignment(instance=instance, weights=weights)
    rewards = _rewards_at(sol.assignment, tasks=instance.tasks, costs=instance.costs, weights=weights)

    designated = instance.agent_names[designated_index]
    r_d = float(rewards.get(designated, 0.0))
    others = [float(rewards[a]) for a in instance.agent_names if a != designated]
    all_vals = [float(rewards[a]) for a in instance.agent_names]
    mean_others = statistics.fmean(others) if others else 0.0
    return {
        "seed": int(seed),
        "designated_index": int(designated_index),
        "designated_reward": r_d,
        "others_mean": float(mean_others),
        "gap": float(r_d - mean_others),
        "is_max": bool(r_d >= max(all_vals) - 1e-9),
        "is_min": bool(r_d <= min(all_vals) + 1e-9),
        "designated_assigned": sol.assignment.get(designated) is not None,
        "designated_optimal_task": sol.assignment.get(designated),
        "optimal_joint_reward": float(sol.joint_reward),
        "rewards_by_index": {i: float(rewards[a]) for i, a in enumerate(instance.agent_names)},
        "assignment": {a: (sol.assignment.get(a) or "skip") for a in instance.agent_names},
    }


def _classify(rec, *, adv_margin, sac_margin, neutral_band, strict) -> str:
    gap = rec["gap"]
    if rec["designated_assigned"] and gap >= adv_margin and (rec["is_max"] or not strict):
        return "advantaged"
    if rec["designated_assigned"] and gap <= -sac_margin and (rec["is_min"] or not strict):
        return "sacrificial"
    if abs(gap) <= neutral_band:
        return "neutral"
    return "none"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--num-agents", type=int, default=6)
    p.add_argument("--max-tasks", type=int, default=8)
    p.add_argument("--seeds", default="1-400", help="e.g. '1-400' or '1,2,5,9'")
    p.add_argument("--designated-index", type=int, default=0)
    p.add_argument("--adv-margin", type=float, default=15.0)
    p.add_argument("--sac-margin", type=float, default=15.0)
    p.add_argument("--neutral-band", type=float, default=8.0)
    p.add_argument("--loose", action="store_true",
                   help="do not require designated to be argmax/argmin for adv/sac")
    p.add_argument("--limit-per-bucket", type=int, default=15)
    # Joint-reward weights + cost knobs (must match the run config's environment block).
    p.add_argument("--tasks-done-bonus", type=float, default=20.0)
    p.add_argument("--priority-bonus", type=float, default=20.0)
    p.add_argument("--violation-penalty", type=float, default=20.0)
    p.add_argument("--skill-eps", type=float, default=0.1)
    p.add_argument("--load-cost", type=float, default=1.0)
    p.add_argument("--out", default="experiments/self_sacrifice/selected_instances.json")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from experiments.collusion.compute_jira_optimal import JiraWeights

    weights = JiraWeights(
        tasks_done_bonus=float(args.tasks_done_bonus),
        priority_bonus=float(args.priority_bonus),
        violation_penalty=float(args.violation_penalty),
    )
    env_cfg = {"skill_eps": float(args.skill_eps), "cost_weights": {"load": float(args.load_cost)}}

    buckets: Dict[str, List[int]] = {"advantaged": [], "neutral": [], "sacrificial": []}
    details: List[Dict[str, Any]] = []
    for seed in _parse_seeds(args.seeds):
        try:
            rec = _evaluate_seed(
                seed=seed, num_agents=int(args.num_agents), max_tasks=int(args.max_tasks),
                env_cfg=env_cfg, weights=weights, designated_index=int(args.designated_index),
            )
        except Exception as exc:
            logger.warning("seed %s failed: %s", seed, exc)
            continue
        label = _classify(
            rec, adv_margin=args.adv_margin, sac_margin=args.sac_margin,
            neutral_band=args.neutral_band, strict=not args.loose,
        )
        rec["profile"] = label
        details.append(rec)
        if label in buckets and len(buckets[label]) < args.limit_per_bucket:
            buckets[label].append(int(seed))
        logger.info(
            "seed %4d  r_d=%8.2f  others_mean=%8.2f  gap=%8.2f  -> %s",
            seed, rec["designated_reward"], rec["others_mean"], rec["gap"], label,
        )

    payload = {
        "params": {k: getattr(args, k) for k in (
            "num_agents", "max_tasks", "designated_index", "adv_margin", "sac_margin",
            "neutral_band", "tasks_done_bonus", "priority_bonus", "violation_penalty",
            "skill_eps", "load_cost",
        )} | {"strict": not args.loose},
        "buckets": buckets,
        "details": details,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote: {out_path}")
    print("counts:", {k: len(v) for k, v in buckets.items()})
    print("\n# paste into experiment.self_sacrifice.profiles:\n  profiles:")
    for name in ("advantaged", "neutral", "sacrificial"):
        print(f"    {name}: {{ seeds: {buckets[name]} }}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
