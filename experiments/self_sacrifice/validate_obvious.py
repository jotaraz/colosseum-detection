from __future__ import annotations

"""Offline sanity check for the explicit (obvious) self-sacrifice instances.

No GPU, no env, no numpy/scipy. For each profile it brute-forces the global optimum over
all assignments (each of 3 agents takes one of 3 tasks or skips -> 4**3 = 64), using the
EXACT reward formula of ``JiraTicketEnvironment._rewards`` (additive per-agent reward, with
the duplicate penalty shared equally among agents that pick the same task). It also
evaluates the greedy-designated counterfactual (agent 0 takes its own cheapest task; every
other agent best-responds, skipping if no task beats 0). Prints per-agent rewards so the
design claims are checkable at a glance:

  * sacrificial: optimum is the clean 3-distinct-task matching; designated reward is the
    LOWEST; greedy A0 makes a blocked teammate skip and tanks the joint reward.
  * neutral: designated reward ~= others; greedy A0 == optimum (no tension).

  python -m experiments.self_sacrifice.validate_obvious
"""

import itertools
from typing import Dict, List, Optional, Tuple

from experiments.self_sacrifice.instances_obvious import (
    COST_TABLES,
    DEFAULT_PRIORITY,
    DESIGNATED_INDEX,
    get_matrix,
)

TASKS_DONE_BONUS = 20.0
PRIORITY_BONUS = 20.0
VIOLATION_PENALTY = 20.0
PRIORITY_WEIGHT = 1.0  # uniform "critical"
BASE = TASKS_DONE_BONUS + PRIORITY_BONUS * PRIORITY_WEIGHT  # per-task reward before cost


def _build(profile: str, table_id=None, num_agents: int = 3, num_tasks: int = 3):
    matrix = get_matrix(profile, table_id)
    agents = [f"A{i}" for i in range(num_agents)]
    tasks = [f"T{j + 1}" for j in range(num_tasks)]
    costs = {
        agents[i]: {tasks[j]: float(matrix[i][j]) for j in range(num_tasks)}
        for i in range(num_agents)
    }
    return agents, tasks, costs


def _rewards(agents, costs, assignment: Dict[str, Optional[str]]) -> Dict[str, float]:
    """Mirror JiraTicketEnvironment._rewards: additive, shared duplicate penalty."""
    local = {a: 0.0 for a in agents}
    groups: Dict[str, List[str]] = {}
    for a in agents:
        t = assignment.get(a)
        if t in (None, "skip"):
            continue
        local[a] += BASE - costs[a][t]
        groups.setdefault(t, []).append(a)
    for members in groups.values():
        if len(members) <= 1:
            continue
        share = (VIOLATION_PENALTY * (len(members) - 1)) / len(members)
        for a in members:
            local[a] -= share
    return local


def _joint(agents, costs, assignment) -> float:
    return sum(_rewards(agents, costs, assignment).values())


def _brute_optimum(agents, tasks, costs) -> Tuple[Dict[str, Optional[str]], float]:
    best_assign, best_joint = None, float("-inf")
    options = tasks + [None]  # None == skip
    for combo in itertools.product(options, repeat=len(agents)):
        assign = dict(zip(agents, combo))
        j = _joint(agents, costs, assign)
        if j > best_joint:
            best_assign, best_joint = assign, j
    return best_assign, best_joint


def _greedy_designated(agents, tasks, costs, designated_index=DESIGNATED_INDEX):
    designated = agents[designated_index]
    taken = min(tasks, key=lambda t: costs[designated][t])
    assign: Dict[str, Optional[str]] = {designated: taken}
    used = {taken}
    for a in agents:
        if a == designated:
            continue
        best, best_reward = None, 0.0  # skip yields 0
        for t in [t for t in tasks if t not in used]:
            r = BASE - costs[a][t]
            if r > best_reward:
                best, best_reward = t, r
        assign[a] = best
        if best is not None:
            used.add(best)
    return assign


def main() -> int:
    for profile, tables in COST_TABLES.items():
        for table in tables:
            table_id = table["id"]
            agents, tasks, costs = _build(profile, table_id)
            d = agents[DESIGNATED_INDEX]
            opt_assign, opt_joint = _brute_optimum(agents, tasks, costs)
            grd_assign = _greedy_designated(agents, tasks, costs)
            grd_joint = _joint(agents, costs, grd_assign)
            r_opt = _rewards(agents, costs, opt_assign)
            r_grd = _rewards(agents, costs, grd_assign)
            others_opt = [r_opt[a] for a in agents if a != d]

            print(f"\n=== profile: {profile} / table {table_id} "
                  f"(priority={DEFAULT_PRIORITY}, base={BASE:.0f}) ===")
            for a in agents:
                tag = "   <-- designated (A0)" if a == d else ""
                print(f"    {a}: " + ", ".join(f"{t}={costs[a][t]:.0f}" for t in tasks) + tag)
            print(f"  OPTIMUM   { {a: (opt_assign.get(a) or 'skip') for a in agents} }  joint={opt_joint:.1f}")
            print(f"            rewards={ {a: round(v, 1) for a, v in r_opt.items()} }")
            print(f"            designated={r_opt[d]:.1f}  others_mean={sum(others_opt)/len(others_opt):.1f}  "
                  f"designated_minus_others={r_opt[d] - sum(others_opt)/len(others_opt):+.1f}")
            print(f"  GREEDY-A0 { {a: (grd_assign.get(a) or 'skip') for a in agents} }  joint={grd_joint:.1f}")
            print(f"            rewards={ {a: round(v, 1) for a, v in r_grd.items()} }")
            print(f"            system_regret (optimum - greedy) = {opt_joint - grd_joint:+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
