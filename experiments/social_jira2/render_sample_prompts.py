from __future__ import annotations

"""Render sample social-jira2 prompts offline (no terrarium / no model needed).

Builds a scenario with placeholder employees, then renders the system prompt and one agent's
planning + execution user prompts for each (scenario_type, feelings_preset) and optionally
each personality. Useful for eyeballing the prose (no raw numbers should ever appear), the
confidentiality framing, and the role-driven tasks block.

    python -m experiments.social_jira2.render_sample_prompts
    python -m experiments.social_jira2.render_sample_prompts --employees 4 --tasks 2 --seed 2
    python -m experiments.social_jira2.render_sample_prompts --personalities    # one file per personality
    python -m experiments.social_jira2.render_sample_prompts --structured       # block library
"""

import argparse
from pathlib import Path

from experiments.social_jira2.prompts import ALL_PERSONALITIES, SocialJiraPrompts
from experiments.social_jira2.scenario import generate_scenario, required_phrase

# default + control are the implemented presets (byte-identical to jira1 discreet/control).
SAMPLE_PRESETS = ("default", "control")


def _context_from_scenario(scenario, agent_name, phase, planning_round=None):
    my_feelings = {
        other: scenario.feelings[agent_name][other]
        for other in scenario.names
        if other != agent_name
    }
    roster = [
        {"name": e.name, "profession": e.profession, "seniority": e.seniority}
        for e in scenario.employees
    ]
    tasks_spec = [
        {"id": t.id, "title": t.title, "required": list(t.required),
         "required_prose": required_phrase(t.required)}
        for t in scenario.tasks
    ]
    ctx = {
        "agent_name": agent_name,
        "employee": agent_name,
        "phase": phase,
        "iteration": 1,
        "scenario_type": scenario.scenario_type,
        "employees": list(scenario.names),
        "tasks": list(scenario.task_ids),
        "roster": roster,
        "tasks_spec": tasks_spec,
        "my_feelings": my_feelings,
        "assignment": {},
    }
    if planning_round is not None:
        ctx["planning_round"] = planning_round
    return ctx


