from __future__ import annotations

"""Render sample social-jira3 prompts offline (no terrarium / no model needed).

Builds a scenario with placeholder employees, attaches the inbox, then renders the system
prompt and one agent's blocks for each axis variant. Useful for eyeballing the inbox prose,
the naturalized system prompt (confidentiality axis), the briefing arm, and the summary turn.

    python -m experiments.social_jira3.render_sample_prompts
    python -m experiments.social_jira3.render_sample_prompts --employees 4 --tasks 2 --seed 2
    python -m experiments.social_jira3.render_sample_prompts --structured       # block library (prompt_blocks.md)
    python -m experiments.social_jira3.render_sample_prompts --structured --structured-name prompt_blocks_v2.md
"""

import argparse
from pathlib import Path

from experiments.social_jira3.inbox import ALL_STRENGTHS
from experiments.social_jira3.prompts import SocialJiraPrompts
from experiments.social_jira3.scenario import (
    attach_availability,
    attach_inboxes,
    generate_scenario,
    required_phrase,
    scenario_dislikes,
)


def _context_from_scenario(scenario, inboxes, agent_name, phase, planning_round=None,
                           *, channel="inbox", availability=None):
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
    if channel == "briefing":
        signal = next((m.text for m in inboxes[agent_name] if m.is_signal and m.thread_id == "signal"), None)
        ctx["signal_text"] = signal
    else:
        ctx["inbox"] = [
            {"thread_id": m.thread_id, "counterpart": m.counterpart,
             "direction": m.direction, "time": m.time, "text": m.text}
            for m in inboxes[agent_name]
        ]
    if availability is not None:
        ctx["my_calendar"] = {
            "free_slots": list((availability.get("free_slots") or {}).get(agent_name, [])),
            "slot_hours": availability.get("slot_hours"),
            "task_joint_hours": availability.get("task_joint_hours"),
        }
    if planning_round is not None:
        ctx["planning_round"] = planning_round
    return ctx


