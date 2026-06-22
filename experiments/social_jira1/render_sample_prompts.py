from __future__ import annotations

"""Render sample social-jira1 prompts offline (no terrarium / no model needed).

Builds a scenario with placeholder employees, then renders the system prompt and one
agent's planning + execution user prompts for each (scenario_type, framing). Useful for
eyeballing the prose (no raw numbers should ever appear) and the confidentiality framing.

    python -m experiments.social_jira1.render_sample_prompts
    python -m experiments.social_jira1.render_sample_prompts --employees 6 --tasks 2 --seed 1
"""

import argparse
from pathlib import Path

from experiments.social_jira1.prompts import ALL_FRAMINGS, SocialJiraPrompts
from experiments.social_jira1.scenario import generate_scenario, pair_key


def _context_from_scenario(scenario, agent_name, phase, planning_round=None):
    my_feelings = {
        other: scenario.feelings[agent_name][other]
        for other in scenario.employees
        if other != agent_name
    }
    pair_goodness = {
        task: {
            f"{pair_key(a, b)[0]} & {pair_key(a, b)[1]}": scenario.goodness[task][pair_key(a, b)]
            for (a, b) in scenario.goodness[task]
        }
        for task in scenario.tasks
    }
    ctx = {
        "agent_name": agent_name,
        "employee": agent_name,
        "phase": phase,
        "iteration": 1,
        "scenario_type": scenario.scenario_type,
        "employees": list(scenario.employees),
        "tasks": list(scenario.tasks),
        "my_feelings": my_feelings,
        "pair_goodness": pair_goodness,
        "assignment": {},
    }
    if planning_round is not None:
        ctx["planning_round"] = planning_round
    return ctx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--employees", type=int, default=6)
    parser.add_argument("--tasks", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--out", default="experiments/social_jira1/sample_prompts", help="output directory"
    )
    args = parser.parse_args()

    employees = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Henry"][: args.employees]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for scenario_type in ("resolvable", "conflict"):
        scenario = generate_scenario(
            seed=args.seed, employees=employees, num_tasks=args.tasks, scenario_type=scenario_type
        )
        actor = employees[0]
        for framing in ALL_FRAMINGS:
            prompts = SocialJiraPrompts(env=None, full_config={}, framing=framing, log_prompts=False)
            system = prompts.get_system_prompt()
            planning = prompts.get_user_prompt(
                actor, _context_from_scenario(scenario, actor, "planning", 1), {}
            )
            execution = prompts.get_user_prompt(
                actor, _context_from_scenario(scenario, actor, "execution"), {}
            )
            text = (
                f"########## SCENARIO_TYPE={scenario_type}  FRAMING={framing}  "
                f"actor={actor}  seed={args.seed} ##########\n\n"
                f"===== SYSTEM =====\n{system}\n\n"
                f"===== USER (planning, round 1) =====\n{planning}\n\n"
                f"===== USER (execution) =====\n{execution}\n"
            )
            path = out_dir / f"sample_{scenario_type}_{framing}.md"
            path.write_text(text)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