def render_structured_doc(scenario, actor, employees, tasks, seed) -> str:
    """One reference doc defining each prompt building block once (both presets) + an
    assembly map. Blocks are pulled straight from the ``_*_block`` methods so the doc tracks
    ``prompts.py`` exactly."""
    pd = SocialJiraPrompts(env=None, full_config={}, feelings_preset="default", log_prompts=False)
    pc = SocialJiraPrompts(env=None, full_config={}, feelings_preset="control", log_prompts=False)
    ctx = _context_from_scenario(scenario, actor, "planning", 1)
    ctx_vote = _context_from_scenario(scenario, actor, "survey", 1)
    ctx_exec = _context_from_scenario(scenario, actor, "execution")

    other = employees[1] if len(employees) > 1 else actor
    ctx_committed = dict(ctx)
    ctx_committed["assignment"] = {actor: tasks[0], other: tasks[0]}
    ctx_no_dislike = dict(ctx)
    ctx_no_dislike["my_feelings"] = {o: 0 for o in ctx["my_feelings"]}
    discussion_example = {
        "m1": f"{other}'s assistant: Proposal — pair {actor} & {other} on Task {tasks[0]}; "
              "I'll take the other task with someone else.",
    }

    L: list[str] = []
    def add(*lines: str) -> None:
        L.extend(lines)
    def block(token: str, note: str, body: str) -> None:
        add(f"# {token}" + (f"  _{note}_" if note else ""), "", body, "")
    def variant(label: str, body: str) -> None:
        add(f"## {label}", "", body, "")

    add(
        "# social-jira2 prompt building blocks",
        "",
        "_Generated from `prompts.py` by `render_sample_prompts.py --structured` — do not edit by hand._",
        "",
        "## Legend",
        "- `#` / `##` lines are headings of THIS document (outside the prompt).",
        "- `=== ... ===` lines are real separators **inside** the prompt (sent to the model verbatim).",
        "- `{{TOKEN}}` is a placeholder: insert the block defined under that token's heading.",
        f"- Concrete values below use seed={seed}, actor={actor}, scenario_type=conflict. "
        "resolvable vs conflict change ONLY the filled-in values (who is disliked), never the "
        "structure. `default`/`control` are the implemented feelings presets.",
        "",
        "# Assembly — which blocks make each message",
        "```",
        "System message      = {{SYSTEM_PROMPT}}",
        "User (planning)     = {{WHO}} + {{TASKS}} + {{FEELINGS}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{COORDINATE}}",
        "User (prelim. vote) = {{WHO}} + {{TASKS}} + {{FEELINGS}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{VOTE}}",
        "User (execution)    = {{WHO}} + {{TASKS}} + {{FEELINGS}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{COMMIT}}",
        "```",
        "Only the final block differs across phases; `{{DISCUSSION}}` accumulates each round "
        "and is omitted while empty (e.g. planning round 1). Blocks are joined by blank lines.",
        "",
        "# ==================== BLOCK LIBRARY ====================",
        "",
    )

    add("# {{SYSTEM_PROMPT}}", "")
    variant("default (critical confidentiality + hard feeling)", pd.get_system_prompt())
    variant("control (no confidentiality)", pc.get_system_prompt())

    add("# {{WHO}}  _personality=none shown; other personalities append one sentence_", "")
    variant("none (jira1 verbatim)", pd._who_block(actor))
    add("")
    block("{{TASKS}}", "role-driven; values are scenario-specific", pd._tasks_block(ctx))

    add("# {{FEELINGS}}  _values are scenario-specific_", "")
    variant("default", pd._feelings_block(actor, ctx))
    variant("control", pc._feelings_block(actor, ctx))
    _fallback = pd._feelings_block(actor, ctx_no_dislike)
    variant(
        "employee named no one they dislike",
        _fallback if _fallback.strip()
        else "_(block omitted entirely — there is nothing private to convey)_",
    )

    add("# {{COMMITMENTS}}  _runtime: depends on who has committed_", "")
    variant("opening (nobody committed yet)", pd._state_block(ctx))
    variant("later (some have committed)", pd._state_block(ctx_committed))

    block("{{DISCUSSION}}", "runtime: the shared blackboard so far; omitted while empty",
          pd._discussion_block(discussion_example))

    add("# {{COORDINATE}}  _planning instruction_", "")
    variant("default", pd._instruction_block("planning", ctx))
    variant("control", pc._instruction_block("planning", ctx))

    block("{{VOTE}}", "preliminary-vote instruction; private, identical for both presets",
          pd._instruction_block("survey", ctx_vote))
    block("{{COMMIT}}", "execution instruction; identical for both presets",
          pd._instruction_block("execution", ctx_exec))

    return "\n".join(L).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--employees", type=int, default=4)
    parser.add_argument("--tasks", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--structured", action="store_true",
                        help="emit a single block-library reference (prompt_blocks.md)")
    parser.add_argument("--personalities", action="store_true",
                        help="also emit one WHO-block sample per personality")
    parser.add_argument("--out", default="experiments/social_jira2/sample_prompts")
    args = parser.parse_args()

    employees = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Henry"][: args.employees]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.structured:
        scenario = generate_scenario(
            seed=args.seed, employees=employees, num_tasks=args.tasks, scenario_type="conflict"
        )
        doc = render_structured_doc(scenario, employees[0], employees, list(scenario.task_ids), args.seed)
        path = out_dir / "prompt_blocks.md"
        path.write_text(doc)
        print(f"wrote {path}")
        return

    if args.personalities:
        scenario = generate_scenario(
            seed=args.seed, employees=employees, num_tasks=args.tasks, scenario_type="conflict"
        )
        actor = employees[0]
        chunks = []
        for personality in ALL_PERSONALITIES:
            prompts = SocialJiraPrompts(
                env=None, full_config={}, feelings_preset="default",
                personality=personality, log_prompts=False,
            )
            chunks.append(f"===== personality={personality} =====\n{prompts._who_block(actor)}\n")
        path = out_dir / "who_block_personalities.md"
        path.write_text("\n".join(chunks))
        print(f"wrote {path}")
        return

    for scenario_type in ("resolvable", "conflict"):
        scenario = generate_scenario(
            seed=args.seed, employees=employees, num_tasks=args.tasks, scenario_type=scenario_type
        )
        actor = employees[0]
        for preset in SAMPLE_PRESETS:
            prompts = SocialJiraPrompts(
                env=None, full_config={}, feelings_preset=preset, log_prompts=False
            )
            system = prompts.get_system_prompt()
            planning = prompts.get_user_prompt(
                actor, _context_from_scenario(scenario, actor, "planning", 1), {}
            )
            vote = prompts.get_user_prompt(
                actor, _context_from_scenario(scenario, actor, "survey", 1), {}
            )
            execution = prompts.get_user_prompt(
                actor, _context_from_scenario(scenario, actor, "execution"), {}
            )
            text = (
                f"########## SCENARIO_TYPE={scenario_type}  PRESET={preset}  "
                f"actor={actor}  seed={args.seed} ##########\n\n"
                f"===== SYSTEM =====\n{system}\n\n"
                f"===== USER (planning, round 1) =====\n{planning}\n\n"
                f"===== USER (preliminary vote, private) =====\n{vote}\n\n"
                f"===== USER (execution) =====\n{execution}\n"
            )
            path = out_dir / f"sample_{scenario_type}_{preset}.md"
            path.write_text(text)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
