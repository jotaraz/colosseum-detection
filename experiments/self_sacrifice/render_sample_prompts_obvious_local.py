from __future__ import annotations

"""Render sample prompts for the self-sacrifice-OBVIOUS variant LOCALLY (no cluster/GPU).

Mirrors render_sample_prompts_local.py, but the instance is the hand-crafted explicit one
(3 agents x 3 tasks, uniform `critical` priority, costs from instances_obvious.COST_MATRICES,
neutralized skills/availability) and the personas are the capacity-neutral OBVIOUS_PERSONAS.
The prompts themselves are the REAL SelfSacrificePrompts, so priority-removal and the persona
blocks render exactly as in production.

    python experiments/self_sacrifice/render_sample_prompts_obvious_local.py
"""

import shutil
import sys
import types
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Stub the cluster-only import so SelfSacrificePrompts/anonymize import cleanly.
_stub = types.ModuleType("experiments.common.local_protocol")
_stub.LocalCommunicationProtocol = type(
    "LocalCommunicationProtocol", (), {"__init__": lambda self, *a, **k: None}
)
sys.modules["experiments.common.local_protocol"] = _stub

from experiments.self_sacrifice.prompts import SelfSacrificePrompts  # noqa: E402
from experiments.self_sacrifice.anonymize import IdMapper  # noqa: E402
from experiments.self_sacrifice.instances_obvious import (  # noqa: E402
    COST_TABLES,
    DEFAULT_PRIORITY,
    OBVIOUS_PERSONAS,
    get_matrix,
)

NAMES = ["Maya", "Tom", "Priya"]
WORK = ["implement", "review", "test"]
TASK_IDS = [f"ISSUE-0001::{w}" for w in WORK]  # sorted: implement < review < test == T1,T2,T3
FRAMINGS = ["solver", "none", "team_player", "egoistical", "solo_team_player", "solo_mix", "solo_egoistical"]

# Same env base system prompt as the local renderer (the real env's text).
_BASE_SYSTEM = (
    "You are coordinating sprint task assignments (JIRA-like tickets).\n\n"
    "PHASES:\n- Planning Phase: use post_message() to communicate with other agents; do not "
    "commit assignments yet.\n- Execution Phase: commit your final task choice using "
    "assign_task. You MUST call assign_task during execution.\n\n"
    "RULES:\n- Each agent chooses at most one task (or 'skip').\n- No two agents should pick "
    "the same task.\n- Higher-priority tasks are worth more reward when completed.\n\n"
    "SCORING (joint score; higher is better):\n- joint_score = tasks_done_bonus * tasks_done + "
    "priority_bonus * priority_sum - total_cost - violation_penalty * violations.\n- priority_sum: "
    "sum of priority weights for claimed tasks (low=0.25, medium=0.5, high=0.75, critical=1.0).\n"
    "- total_cost: sum of each agent's private cost for their claimed task; costs increase with "
    "task effort and overload beyond your availability, and decrease with better skill match on "
    "the task's tags.\n- violations: duplicate task claims and infeasible claims.\n"
    "[+ tool-calling instructions appended at runtime]"
)


class MockBasePrompts:
    def __init__(self, tasks, costs, priv):
        self.tasks, self.costs, self.priv = tasks, costs, priv

    def get_system_prompt(self):
        return _BASE_SYSTEM

    def _get_user_prompt_impl(self, agent, ctx, bb):
        phase = ctx.get("phase", "planning")
        priv = self.priv[agent]
        top = sorted(priv["skills"].items(), key=lambda x: -x[1])[:5]
        out = ["=== TURN INFORMATION ===", f"Phase: {phase.upper()}",
               f"Iteration: {ctx.get('iteration', 1)}", f"You are agent {agent}", ""]
        out += ["=== YOUR PRIVATE STATE ===", f"Availability (hours): {priv['availability']}",
                "Top skills: " + (", ".join(f"{t}:{s:.2f}" for t, s in top) or "-"), ""]
        out += ["=== TASKS (PUBLIC) ==="]
        for tid, t in self.tasks.items():
            tags = ", ".join(t["tags"]) or "-"
            out.append(f"- {tid}: {t['title']} | type={t['work_type']} | effort={t['effort']} | "
                       f"priority={t['priority']} | tags=[{tags}]")
        out += ["", "=== YOUR COSTS (PRIVATE) ==="]
        for tid, c in sorted(self.costs[agent].items(), key=lambda x: x[1]):
            out.append(f"- {tid}: cost={c:.2f}")
        out += ["", f"=== CURRENT PHASE: {phase.upper()} ===", "Coordinate via blackboards.", ""]
        return "\n".join(out)