def render_structured_doc(seed, employees, num_tasks, out_name="prompt_blocks.md") -> str:
    """One reference doc defining each prompt building block once + an assembly map.

    Blocks are pulled straight from the ``_*_block`` methods so the doc tracks ``prompts.py``
    exactly. Inbox variants are re-generated per strength (the strength lives in the message
    text, not the instructions — SPEC §2.2).
    """
    scenario = generate_scenario(
        seed=seed, employees=employees, num_tasks=num_tasks, scenario_type="conflict"
    )
    actor = employees[0]
    tasks = list(scenario.task_ids)
    disliked = scenario_dislikes(scenario)[actor]

    def prompts_for(confidentiality="audience", channel="inbox", audience="employee"):
        return SocialJiraPrompts(
            env=None, full_config={}, feelings_channel=channel, confidentiality=confidentiality,
            summary_audience=audience, log_prompts=False,
        )

    pd = prompts_for()  # defaults: inbox / confidentiality audience / employee / decoys on
    inboxes_by_strength = {
        s: attach_inboxes(scenario, dislike_strength=s) for s in ALL_STRENGTHS
    }
    inboxes = inboxes_by_strength["quit"]  # default strength
    availability = attach_availability(scenario)

    ctx = _context_from_scenario(scenario, inboxes, actor, "planning", 1,
                                 availability=availability)
    ctx_vote = _context_from_scenario(scenario, inboxes, actor, "survey", 1)
    ctx_exec = _context_from_scenario(scenario, inboxes, actor, "execution")
    ctx_sum = _context_from_scenario(scenario, inboxes, actor, "summary")

    other = employees[1] if len(employees) > 1 else actor
    ctx_committed = dict(ctx)
    ctx_committed["assignment"] = {actor: tasks[0], other: tasks[0]}
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
        "# social-jira3 prompt building blocks",
        "",
        f"_Generated from `prompts.py` by `render_sample_prompts.py --structured --structured-name {out_name}` — do not edit by hand._",
        "",
        "## Legend",
        "- `#` / `##` lines are headings of THIS document (outside the prompt).",
        "- `=== ... ===` lines are real separators **inside** the prompt (sent to the model verbatim).",
        "- `{{TOKEN}}` is a placeholder: insert the block defined under that token's heading.",
        f"- Concrete values below use seed={seed}, actor={actor} (dislikes {disliked}), "
        "scenario_type=conflict. resolvable vs conflict change ONLY the filled-in values, "
        "never the structure. Defaults shown: channel=inbox, strength=quit, confidentiality=audience, "
        "audience=employee, decoys=on; each axis's other levels appear as variants under its "
        "block. The inbox is last-day two-way conversation threads with NO relation labels "
        "(relations live only in ground truth).",
        "",
        "## What changed vs jira2 (so the diffs are deliberate, SPEC §3)",
        "- No CONFIDENTIALITY block, no red line, no avoidance directive anywhere: the only",
        "  discretion text is the confidentiality norm — a bare paragraph in the USER prompt, right",
        "  after the personal-messages block (moved out of the system prompt in prompt_version 5).",
        "  The `confidentiality` axis picks the MECHANISM (none / audience / stakes / self /",
        "  leverage / duty / integrity / consent); every treatment norm ends on 'the group does",
        "  expect a reason for your stance'.",
        "- The system prompt states the INFORMATION STRUCTURE at ALL confidentiality levels:",
        "  each assistant sees only its own employee's private info — a world-model",
        "  clarification that never mentions feelings, so confidentiality=none stays pure.",
        "- jira2's system-prompt sentence \"Your employee's private feelings about colleagues",
        "  are known only to you\" is dropped — under confidentiality=none nothing in the prompt",
        "  may hint that feelings exist.",
        "- The vote block's \"including your employee's private feelings\" is naturalized to",
        "  \"using everything you know\" for the same reason.",
        "",
        "# Assembly — which blocks make each message",
        "```",
        "System message      = {{SYSTEM_PROMPT}}   (shared core; IDENTICAL at every confidentiality/hint level)",
        "User (planning)     = {{WHO}} + {{TASKS}} + {{AVAILABILITY}} + {{INBOX}} + {{CONFIDENTIALITY}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{COORDINATE}}",
        "User (prelim. vote) = {{WHO}} + {{TASKS}} + {{INBOX}} + {{CONFIDENTIALITY}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{VOTE}}",
        "User (execution)    = {{WHO}} + {{TASKS}} + {{INBOX}} + {{CONFIDENTIALITY}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{COMMIT}}",
        "User (summary)      = {{WHO}} + {{TASKS}} + {{AVAILABILITY}} + {{INBOX}} + {{CONFIDENTIALITY}} + {{COMMITMENTS}} + {{DISCUSSION}} + {{SUMMARY}}",
        "```",
        "(`{{AVAILABILITY}}` sits between `{{TASKS}}` and `{{INBOX}}` in EVERY message and is",
        "omitted when decoys=off; abbreviated above only to keep the lines readable.)",
        "`{{CONFIDENTIALITY}}` (SPEC §3.2) sits right after the personal-messages block in EVERY user",
        "message and is empty when confidentiality=none. Under `feelings_channel: briefing`, `{{INBOX}}`",
        "is replaced by `{{BRIEFING}}` in every message (and `{{CONFIDENTIALITY}}` follows it). Only the",
        "final block differs across phases; `{{DISCUSSION}}` accumulates each round and is omitted while",
        "empty. The summary turn runs once, after execution, with the final commitments and the full",
        "discussion in context. Blocks are joined by blank lines.",
        "",
        "# ==================== BLOCK LIBRARY ====================",
        "",
    )

    from experiments.social_jira3.prompts import (
        CONFIDENTIALITY_MODES, build_confidentiality_norm,
        _CONFIDENTIALITY_BODIES, _HINT_SMALL, _HINT_BIG_EXAMPLE,
        _HINT_NOCONSTRAINT_EXAMPLE, _REASON_CLAUSE,
    )
    from experiments.social_jira3.inbox import object_pronoun
    # The hint's worked example names the agent's own employee (disliker) + disliked colleague.
    _ex = dict(disliker=actor, disliked=disliked, obj=object_pronoun(disliked))
    _conf_desc = {
        "none": "no norm paragraph — the user prompt has no confidentiality clause (pure may-or-may-not-infer)",
        "audience": "default: audience-harm norm (relational harm; the readers are the employees)",
        "duty": "fiduciary/professional-secrecy norm (licenses not-at-liberty-to-share)",
        "self": "audited-channel enforcement (flagged + removed; no good-reason exception)",
        "stakes": "concrete-harm norm (leaking is dangerous to your employee, not merely embarrassing)",
        "leverage": "amoral/instrumental (disclosure is tactically bad — it weakens your position)",
        "integrity": "virtue/character (a trustworthy assistant does not betray a confidence)",
        "consent": "autonomy/ownership (the info is the employee's; disclosing OR hinting removes their control)",
    }
    _hint_desc = {
        "none": "base norm only",
        "small": "+ 'no one can infer or suspect a personal motive'",
        "big": "+ that, plus a worked example of an inference-leaking phrasing (descriptive framing)",
        "noconstraint": "+ that, plus the same example as a direct prohibition (imperative framing)",
    }
    # SYSTEM_PROMPT = the SHARED CORE, now IDENTICAL at every confidentiality/hint level: from
    # prompt_version 5 the confidentiality norm moved OUT of the system prompt into the user
    # prompt (see {{CONFIDENTIALITY}} below), so nothing here varies with the axis.
    core = prompts_for(confidentiality="none").get_system_prompt()
    block("{{SYSTEM_PROMPT}}",
          "shared core — IDENTICAL at every confidentiality/hint level (from v5 it no longer "
          "carries the confidentiality norm; that moved to the user prompt)",
          core)
    add("# {{CONFIDENTIALITY}}  _the `confidentiality` x `hint` axes (SPEC §3.2) — a USER-prompt paragraph right after the personal messages_",
        "", "_From prompt_version 5 this is a bare paragraph in the USER prompt, placed directly "
        "after `{{INBOX}}`/`{{BRIEFING}}` (the personal messages) — no `=== HEADER ===`, to keep it "
        "naturalized. 29 valid tuples: (none,none) + {audience,stakes,self,leverage,duty,integrity,"
        "consent} x {none,small,big,noconstraint}. Rather than print all 29, each mechanism body and "
        "each hint clause is defined ONCE below, followed by the rule that merges them._", "",
        "## How the paragraph is assembled", "",
        "```",
        "{{CONFIDENTIALITY}} = <mechanism body>  [+ ' ' + <hint clause>]  + ' ' + <reason clause>",
        "```",
        "- Take the **mechanism body** for the `confidentiality` level. If it is `none` there is "
        "NO paragraph at all (the `hint` is ignored) and the user prompt goes straight from the "
        "personal messages to `{{COMMITMENTS}}`.",
        "- If `hint` != none, append its **hint clause**.",
        "- Always finish with the **reason clause**.",
        "",
        "The parts are joined with single spaces into one paragraph, inserted into the user prompt "
        "right after the personal-messages block.",
        "",
        f"The hint's worked example (big/noconstraint) NAMES the agent's own employee ({actor}) and "
        f"their disliked colleague ({disliked}) — filled per agent from its private info, NOT fixed "
        "placeholders (prompt_version 5).",
        "",
        "## Mechanism bodies — the `confidentiality` axis (pick one)", "")
    for lvl in CONFIDENTIALITY_MODES:
        body = _CONFIDENTIALITY_BODIES[lvl]
        add(f"### confidentiality={lvl}  ({_conf_desc.get(lvl, '')})", "",
            body if body else "_(no body — no paragraph is emitted; `hint` is ignored)_", "")

    add("## Hint clauses — the `hint` axis (appended only when confidentiality != none)", "")
    add(f"### hint=none  ({_hint_desc['none']})", "", "_(nothing appended)_", "")
    add(f"### hint=small  ({_hint_desc['small']})", "", _HINT_SMALL, "")
    add(f"### hint=big  ({_hint_desc['big']})", "",
        "Everything in `hint=small` above, then this worked example appended:", "",
        _HINT_BIG_EXAMPLE.format(**_ex), "")
    add(f"### hint=noconstraint  ({_hint_desc['noconstraint']})", "",
        "Everything in `hint=small` above, then this worked example appended:", "",
        _HINT_NOCONSTRAINT_EXAMPLE.format(**_ex), "")

    add("## Reason clause — always appended last, at every treatment level", "",
        _REASON_CLAUSE, "")

    add(f"## Worked example — confidentiality=self, hint=big, {actor} (disliker) & {disliked} (disliked)", "",
        build_confidentiality_norm("self", "big", disliker=actor, disliked=disliked), "")

    add("# {{WHO}}  _personality=none shown; other personalities append one sentence (jira2 registry)_", "")
    variant("none", pd._who_block(actor))
    add("")
    block("{{TASKS}}", "role-driven; jira2 verbatim; values are scenario-specific",
          pd._tasks_block(ctx))

    block("{{AVAILABILITY}}", "PRIVATE calendar decoy (decoys=on): the assistant sees ONLY its own employee's slots; ground truth guarantees equal pairwise overlap (SPEC §2.5a); omitted when decoys=off",
          pd._availability_block(ctx))

    add("# {{INBOX}}  _last-day conversation threads; the `dislike_strength` axis lives in the signal wording (SPEC §2); the dislike appears in n_signal_threads=2 conversations (primary + a strength-matched secondary mention); relations are NOT labeled; secrecy_request=on (default) appends the employee's 'keep this between us' request to the PRIMARY signal message_", "")
    for strength in ALL_STRENGTHS:
        sctx = _context_from_scenario(
            scenario, inboxes_by_strength[strength], actor, "planning", 1
        )
        variant(f"dislike_strength={strength}" + (" (default)" if strength == "quit" else ""),
                pd._inbox_block(actor, sctx))

    add("# {{BRIEFING}}  _feelings_channel=briefing: same signal text, delivered directly (SPEC §2.5)_", "")
    for strength in ALL_STRENGTHS:
        bctx = _context_from_scenario(
            scenario, inboxes_by_strength[strength], actor, "planning", 1, channel="briefing"
        )
        variant(f"dislike_strength={strength}" + (" (default)" if strength == "quit" else ""),
                prompts_for(channel="briefing")._briefing_block(actor, bctx))

    add("# {{COMMITMENTS}}  _runtime: depends on who has committed_", "")
    variant("opening (nobody committed yet)", pd._state_block(ctx))
    variant("later (some have committed)", pd._state_block(ctx_committed))

    block("{{DISCUSSION}}", "runtime: the shared blackboard so far; omitted while empty",
          pd._discussion_block(discussion_example))

    block("{{COORDINATE}}", "planning instruction; single variant (no control fork in v3)",
          pd._instruction_block("planning", ctx))
    block("{{VOTE}}", "preliminary-vote instruction; private; jira2 minus the feelings clause",
          pd._instruction_block("survey", ctx_vote))
    block("{{COMMIT}}", "execution instruction; jira2 verbatim",
          pd._instruction_block("execution", ctx_exec))

    add("# {{SUMMARY}}  _the `summary_audience` axis lives here (SPEC §4)_", "")
    variant("summary_audience=employee (default: faithfulness probe)",
            prompts_for(audience="employee")._summary_block(ctx_sum))
    variant("summary_audience=manager (third discretion surface)",
            prompts_for(audience="manager")._summary_block(ctx_sum))

    return "\n".join(L).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--employees", type=int, default=4)
    parser.add_argument("--tasks", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--structured", action="store_true",
                        help="emit a single block-library reference (prompt_blocks.md)")
    parser.add_argument("--structured-name", default="prompt_blocks.md",
                        help="filename for the --structured output")
    parser.add_argument("--out", default="experiments/social_jira3/sample_prompts")
    args = parser.parse_args()

    employees = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Henry"][: args.employees]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.structured:
        doc = render_structured_doc(
            args.seed, employees, args.tasks, out_name=args.structured_name
        )
        path = out_dir / args.structured_name
        path.write_text(doc)
        print(f"wrote {path}")
        return

    for scenario_type in ("resolvable", "conflict"):
        scenario = generate_scenario(
            seed=args.seed, employees=employees, num_tasks=args.tasks,
            scenario_type=scenario_type,
        )
        inboxes = attach_inboxes(scenario)  # default strength (quit)
        availability = attach_availability(scenario)
        actor = employees[0]
        for channel in ("inbox", "briefing"):
            prompts = SocialJiraPrompts(
                env=None, full_config={}, feelings_channel=channel, log_prompts=False
            )
            system = prompts.get_system_prompt()
            planning = prompts.get_user_prompt(
                actor,
                _context_from_scenario(scenario, inboxes, actor, "planning", 1, channel=channel, availability=availability),
                {},
            )
            vote = prompts.get_user_prompt(
                actor,
                _context_from_scenario(scenario, inboxes, actor, "survey", 1, channel=channel, availability=availability),
                {},
            )
            execution = prompts.get_user_prompt(
                actor,
                _context_from_scenario(scenario, inboxes, actor, "execution", channel=channel, availability=availability),
                {},
            )
            summary = prompts.get_user_prompt(
                actor,
                _context_from_scenario(scenario, inboxes, actor, "summary", channel=channel, availability=availability),
                {},
            )
            text = (
                f"########## SCENARIO_TYPE={scenario_type}  CHANNEL={channel}  "
                f"strength=quit  confidentiality=audience  audience=employee  decoys=on  "
                f"actor={actor}  seed={args.seed} ##########\n\n"
                f"===== SYSTEM =====\n{system}\n\n"
                f"===== USER (planning, round 1) =====\n{planning}\n\n"
                f"===== USER (preliminary vote, private) =====\n{vote}\n\n"
                f"===== USER (execution) =====\n{execution}\n\n"
                f"===== USER (closing summary, private) =====\n{summary}\n"
            )
            path = out_dir / f"sample_{scenario_type}_{channel}.md"
            path.write_text(text)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
