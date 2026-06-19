from __future__ import annotations

"""Render sample prompts LOCALLY (no cluster / no terrarium).

The real JiraTicketEnvironment lives in the cluster-only `terrarium`/`envs` packages, so
this mocks (a) the instance generation — a verified byte-for-byte mirror of the env's cost
tables — and (b) the env's native base JIRA prompt, then runs them through the REAL
SelfSacrificePrompts (so the persona blocks, JIRA-body rewrite, and solver prompt are
exactly the production code). Agent names are illustrative (the env uses random names).

    python experiments/self_sacrifice/render_sample_prompts_local.py
"""

import math
import random
import shutil
import sys
import types
from pathlib import Path

# Stub the cluster-only import so SelfSacrificePrompts/anonymize import cleanly.
_stub = types.ModuleType("experiments.common.local_protocol")
_stub.LocalCommunicationProtocol = type("LocalCommunicationProtocol", (), {"__init__": lambda self, *a, **k: None})
sys.modules["experiments.common.local_protocol"] = _stub

from experiments.self_sacrifice.prompts import SelfSacrificePrompts  # noqa: E402
from experiments.self_sacrifice.anonymize import IdMapper  # noqa: E402

_TAGS = ["backend", "frontend", "infrastructure", "machine-learning", "security", "data-science",
         "api-development", "ui-ux-design", "devops", "mobile-development", "testing", "documentation"]
_MICRO = ["implement", "review", "test", "docs", "triage"]
_PRIO = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
_MULT = {"implement": 1.0, "review": 0.5, "test": 0.7, "docs": 0.5, "triage": 0.4}
# Illustrative names (the env draws random first names from a separate rng → cosmetic only).
_NAMES = ["Maya", "Tom", "Priya", "Diego", "Sara", "Kenji"]


def build_instance(seed: int, n: int = 6, max_tasks: int = 8):
    """Mirror of JiraTicketEnvironment instance generation (skips the separate name rng)."""
    rng = random.Random(seed)
    tag_pool = list(_TAGS)
    prio_pool = list(_PRIO.keys())
    issue_count = max(1, int(math.ceil(max_tasks / max(1, len(_MICRO)))))
    issues = []
    for idx in range(issue_count):
        max_tags = min(2, len(tag_pool))
        tags = rng.sample(tag_pool, k=(rng.randint(1, max_tags) if max_tags else 0))
        prio = rng.choice(prio_pool)
        effort = float(rng.randint(2, 8))
        summary = f"{rng.choice(['Build', 'Fix', 'Improve'])} {rng.choice(tags)}"
        issues.append({"issue_id": f"ISSUE-{idx + 1:04d}", "tags": tags, "priority": prio,
                       "effort": effort, "summary": summary})
    tasks = {}
    for issue in issues:
        for micro in _MICRO:
            tid = f"{issue['issue_id']}::{micro}"
            if tid in tasks:
                continue
            eff = max(1.0, issue["effort"] * _MULT.get(micro, 0.6))
            tasks[tid] = {"id": tid, "issue_id": issue["issue_id"], "tags": list(issue["tags"]),
                          "priority": issue["priority"], "effort": eff, "work_type": micro,
                          "title": f"{issue['summary']} [{micro}]"}
            if len(tasks) >= max_tasks:
                break
        if len(tasks) >= max_tasks:
            break
    skill_pool = sorted({t for tk in tasks.values() for t in tk["tags"]})
    names = _NAMES[:n]
    priv = {}
    for a in names:
        mp = min(2, len(skill_pool))
        ptags = rng.sample(skill_pool, k=(rng.randint(1, mp) if mp else 0)) if skill_pool else []
        priv[a] = {"availability": float(rng.randint(4, 10)),
                   "skills": {t: rng.uniform(0.6, 1.0) for t in ptags}}
    costs = {a: {} for a in names}
    for a in names:
        sk = priv[a]["skills"]
        avail = float(priv[a]["availability"])
        for tid, tk in tasks.items():
            tg = tk["tags"]
            match = (sum(sk.get(x, 0.0) for x in tg) / max(1, len(tg))) if tg else 0.0
            eff = float(tk["effort"])
            costs[a][tid] = float(max(0.0, eff / max(0.1, match + 0.1) + 1.0 * max(0.0, eff - avail)))
    return names, tasks, costs, priv


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
        for tid, c in sorted(self.costs[agent].items(), key=lambda x: x[1]):  # env sorts ascending
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


def main():
    seed = 2
    names, tasks, costs, priv = build_instance(seed)
    env = MockEnv(names, tasks, costs, priv)
    cfg = {"experiment": {"self_sacrifice": {"designated_index": 0}}, "environment": {}}
    out = Path("experiments/self_sacrifice/sample_prompts")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for fr in ["solver", "team_player", "mix", "egoistical", "none"]:
        if fr == "solver":
            mapper = IdMapper(agent_names=names, task_ids=list(tasks.keys()))
            sp = SelfSacrificePrompts(env, cfg, framing="solver", id_mapper=mapper,
                                      base_prompts=env.prompts, log_prompts=False)
        else:
            sp = SelfSacrificePrompts(env, cfg, framing=fr, base_prompts=env.prompts, log_prompts=False)
        md = [f"# neutral (seed {seed}) — framing = {fr}", ""]
        md += ["## SYSTEM PROMPT", "```", sp.get_system_prompt(), "```", ""]
        for i, a in enumerate(names):
            ctx = env.build_agent_context(a, "planning", 1, planning_round=1)
            tag = "  (DESIGNATED)" if i == 0 else ""
            md += [f"## agent[{i}] = {a}{tag} — PLANNING", "```",
                   sp.get_user_prompt(a, dict(ctx), {}), "```", ""]
        (out / f"sample_neutral_{fr}.md").write_text("\n".join(md), encoding="utf-8")
        print("wrote", out / f"sample_neutral_{fr}.md")


if __name__ == "__main__":
    main()
