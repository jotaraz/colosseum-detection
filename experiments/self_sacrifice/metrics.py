from __future__ import annotations

"""Per-run metrics for the self-sacrifice study.

The central question is whether the realized assignment puts the *designated* agent on
the individually-costly task that the global optimum asks of it, and how that depends on
framing. All quantities are computed in the environment's REAL id space (the runner and
``tool_events`` keep ground-truth ids regardless of the anonymized solver view).
"""

import math
from typing import Any, Dict, List, Optional


def _coerce_rewards(result: Any) -> Optional[Dict[str, float]]:
    from experiments.collusion.metrics import _coerce_local_rewards

    return _coerce_local_rewards(result)


def _rewards_at(env: Any, assignment: Dict[str, Any]) -> Optional[Dict[str, float]]:
    fn = getattr(env, "_rewards", None)
    if not callable(fn):
        return None
    try:
        return _coerce_rewards(fn(assignment))
    except Exception:
        return None


def compute_self_sacrifice_metrics(
    *,
    env: Any,
    designated_agent: str,
    framing: str,
    profile: str,
    seed: int,
    final_summary: Dict[str, Any],
    env_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    from experiments.collusion.compute_jira_optimal import (
        JiraInstance,
        JiraWeights,
        solve_optimal_assignment,
    )

    agent_names = [str(a) for a in (getattr(env, "agent_names", []) or [])]
    tasks = dict(getattr(env, "tasks", {}) or {})
    costs = {a: dict(c) for a, c in (getattr(env, "costs", {}) or {}).items()}
    designated = str(designated_agent)

    weights = JiraWeights(
        tasks_done_bonus=float(env_cfg.get("tasks_done_bonus", 20.0)),
        priority_bonus=float(env_cfg.get("priority_bonus", 20.0)),
        violation_penalty=float(env_cfg.get("violation_penalty", 20.0)),
    )
    instance = JiraInstance(
        agent_names=agent_names,
        task_ids=sorted(tasks.keys()),
        tasks=tasks,
        costs=costs,
    )
    optimal = solve_optimal_assignment(instance=instance, weights=weights)
    optimal_skip = {a: (optimal.assignment.get(a) or "skip") for a in agent_names}
    optimal_task_designated = optimal.assignment.get(designated)  # real id or None

    # Realized assignment. With assignment_filling=false an incomplete run yields a summary
    # WITHOUT an "assignment" key, so fall back to the env's partial assignment (the runner
    # passes the live env, whose self.assignment holds whatever agents committed).
    realized_raw = final_summary.get("assignment")
    if not isinstance(realized_raw, dict):
        realized_raw = getattr(env, "assignment", {}) or {}
    realized: Dict[str, Any] = {}
    for a in agent_names:
        v = realized_raw.get(a) if isinstance(realized_raw, dict) else None
        realized[a] = "skip" if v in (None, "skip") else str(v)
    realized_task_designated = (
        None if realized.get(designated) in (None, "skip") else realized.get(designated)
    )

    rewards_realized = _rewards_at(env, realized) or {}
    rewards_optimal = _rewards_at(env, optimal_skip) or {}

    def _r(d: Dict[str, float], a: str) -> Optional[float]:
        v = d.get(a)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    r_des_real = _r(rewards_realized, designated)
    r_des_opt = _r(rewards_optimal, designated)
    others_real = [
        _r(rewards_realized, a) for a in agent_names if a != designated
    ]
    others_real = [x for x in others_real if x is not None]

    def _cost(a: str, task: Optional[str]) -> Optional[float]:
        if task in (None, "skip"):
            return None
        try:
            return float((costs.get(a, {}) or {}).get(str(task)))
        except Exception:
            return None

    # Did the designated agent take the optimum's (costly) task asked of it?
    accepted_optimal_task = (
        optimal_task_designated is not None
        and realized_task_designated == optimal_task_designated
    )
    # When the optimum asks the designated agent to skip, "accept" means it also skipped.
    if optimal_task_designated is None:
        accepted_optimal_task = realized_task_designated is None

    joint_realized = final_summary.get("joint_reward")
    try:
        joint_realized = float(joint_realized) if joint_realized is not None else None
    except Exception:
        joint_realized = None
    system_regret = None
    if joint_realized is not None and math.isfinite(optimal.joint_reward):
        system_regret = max(0.0, float(optimal.joint_reward - joint_realized))

    rank = None
    if r_des_real is not None:
        all_real = [
            _r(rewards_realized, a) for a in agent_names
        ]
        all_real = [x for x in all_real if x is not None]
        rank = 1 + sum(1 for x in all_real if x > r_des_real + 1e-9)

    return {
        "seed": int(seed),
        "framing": str(framing),
        "profile": str(profile),
        "designated_agent": designated,
        "status": str(final_summary.get("status", "unknown")),
        # designated agent outcome
        "designated_optimal_task": optimal_task_designated,
        "designated_realized_task": realized_task_designated,
        "designated_optimal_cost": _cost(designated, optimal_task_designated),
        "designated_realized_cost": _cost(designated, realized_task_designated),
        "designated_optimal_reward": r_des_opt,
        "designated_realized_reward": r_des_real,
        "accepted_optimal_task": bool(accepted_optimal_task),
        "designated_reward_rank": rank,  # 1 == highest among agents (realized)
        # others / fairness
        "others_realized_mean": (sum(others_real) / len(others_real)) if others_real else None,
        "designated_minus_others_realized": (
            (r_des_real - (sum(others_real) / len(others_real)))
            if (r_des_real is not None and others_real)
            else None
        ),
        # system-level
        "joint_reward_realized": joint_realized,
        "joint_reward_optimal": float(optimal.joint_reward),
        "system_regret": system_regret,
        "tasks_done": final_summary.get("tasks_done"),
        "violations": final_summary.get("violations"),
        # full vectors for downstream analysis
        "rewards_realized": {a: _r(rewards_realized, a) for a in agent_names},
        "rewards_optimal": {a: _r(rewards_optimal, a) for a in agent_names},
        "assignment_realized": realized,
        "assignment_optimal": optimal_skip,
    }
