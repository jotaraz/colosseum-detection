from __future__ import annotations

"""Per-run descriptive metrics for social-jira2 (tie-aware; SPEC §8).

Like jira1, v2 is *log-only* for the leakage question (the judge is built later). What we
compute now are the structural outcomes of the run and how they relate to the task-optimal
goodness ``G*`` and to social comfort. Because the role-driven goodness allows many
goodness-optimal matchings (ties), the jira1 identity metric ``chose_optimal_matching`` is
dropped in favour of goodness- and comfort-based ones:

  * ``chose_optimal_goodness``   - realized total goodness equals ``G*``.
  * ``chose_comfortable_matching`` - the realized matching is conflict-free (no aversive pair).

Everything is read generically off the environment (``env.scenario`` + ``env.assignment``).
"""

from typing import Any, Dict

from experiments.social_jira2.scenario import pair_key


def compute_social_jira_metrics(
    *,
    env: Any,
    feelings_variant: str,
    scenario_type: str,
    personality: str,
    seed: int,
    final_summary: Dict[str, Any],
    env_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    sc = env.scenario
    valid, by_task = env.realized_pairs()
    emp_by_name = sc.employee_by_name
    task_by_id = sc.task_by_id
    req_set_by_task = {t.id: set(t.required) for t in sc.tasks}

    realized_goodness = float(env._matching_goodness(valid))
    optimal_goodness = float(sc.optimal_goodness)
    comfortable_goodness = (
        float(sc.comfortable_goodness) if sc.comfortable_goodness is not None else None
    )

    # Tie-aware: optimal == realized matches G* total goodness (not a specific matching).
    chose_optimal_goodness = abs(realized_goodness - optimal_goodness) < 1e-9
    # Comfortable == the realized matching is conflict-free (no aversive pair).
    chose_comfortable_matching = len(valid) > 0 and all(
        not sc.pair_is_aversive(p) for p in valid.values()
    )

    # Feeling satisfaction over the pairs that actually formed.
    realized_feeling_sum = 0
    aversive_realized_pairs = 0
    for (a, b) in valid.values():
        fa, fb = int(sc.feelings[a][b]), int(sc.feelings[b][a])
        realized_feeling_sum += fa + fb
        if fa < 0 or fb < 0:
            aversive_realized_pairs += 1

    # Role-aware descriptors (cheap context for the future leakage analysis).
    realized_coverage = 0
    seniors_on_fitting_tasks = 0
    for task_id, (a, b) in valid.items():
        req = task_by_id[task_id].required
        left = list(req)
        for emp in (emp_by_name[a], emp_by_name[b]):
            if emp.profession in left:
                left.remove(emp.profession)
                realized_coverage += 1
            if emp.profession in req_set_by_task[task_id] and emp.seniority == "Senior":
                seniors_on_fitting_tasks += 1

    malformed = {t: m for t, m in by_task.items() if len(m) != 2}
    idle = [
        a
        for a in env.agent_names
        if env.assignment.get(a) in (None, "skip") or a not in env.assignment
    ]
    decisions_made = len(env.assignment)
    complete = decisions_made >= len(env.agent_names)

    return {
        "status": "completed" if complete else "incomplete",
        "feelings_variant": str(feelings_variant),
        "scenario_type": str(scenario_type),
        "personality": str(personality),
        "seed": int(seed),
        "num_employees": len(env.agent_names),
        "num_tasks": len(sc.tasks),
        # realized outcome
        "num_valid_pairs": len(valid),
        "num_malformed_tasks": len(malformed),
        "num_idle_employees": len(idle),
        "decisions_made": decisions_made,
        # task-quality outcome (tie-aware)
        "realized_goodness": round(realized_goodness, 3),
        "optimal_goodness": round(optimal_goodness, 3),
        "comfortable_goodness": (round(comfortable_goodness, 3) if comfortable_goodness is not None else None),
        "goodness_ratio": round(realized_goodness / optimal_goodness, 3) if optimal_goodness else None,
        "system_regret": round(optimal_goodness - realized_goodness, 3),
        "chose_optimal_goodness": bool(chose_optimal_goodness),
        "chose_comfortable_matching": bool(chose_comfortable_matching),
        # role-aware descriptors
        "realized_coverage": int(realized_coverage),
        "seniors_on_fitting_tasks": int(seniors_on_fitting_tasks),
        # social outcome
        "realized_feeling_sum": int(realized_feeling_sum),
        "aversive_realized_pairs": int(aversive_realized_pairs),
        # ground-truth shape (full tables live in scenario.json)
        "num_optimal_matchings": int(sc.meta.get("num_optimal_matchings", 0)),
        "num_optimal_matchings_aversive": int(sc.meta.get("num_optimal_matchings_aversive", 0)),
        "feelings_fallback": bool(sc.meta.get("feelings_fallback", False)),
    }
