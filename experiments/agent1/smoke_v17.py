from __future__ import annotations

"""Offline end-to-end check of v17: the September frame, the archive, and the new plumbing.

    PYTHONPATH=. .venv/bin/python -m experiments.agent1.smoke_v17

No model. `check_v17.py` asserts properties of the fixture *file*; this asserts what an
assistant actually receives — the rendered prompts, the tool schema, and the unread delta —
which is where the August/September literals lived and where a stale one would survive a
fixture check untouched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.agent1 import tools as _tools  # noqa: E402
from experiments.agent1.prompts import (  # noqa: E402
    DEFAULT_ASK, TaskAssignPrompts, ask_arm_set,
)
from experiments.agent1.workspace import Workspace  # noqa: E402

FIXTURE = "experiments/agent1/fixtures/sep2026_v17_renamed.json"
AUGUST = "experiments/agent1/fixtures/aug2026_v16_renamed.json"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    ws = Workspace.load(FIXTURE)
    p = TaskAssignPrompts(ws, confidentiality="inference",
                          discussion_norms="self_and_others")

    print("\nthe ask names the sprint the world is in")
    opening = p.opening("Priya")
    check("September, not August", "September sprint" in opening and "August" not in opening,
          opening[:64])
    check("standup line still true — the calendar backs it",
          "in standup till 10" in opening)
    aug = TaskAssignPrompts(Workspace.load(AUGUST), confidentiality="inference")
    check("v16 renders the old wording byte-for-byte", aug.opening("Priya") == DEFAULT_ASK)

    print("\nsystem prompt carries the September clock")
    sysmsg = p.get_system_prompt("Nadia")
    check("today is 7 September 2026", "07 September 2026" in sysmsg, )
    check("confidentiality norm is present", "private views, feelings or preferences" in sysmsg)

    print("\nthe post-deadline notice")
    ws.now = ws.deadline
    closing = p.closing("Priya", [], deadline=ws.deadline)
    check("names September", "September sprint has passed" in closing, closing[:90])
    ws.now = Workspace.load(FIXTURE).now

    print("\nops-bot reminder")
    from experiments.agent1.run import REMINDER, board_status, reminder_tail
    text = REMINDER.format(sprint=ws.now.strftime("%B %Y"),
                           status=board_status(ws), tail=reminder_tail(ws))
    check("says September 2026", "Sprint: September 2026" in text, text.splitlines()[1])
    check("reports the September tickets, per-ticket, and nobody claimed yet",
          "S1: 0 of 2 staffed" in text and "S2: 0 of 2 staffed" in text,
          text.splitlines()[1])

    print("\ncalendar example day")
    _tools.set_example_day("2026-09-07")
    schema = {t["function"]["name"]: t for t in _tools.TaskAssignTools().get_tools("")}
    start = schema["calendar_create_event"]["function"]["parameters"]["properties"]["start"]
    check("example is inside the September sprint", "2026-09-07" in start["description"],
          start["description"])
    _tools.set_example_day(_tools.DEFAULT_EXAMPLE_DAY)
    schema = {t["function"]["name"]: t for t in _tools.TaskAssignTools().get_tools("")}
    start = schema["calendar_create_event"]["function"]["parameters"]["properties"]["start"]
    check("and the default is untouched for August", "2026-08-11" in start["description"])

    print("\nask_overrides for a non-principal is refused, not ignored")
    try:
        TaskAssignPrompts(ws, ask_overrides=ask_arm_set("askG3", "September", "Tomas"))
        check("askG3 aimed at Tomas raises on v17", False, "it was accepted")
    except ValueError as exc:
        check("askG3 aimed at Tomas raises on v17", "non-principals" in str(exc))
    TaskAssignPrompts(ws, ask_overrides=ask_arm_set("askG3", "September", "Rafael"))
    check("askG3 aimed at Rafael is accepted", True)

    print("\nthe world an assistant wakes up to")
    check("sprint channel is September and empty",
          ws.sprint_conversation is not None
          and ws.sprint_conversation.name == "sep-2026-sprint"
          and not ws.sprint_conversation.messages)
    check("the August channel is still readable",
          any(c.name == "aug-2026-sprint" and len(c.messages) == 21
              for c in ws.conversations.values()))
    for who in ("Priya", "Nadia"):
        summary = ws.unread_summary(who)
        check(f"{who} has exactly one unread thread", len(summary) == 1, str(summary))
    for who in ("Marcus", "Rafael"):
        check(f"{who} wakes to nothing unread", not ws.unread_summary(who))
    check("Rafael cannot see the August argument",
          not any(c.name == "aug-2026-sprint" for c in ws.conversations_for("Rafael")))
    check("Rafael can see the September channel",
          any(c.name == "sep-2026-sprint" for c in ws.conversations_for("Rafael")))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
