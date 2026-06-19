from __future__ import annotations

"""Render sample solver/JIRA agent prompts for inspection (no LLM, no GPU).

Builds the real environment for one seed per profile and writes the rendered system +
per-agent user prompts to markdown files, so the solver vs JIRA framings can be compared
side by side before committing to a run.

    python -m experiments.self_sacrifice.render_sample_prompts
"""

from pathlib import Path

from experiments.collusion.run import _resolve_environment_class
from experiments.common.local_protocol import LocalCommunicationProtocol
from experiments.self_sacrifice.anonymize import IdMapper
from experiments.self_sacrifice.prompts import SelfSacrificePrompts

# One representative seed (neutral profile only — enough to eyeball the framings/personas).
SEEDS = [("neutral", 2)]
FRAMINGS = ["solver", "team_player", "mix", "egoistical", "none"]
NUM_AGENTS = 6
MAX_TASKS = 8
OUT = Path("experiments/self_sacrifice/sample_prompts")


def _build_env(seed: int):
    cfg = {
        "simulation": {"seed": seed, "max_iterations": 1},
        "communication_network": {"topology": "complete", "num_agents": NUM_AGENTS, "consolidate_channels": True},
        "environment": {"name": "JiraTicketEnvironment", "assignment_filling": False, "max_tasks": MAX_TASKS},
        "llm": {},
    }
    proto = LocalCommunicationProtocol(config=cfg)
    env = _resolve_environment_class(cfg["environment"])(
        proto, cfg, tool_logger=type("TL", (), {"log_dir": Path("/tmp")})()
    )
    return env, cfg


def _render(profile: str, seed: int, framing: str) -> Path:
    env, cfg = _build_env(seed)
    names = list(env.agent_names)

    if framing == "solver":
        mapper = IdMapper(agent_names=names, task_ids=list(env.tasks.keys()))
        sp = SelfSacrificePrompts(env, cfg, framing="solver", id_mapper=mapper,
                                  base_prompts=env.prompts, log_prompts=False)
    else:
        mapper = None
        sp = SelfSacrificePrompts(env, cfg, framing=framing,
                                  base_prompts=env.prompts, log_prompts=False)

    md = [f"# Sample prompts — profile={profile} (seed {seed}) — framing={framing}", ""]
    if mapper is not None:
        md += ["## TASK LABEL MAP (solver label <-> real id)", ""]
        for real in mapper.task_ids:
            md.append(f"- `{mapper.task_to_anon[real]}` <-> `{real}`")
        md += ["", f"Agents: " + ", ".join(f"`{mapper.agent_to_anon[a]}`=`{a}`" for a in names), ""]

    md += ["## SYSTEM PROMPT", "```", sp.get_system_prompt(), "```", ""]
    for i, agent in enumerate(names):
        ctx = env.build_agent_context(agent, "planning", 1, planning_round=1)
        up = sp.get_user_prompt(agent, dict(ctx), {})
        tag = "  (DESIGNATED)" if i == 0 else ""
        md += [f"## USER PROMPT — agent[{i}] = {agent}{tag} — PLANNING", "```", up, "```", ""]

    a0 = names[0]
    ctx_e = env.build_agent_context(a0, "execution", 1)
    up_e = sp.get_user_prompt(a0, dict(ctx_e), {})
    md += [f"## USER PROMPT — agent[0] = {a0} (DESIGNATED) — EXECUTION", "```", up_e, "```", ""]

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"sample_{profile}_{framing}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


def main() -> int:
    for profile, seed in SEEDS:
        for framing in FRAMINGS:
            print("wrote", _render(profile, seed, framing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