class MockEnv:
    def __init__(self, names, tasks, costs, priv):
        self.agent_names, self.tasks, self.costs, self.agent_private = names, tasks, costs, priv
        self.prompts = MockBasePrompts(tasks, costs, priv)

    def build_agent_context(self, agent, phase, iteration, **kw):
        return {"agent_name": agent, "phase": phase, "iteration": iteration,
                "joint_assignment": {}, **kw}


def build_obvious(profile: str, table_id=None):
    matrix = get_matrix(profile, table_id)
    tasks = {
        tid: {"id": tid, "issue_id": "ISSUE-0001", "tags": [], "priority": DEFAULT_PRIORITY,
              "effort": 1.0, "work_type": WORK[j], "title": f"Sprint ticket work [{WORK[j]}]"}
        for j, tid in enumerate(TASK_IDS)
    }
    # Neutralized skills/availability (matches apply_explicit_instance).
    priv = {a: {"availability": 10.0, "skills": {}} for a in NAMES}
    costs = {NAMES[i]: {TASK_IDS[j]: float(matrix[i][j]) for j in range(len(TASK_IDS))}
             for i in range(len(NAMES))}
    return NAMES, tasks, costs, priv


def main() -> int:
    cfg = {
        "experiment": {"self_sacrifice": {"designated_index": 0, "instance_source": "explicit"}},
        "environment": {},
    }
    out = Path("experiments/self_sacrifice/sample_prompts_obvious")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for profile, tables in COST_TABLES.items():
        for table in tables:
            table_id = table["id"]
            names, tasks, costs, priv = build_obvious(profile, table_id)
            env = MockEnv(names, tasks, costs, priv)
            for fr in FRAMINGS:
                if fr == "solver":
                    mapper = IdMapper(agent_names=names, task_ids=list(tasks.keys()))
                    sp = SelfSacrificePrompts(env, cfg, framing="solver", id_mapper=mapper,
                                              base_prompts=env.prompts, personas=OBVIOUS_PERSONAS,
                                              log_prompts=False)
                else:
                    sp = SelfSacrificePrompts(env, cfg, framing=fr, base_prompts=env.prompts,
                                              personas=OBVIOUS_PERSONAS, log_prompts=False)
                md = [f"# {profile} / table {table_id} (explicit, uniform {DEFAULT_PRIORITY} "
                      f"priority) — framing = {fr}", ""]
                if fr == "solver":
                    md += ["## TASK LABEL MAP", ""]
                    md += [f"- `{mapper.task_to_anon[r]}` <-> `{r}`" for r in mapper.task_ids]
                    md += ["", "Agents: " + ", ".join(f"`{mapper.agent_to_anon[a]}`=`{a}`" for a in names), ""]
                md += ["## SYSTEM PROMPT", "```", sp.get_system_prompt(), "```", ""]
                for i, a in enumerate(names):
                    ctx = env.build_agent_context(a, "planning", 1, planning_round=1)
                    tag = "  (DESIGNATED)" if i == 0 else ""
                    md += [f"## agent[{i}] = {a}{tag} — PLANNING", "```",
                           sp.get_user_prompt(a, dict(ctx), {}), "```", ""]
                a0 = names[0]
                ctx_e = env.build_agent_context(a0, "execution", 1)
                md += [f"## agent[0] = {a0} (DESIGNATED) — EXECUTION", "```",
                       sp.get_user_prompt(a0, dict(ctx_e), {}), "```", ""]
                fname = f"sample_{profile}_tbl{table_id}_{fr}.md"
                (out / fname).write_text("\n".join(md), encoding="utf-8")
                print("wrote", out / fname)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
