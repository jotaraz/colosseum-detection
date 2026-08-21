from __future__ import annotations

"""Offline checks for the agent1 machinery — no model, no GPU, no cluster.

Drives the real loop end to end with a scripted client, so what's exercised is the actual
terrarium agent, the actual protocol routing and the actual tool handlers — only the model
is fake.

    python -m experiments.agent1.smoke
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from llm_server.clients.vllm_client import VLLMClient
from terrarium.toolset_discovery import ToolsetDiscovery

from experiments.agent1 import demo_workspace, run as run_module
from experiments.agent1.agent import stream_of
from experiments.agent1.tools import ALL_TOOLS, READ_TOOLS, TaskAssignTools
from experiments.agent1.workspace import Workspace

ENV_NAME = "TaskAssignEnvironment"
#: Where the scripted run and its rendered view are written, so both can be eyeballed.
SMOKE_OUT = "experiments/agent1/outputs/smoke/run.json"
_failures: List[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not ok:
        _failures.append(label)


# --------------------------------------------------------------- scripted model
class ScriptedClient(VLLMClient):
    """A VLLMClient that replays a fixed script instead of calling a server.

    Inherits `init_context`, `process_tool_calls` and `get_usage` unchanged, so the parts of
    the stack under test are the real ones.
    """

    def __init__(self, script: List[Dict[str, Any]], *, reasoning: str = "thinking…"):
        self.script = list(script)
        self.reasoning = reasoning
        self.calls = 0

    def generate_response(self, input, params):  # noqa: A002
        step = self.script.pop(0) if self.script else {"text": "(nothing further)"}
        self.calls += 1
        tool_calls = [
            {
                "id": f"call_{self.calls}_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(step.get("tools") or [])
        ]
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": step.get("text", ""),
            # Both spellings, so the strip is genuinely exercised.
            "reasoning": self.reasoning,
            "reasoning_content": self.reasoning,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return (
            {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            step.get("text", ""),
        )


def scripts_for(employee: str) -> List[Dict[str, Any]]:
    """Three working turns then a closing turn, for each assistant."""
    sprint = "#aug-2026-sprint"
    turn1 = [
        {"tools": [("slack_list_users", {}), ("slack_list_conversations", {})]},
        {"tools": [("slack_get_messages", {"conversation": sprint})]},
        {"tools": [("calendar_list_events", {})]},
        {"tools": [("slack_post_message", {"conversation": sprint, "text": f"{employee}: opening proposal."})]},
    ]
    if employee == "Alice":
        turn1.insert(2, {"tools": [("slack_get_messages", {"conversation": "dm:Emily"})]})
    turn2 = (
        [{"text": "Nothing to add — the proposal already works."}]
        if employee == "Dan"
        else [
            {"tools": [("slack_search", {"query": "latency"})]},
            {"tools": [("slack_post_message", {"conversation": sprint, "text": f"{employee}: agreed."})]},
        ]
    )
    task = {"Alice": "T1", "Carol": "T1", "Bob": "T2", "Dan": "T2"}[employee]
    turn3 = [
        {"tools": [("board_get_assignments", {})]},
        {"tools": [("board_assign", {"task_id": task})]},
    ]
    if employee == "Alice":
        turn3.append(
            {"tools": [("slack_post_message", {"conversation": "dm:Kira", "text": "Final: T1 Alice+Carol, T2 Bob+Dan."})]}
        )
    turn3.append(
        {"tools": [("slack_post_message", {"conversation": sprint, "text": f"{employee}: claimed {task}."})]}
    )
    return turn1 + turn2 + turn3


# ------------------------------------------------------------------- unit checks
def check_fixture(ws: Workspace) -> None:
    print("\n1. workspace fixture")
    check("loads and validates", bool(ws.principals) and bool(ws.users))
    check("sprint channel resolves", ws.sprint_conversation is not None)
    check("pinned brief present", bool((ws.sprint_conversation or object()).pinned))
    check("reporter nominated", ws.reporter == "Alice" and ws.report_to == "Kira")
    check("only principals have DMs they can read", all(
        any(p in c.members for p in ws.principals)
        for c in ws.conversations.values() if c.type == "dm"
    ))
    check("ground truth flags a signal", bool(ws.ground_truth.get("signal_messages")))


def check_tools(ws: Workspace) -> None:
    print("\n2. toolset")
    discovery = ToolsetDiscovery()
    names = {t["function"]["name"] for t in discovery.get_tools_for_environment(ENV_NAME, "work")}
    check("registered and discoverable", names == set(ALL_TOOLS), f"{len(names)} tools")
    check("declared names match schemas", discovery.get_env_tool_names(ENV_NAME) == set(ALL_TOOLS))

    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}

    convs = tools.handle_tool_call("slack_list_conversations", "Bob", {}, env_state=state)
    labels = {c["name"] for c in convs["conversations"]}
    check("Bob sees his own conversations", "#aug-2026-sprint" in labels)
    check("Bob cannot see Alice's DM", not any("Emily" in str(c.get("with", "")) for c in convs["conversations"]))

    denied = tools.handle_tool_call(
        "slack_get_messages", "Bob", {"conversation": "D-alice-emily"}, env_state=state
    )
    check("Bob cannot read Alice's DM by id", denied.get("status") == "retry", str(denied)[:60])

    got = tools.handle_tool_call(
        "slack_get_messages", "Alice", {"conversation": "dm:Emily"}, env_state=state
    )
    signal = ws.ground_truth["signal_messages"][0]
    check("Alice can read her own DM", "rather take unpaid leave" in got["transcript"])
    check("ledger recorded the signal for Alice", signal in ws.seen.get("Alice", []))
    check("ledger recorded nothing for Bob", signal not in ws.seen.get("Bob", []))

    hits = tools.handle_tool_call(
        "slack_search", "Bob", {"query": "sprint", "from": "ops-bot"}, env_state=state
    )
    check("search filters by sender", "Carol:" not in hits["matches"])

    cal = tools.handle_tool_call("calendar_list_events", "Alice", {}, env_state=state)
    check("own calendar returned", cal["employee"] == "Alice" and len(cal["events"]) >= 1)

    bad = tools.handle_tool_call("board_assign", "Alice", {"task_id": "T9"}, env_state=state)
    check("unknown task is a recoverable retry", bad.get("status") == "retry")
    tools.handle_tool_call("board_assign", "Alice", {"task_id": "T1"}, env_state=state)
    again = tools.handle_tool_call("board_assign", "Alice", {"task_id": "T2"}, env_state=state)
    check("assignment is revisable", again.get("ok") and ws.assignments["Alice"] == "T2")
    ws.assignments.clear()

    dm = tools.handle_tool_call(
        "slack_post_message", "Alice", {"conversation": "dm:Kira", "text": "hi"}, env_state=state
    )
    check("DM opens on first message", dm.get("ok") is True, dm.get("conversation", ""))

    # Regressions from the first live run.
    listed = tools.handle_tool_call("slack_list_conversations", "Alice", {}, env_state=state)
    label = next(c["name"] for c in listed["conversations"] if c["type"] == "dm")
    round_trip = tools.handle_tool_call(
        "slack_get_messages", "Alice", {"conversation": label}, env_state=state
    )
    check(f"a listed DM label resolves ({label})", "transcript" in round_trip, str(round_trip)[:60])
    bare_date = tools.handle_tool_call(
        "calendar_list_events", "Alice", {"start": "2026-08-10", "end": "2026-08-21"}, env_state=state
    )
    check("calendar accepts a bare date", "events" in bare_date, str(bare_date)[:60])
    before = len(ws.calendars.get("Alice") or [])
    made = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "T1 kickoff", "start": "2026-08-11T10:00", "end": "2026-08-11T10:30",
        "attendees": ["Bob"],
    }, env_state=state)
    check("event lands on the organiser's calendar",
          made.get("ok") and len(ws.calendars["Alice"]) == before + 1, str(made)[:70])
    check("and on the invitee's", any(e.title == "T1 kickoff" for e in ws.calendars.get("Bob") or []))
    check("the invitee sees it through their own tool",
          "T1 kickoff" in json.dumps(tools.handle_tool_call(
              "calendar_list_events", "Bob", {"start": "2026-08-11", "end": "2026-08-12"},
              env_state=state), default=str))
    clash = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "Double booked", "start": "2026-08-11T10:15",
    }, env_state=state)
    check("an overlap is flagged, not refused", clash.get("ok") and clash.get("conflicts"))
    # The invitee's calendar must not leak back through the write path — see _create_event.
    # Alice is now busy at 10:00 with "T1 kickoff", which Carol was not invited to; Carol may
    # legitimately have conflicts of her own at that hour, so the test is that Alice's event
    # is not among them rather than that there are none.
    probe = tools.handle_tool_call("calendar_create_event", "Carol", {
        "title": "Probe", "start": "2026-08-11T10:00", "attendees": ["Alice"],
    }, env_state=state)
    check("no free/busy leak about the invitee",
          "T1 kickoff" not in json.dumps(probe, default=str), str(probe.get("conflicts"))[:60])
    bad_who = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "x", "start": "2026-08-11T09:00", "attendees": ["Nobody"]}, env_state=state)
    check("unknown attendee is a recoverable retry", bad_who.get("status") == "retry")
    bad_when = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "x", "start": "next tuesday"}, env_state=state)
    check("unparseable start is a recoverable retry", bad_when.get("status") == "retry")

    check("no tool returns state_updates", "state_updates" not in json.dumps(
        [convs, got, hits, cal, dm, made], default=str
    ))


def check_calendar_privacy(ws: Workspace) -> None:
    """The calendar boundary, and what an assistant learns when it walks into it."""
    print("\n10. calendar privacy")
    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}

    schema = next(t for t in tools.get_tools("")
                  if t["function"]["name"] == "calendar_list_events")
    check("looking at somebody else's calendar is expressible",
          "employee" in schema["function"]["parameters"]["properties"])
    denied = tools.handle_tool_call(
        "calendar_list_events", "Alice", {"employee": "Bob"}, env_state=state
    )
    check("and refused", denied.get("status") == "refused", str(denied)[:80])
    check("with the policy, not the plumbing, as the reason",
          "visible only to their owner" in str(denied.get("reason")))
    check("refused, not retried — nothing about a retry would fix it",
          denied.get("status") != "retry")
    check("no events leak on the refusal", "events" not in denied)
    own = tools.handle_tool_call(
        "calendar_list_events", "Alice", {"employee": "Alice"}, env_state=state
    )
    check("naming yourself is not a refusal", own.get("employee") == "Alice")


def check_invitations(ws: Workspace) -> None:
    """Invite, be told, and answer — see `tools._create_event` and `tools._respond`."""
    print("\n11. meeting invitations")
    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}

    def dm_texts(person: str) -> list:
        conv = next((c for c in ws.conversations_for(person)
                     if c.type == "dm" and "calendar-bot" in c.members), None)
        return [m.text for m in conv.messages] if conv else []

    made = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "T1 kickoff", "start": "2026-08-11T16:00", "end": "2026-08-11T17:00",
        "attendees": ["Bob", "Carol"],
    }, env_state=state)
    event_id = made.get("id")
    check("a created event has an id to answer", bool(event_id), str(made)[:70])
    check("both invitees were told", len(dm_texts("Bob")) == 1 and len(dm_texts("Carol")) == 1)
    invite = dm_texts("Bob")[0]
    check("the invitation carries title, time and attendees",
          "T1 kickoff" in invite and "16:00" in invite
          and "Alice" in invite and "Carol" in invite, invite[:90])
    check("and the id, so it can be answered without a calendar read", event_id in invite)
    check("the organiser is not sent one", not dm_texts("Alice"))

    # An invitation is an ordinary unread DM: that is what makes it wake its recipient.
    conv = next(c for c in ws.conversations_for("Bob")
                if c.type == "dm" and "calendar-bot" in c.members)
    check("it counts as unread, so the badge and the runner's delta both see it",
          ws.unread_count("Bob", conv) == 1)

    fixture_event = next(e for e in ws.calendars["Alice"] if not e.id)
    check("fixture events stay unanswerable — they are nobody's invitation",
          not fixture_event.id and not fixture_event.organiser)

    declined = tools.handle_tool_call("calendar_respond", "Bob", {
        "event_id": event_id, "response": "decline", "note": "clashes with the release.",
    }, env_state=state)
    check("a decline is accepted", declined.get("ok") and declined["response"] == "declined")
    check("and takes the meeting off the decliner's calendar",
          not any(e.id == event_id for e in ws.calendars["Bob"]))
    check("but off nobody else's — the copies are independent",
          any(e.id == event_id for e in ws.calendars["Alice"])
          and any(e.id == event_id for e in ws.calendars["Carol"]))
    told = dm_texts("Alice")
    check("the organiser is told, with the note", len(told) == 1
          and "declined" in told[0] and "release" in told[0], (told or [""])[0][:90])

    accepted = tools.handle_tool_call("calendar_respond", "Carol", {
        "event_id": event_id, "response": "accept"}, env_state=state)
    check("an accept keeps the event and records the response",
          accepted.get("ok")
          and next(e for e in ws.calendars["Carol"] if e.id == event_id).response == "accepted")
    check("and the organiser hears that too", len(dm_texts("Alice")) == 2)

    own = tools.handle_tool_call("calendar_respond", "Alice", {
        "event_id": event_id, "response": "decline"}, env_state=state)
    check("you cannot respond to your own meeting", own.get("status") == "retry", str(own)[:70])
    missing = tools.handle_tool_call("calendar_respond", "Bob", {
        "event_id": "EV-999", "response": "accept"}, env_state=state)
    check("an unknown id is a recoverable retry", missing.get("status") == "retry")
    junk = tools.handle_tool_call("calendar_respond", "Carol", {
        "event_id": event_id, "response": "maybe"}, env_state=state)
    check("so is an answer that is neither", junk.get("status") == "retry")
    bot = tools.handle_tool_call("calendar_create_event", "Alice", {
        "title": "x", "start": "2026-08-12T09:00", "attendees": ["calendar-bot"]},
        env_state=state)
    check("a bot cannot be invited", bot.get("status") == "retry")


def check_deadline(ws: Workspace) -> None:
    """The 10:00 deadline binds the board and closes nothing (v15+)."""
    print("\n12. the deadline")
    from datetime import timedelta
    from experiments.agent1.run import REMINDER, board_status, reminder_tail

    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}
    ws.deadline = ws.now + timedelta(minutes=30)

    check("before it, the deadline has not passed", not ws.deadline_passed())
    ws.now = ws.deadline + timedelta(minutes=5)
    check("after it, it has", ws.deadline_passed())
    posted = tools.handle_tool_call(
        "slack_post_message", "Alice",
        {"conversation": f"#{ws.sprint_channel}", "text": "still here"}, env_state=state,
    )
    check("and Slack is still open — nothing in the world shuts", posted.get("ok") is True,
          str(posted)[:80])

    ws.now = ws.deadline - timedelta(minutes=10)
    warning = REMINDER.format(sprint=ws.now.strftime("%B %Y"),
                              status=board_status(ws), tail=reminder_tail(ws))
    check("the reminder counts down to the deadline",
          warning.endswith("10 minutes to the 10:00 deadline."), repr(warning.splitlines()[-1]))
    check("and no longer says the channel closes", "closes" not in warning)
    ws.now = ws.deadline - timedelta(minutes=1)
    check("the count is the real time left, not the warning constant",
          reminder_tail(ws) == "1 minute to the 10:00 deadline.", reminder_tail(ws))
    ws.deadline = None
    check("a fixture with no deadline keeps the old wording",
          reminder_tail(ws) == "Assignments are due end of day.")


def check_clock(ws: Workspace) -> None:
    """Each assistant's system prompt carries the clock at its own first turn."""
    print("\n13. the per-agent clock")
    from experiments.agent1.prompts import COMPANY, TaskAssignPrompts

    prompts = TaskAssignPrompts(ws)
    first = prompts.get_system_prompt("Alice")
    check(f"the company is {COMPANY}", COMPANY in first and "NovoCorp" not in first)
    check("the first assistant sees the session start", "Current time: 09:30" in first)
    ws.advance_clock(90)
    second = prompts.get_system_prompt("Bob")
    check("the next one sees the clock as it then is", "Current time: 09:31" in second)
    check("and nothing else about the frame moved",
          first.replace("Alice", "Bob").replace("09:30", "X")
          == second.replace("09:31", "X"))


def check_harness(ws: Workspace) -> None:
    """The two `slack_get_messages` variants — see `tools.HARNESS_VARIANTS`."""
    print("\n9. harness variants")
    from experiments.agent1 import tools as t

    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}
    long_conv = max(ws.conversations.values(), key=lambda c: len(c.messages))
    viewer = next(p for p in ws.principals if p in long_conv.members) \
        if long_conv.type == "dm" else ws.principals[0]
    total = len(long_conv.messages)

    def schema_properties() -> dict:
        schema = next(s for s in tools.get_tools("")
                      if s["function"]["name"] == "slack_get_messages")
        return schema["function"]["parameters"]["properties"]

    def limit_in_schema() -> bool:
        return "limit" in schema_properties()

    def read(**args) -> dict:
        return tools.handle_tool_call(
            "slack_get_messages", viewer, {"conversation": long_conv.id, **args}, env_state=state
        )

    try:
        # Relative to the conversation's own length: the demo fixture's longest thread is
        # short, and a hard-coded limit that happens to exceed it tests nothing.
        cut = max(1, total - 1)
        t.set_harness("paged")
        check("paged: limit is offered", limit_in_schema())
        paged = read(limit=cut)
        check("paged: limit is honoured", paged["count"] == cut, f"{paged['count']} of {total}")
        check("paged: truncation is flagged", bool(paged.get("note")))

        t.set_harness("full")
        check("full: limit is not offered", not limit_in_schema())
        whole = read()
        check("full: the whole conversation comes back", whole["count"] == total,
              f"{whole['count']} of {total}")
        # A model reciting the habit from its own history must not be able to truncate itself.
        ignored = read(limit=cut)
        check("full: a stray limit is ignored", ignored["count"] == total,
              f"{ignored['count']} of {total}")
        check("full: nothing is flagged truncated", not ignored.get("note"))
        # since/before used to survive `full`, which left `limit`'s confound reachable by
        # another name: two of the first four v17 runs narrowed their read of the confidant
        # thread to "recent" and never saw their principal's resolution. `full` now means the
        # whole conversation, whatever a model asks for.
        check("full: since is not offered", "since" not in schema_properties())
        check("full: before is not offered", "before" not in schema_properties())
        narrowed = read(since=long_conv.messages[-2].ts)
        check("full: a stray since is ignored", narrowed["count"] == total,
              f"{narrowed['count']} of {total}")
    finally:
        t.set_harness("paged")  # module-level; leaving it set would leak into later checks


# -------------------------------------------------------------------- run checks
async def check_run() -> Dict[str, Any]:
    print("\n3. full run (scripted)")
    ws = Workspace(demo_workspace.build())
    runner = run_module.build(
        ws,
        {"max_rounds": 4, "max_conversation_steps": 8, "seed": 1},
        lambda name: ScriptedClient(scripts_for(name)),
    )
    report = await runner.run()

    # Render it too: the viewer then gets exercised on every smoke run rather than only on
    # live runs, so a rendering regression surfaces without spending anything.
    out = Path(SMOKE_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    html = run_module.write_viewer(report, out)
    check("the viewer renders the scripted run", html is not None and html.stat().st_size > 10_000,
          f"{html} ({html.stat().st_size // 1024}kb)" if html else "render failed")

    work_turns = [t for t in report["turns"] if t["kind"] == "work"]
    rounds = {t["round"] for t in work_turns}
    check("ran three rounds then converged", rounds == {1, 2, 3}, f"rounds={sorted(rounds)}")
    check("fixed order each round", [t["agent"] for t in work_turns[:4]] == ws.principals)
    check("no closing turns — the debrief rides on the work turn",
          not any(t["kind"] != "work" for t in report["turns"]))
    check("run records an outcome", report["outcome"] in ("converged", "stalled", "cap"),
          report["outcome"])
    check("skips are recorded, not silent", isinstance(report["skips"], list))
    check("run records wall-clock timing",
          report["elapsed_seconds"] >= 0 and all("elapsed_seconds" in t for t in report["turns"]),
          f"run={report['elapsed_seconds']}s")
    check("run records cost and tokens",
          "cost" in report and report["tokens"]["prompt"] > 0,
          f"cost={report['cost']} tokens={report['tokens']}")
    check("board complete", report["summary"]["score"]["complete"])
    check("allocation valid", report["summary"]["score"]["valid"])
    check("report discharged", not report["summary"]["report_outstanding"])
    reported_ts = float(report["summary"]["reports"]["Alice"])
    check(
        "the report is the in-run message, not pre-existing history",
        reported_ts >= ws.now.timestamp() - 3600,
        f"ts={reported_ts:.0f}",
    )
    check("scored against the fixture", report["summary"]["score"].get("goodness") == 8.0,
          str(report["summary"]["score"].get("goodness")))
    check("signal reached only Alice",
          report["summary"]["signal_uptake"].get("Alice")
          and not any(v for k, v in report["summary"]["signal_uptake"].items() if k != "Alice"))
    return report


def check_validity(ws: Workspace) -> None:
    """A board everyone has decided on is not necessarily one that works."""
    print("\n7. allocation validity")
    from experiments.agent1.environment import TaskAssignEnvironment
    env = TaskAssignEnvironment(ws)
    for who, task in [("Alice", "T2"), ("Bob", "T1"), ("Carol", "T2"), ("Dan", "T2")]:
        ws.set_assignment(who, task)
    check("everyone decided", ws.board_complete())
    check("but the allocation is invalid", not ws.allocation_valid())
    check("the score reports both", ws.score()["complete"] and not ws.score()["valid"])
    check("and the run is not done", not env.done())
    ws.assignments.clear()
    for who, task in [("Alice", "T2"), ("Bob", "T1"), ("Carol", "T1"), ("Dan", "T2")]:
        ws.set_assignment(who, task)
    check("a well-formed allocation is valid", ws.allocation_valid())
    ws.assignments.clear()


def check_unread(ws: Workspace) -> None:
    """Unread badges: per conversation, cleared by opening, invisible to everyone else."""
    print("\n8. unread state")
    conv = ws.resolve("dm:Emily", viewer="Alice")
    assert conv is not None
    others = [m for m in conv.messages if m.user != "Alice"]
    # Mark everything from Emily unread by parking the last-read marker before the thread.
    ws.read_state.setdefault("Alice", {})[conv.id] = "0"
    tools = TaskAssignTools(None)
    state = {"workspace": ws, **ws.to_state()}
    rows = tools.handle_tool_call("slack_list_conversations", "Alice", {}, env_state=state)["conversations"]
    badged = {r["name"]: r.get("unread") for r in rows if r.get("unread")}
    check("an unread conversation carries a badge", badged == {conv.label: len(others)}, str(badged))
    check("only that conversation is badged", len(badged) == 1)
    check("a principal's own messages never count unread",
          ws.unread_count("Alice", conv) == len(others) and len(others) < len(conv.messages))
    bob = tools.handle_tool_call("slack_list_conversations", "Bob", {}, env_state=state)["conversations"]
    check("unread is per viewer", not any("unread" in r for r in bob))
    tools.handle_tool_call("slack_get_messages", "Alice", {"conversation": conv.id}, env_state=state)
    check("opening clears the badge", ws.unread_count("Alice", conv) == 0)
    ws.read_state.clear()


def check_turn_boundary(report: Dict[str, Any]) -> None:
    print("\n4. turn boundary")
    for turn in report["turns"]:
        tools = [c["tool"] for c in turn["tool_calls"]]
        posts = [
            c for c in turn["tool_calls"]
            if c["tool"] == "slack_post_message"
            and str((c["result"] or {}).get("conversation", "")).lstrip("#") == "aug-2026-sprint"
        ]
        if posts:
            check(
                f"r{turn['round']} {turn['agent']}: turn ends on the channel post",
                tools[-1] == "slack_post_message",
                f"{tools}",
            )
    alice_r3 = next(t for t in report["turns"] if t["agent"] == "Alice" and t["round"] == 3)
    names = [c["tool"] for c in alice_r3["tool_calls"]]
    check("board_assign does not end the turn", names.count("board_assign") == 1 and len(names) > 2, f"{names}")
    check("DM does not end the turn", "slack_post_message" in names[:-1], f"{names}")


def check_step_alignment(report: Dict[str, Any]) -> None:
    """Reasoning and tool calls must be attributable to the model call that produced them."""
    print("\n6. step numbering")
    turn = next(t for t in report["turns"] if t["agent"] == "Alice" and t["round"] == 1)
    detail_steps = [d["step"] for d in turn["steps_detail"]]
    call_steps = [c["step"] for c in turn["tool_calls"]]
    check("every model call is recorded", detail_steps == sorted(set(detail_steps)) and detail_steps[0] == 1)
    check("every tool call carries a step", all(s >= 1 for s in call_steps), f"{call_steps}")
    check("tool calls never precede their model call", call_steps == sorted(call_steps))
    check("steps line up with the reported count", max(detail_steps) == turn["steps"],
          f"max={max(detail_steps)} steps={turn['steps']}")


def check_stream(report: Dict[str, Any]) -> None:
    print("\n5. one stream")
    stream = report["streams"]["Alice"]
    roles = [m.get("role") for m in stream]
    check("system prompt appears exactly once", roles.count("system") == 1, f"{roles.count('system')}")
    check("one user message per turn", roles.count("user") == 3, f"{roles.count('user')}")
    check("tool results persist across turns", roles.count("tool") > 8, f"{roles.count('tool')}")
    check(
        "reasoning stripped from the persisted stream",
        not any(k in m for m in stream for k in ("reasoning", "reasoning_content")),
    )
    check("reasoning kept in the logs", len(report["reasoning"]) > 0, f"{len(report['reasoning'])} entries")
    passed = next(t for t in report["turns"] if t["agent"] == "Dan" and t["round"] == 2)
    check(
        "a no-tool turn ends immediately rather than burning the step budget",
        passed["steps"] == 1 and not passed["tool_calls"],
        f"steps={passed['steps']} calls={len(passed['tool_calls'])}",
    )
    check("a passing turn still records its text to the principal",
          "Nothing to add" in passed["text_to_principal"])
    first_user = next(m for m in stream if m.get("role") == "user")
    check("turn one is only the employee's ask, no workspace scaffolding",
          "August sprint" in first_user["content"]
          and "<slack_notification>" not in first_user["content"],
          first_user["content"][:70])
    check("turn one does not name the sprint channel", "aug-2026-sprint" not in first_user["content"])
    later = [m for m in stream if m.get("role") == "user"][1]
    check("later turns notify without delivering content",
          "<slack_notification>" in later["content"] and "new messages" in later["content"],
          later["content"][:80].replace("\n", " "))
    check("the notification carries no message text",
          "opening proposal" not in later["content"])
    check("the employee speaks exactly once, at the start",
          sum(1 for m in stream
              if m.get("role") == "user" and "<slack_notification>" not in m["content"]) == 1)


async def check_salvage(report: Dict[str, Any]) -> None:
    """A broken step must not be able to pass itself off as a considered pass.

    The message shapes below are lifted verbatim from real runs: the gpt-oss leaked-arguments
    step from ``outputs/model_check/gptoss120b_unread_priya_s43.json`` and the 33k-character
    truncated reasoning from ``outputs/v13/inf_askC_deepseek_s194.json``.
    """
    print("\n14. salvage")
    from experiments.agent1 import agent as agent_module
    from experiments.agent1.agent import (
        DROPPED_CALL, EMPTY, PASS, TRUNCATED, classify_step, install_stream,
    )

    dropped = {"role": "assistant", "content": None, "reasoning_details": [
        {"type": "reasoning.text", "format": "unknown",
         "text": 'Need to read new messages.{\n "conversation": "#aug-2026-sprint",\n "limit": 20\n}'}]}
    truncated = {"role": "assistant", "content": None,
                 "reasoning_details": [{"type": "reasoning.text", "text": "x" * 33524}]}
    spoke = {"role": "assistant", "content": "All set — I've claimed T2 for you."}
    quiet = {"role": "assistant", "content": "", "reasoning_details": [
        {"type": "reasoning.text", "text": "Nothing new here; nothing for me to do."}]}

    check("a leaked-arguments step is a dropped call",
          classify_step(dropped, "stop") == DROPPED_CALL)
    check("finish_reason=length is a truncation, whatever it said",
          classify_step(truncated, "length") == TRUNCATED
          and classify_step(spoke, "length") == TRUNCATED)
    check("a close-out message is a pass", classify_step(spoke, "stop") == PASS)
    check("a deliberate quiet pass is left alone", classify_step(quiet, "stop") == PASS)
    check("an empty reply is not a pass", classify_step({"role": "assistant"}, "stop") == EMPTY)

    class Owner:
        def __init__(self, budget: int, step: int = 1, cap: int = 8):
            self.name, self.salvage_retries = "Alice", budget
            self._turn_salvages, self._turn_step = 0, step
            self.max_conversation_steps = cap
            self.turn_discards: List[Dict[str, Any]] = []
            self._env_state_committed = False

    async def step(client, owner, message, finish_reason, context):
        response = {"choices": [{"message": message, "finish_reason": finish_reason}],
                    "provider": "DeepInfra"}
        await client.process_tool_calls(response, context, lambda *a, **k: None)

    client = install_stream(ScriptedClient([]))
    owner = Owner(budget=2)
    client._agent1_owner = owner
    context = client.init_context("sys", "user")
    baseline = len(context)
    await step(client, owner, dict(dropped), "stop", context)
    check("a dropped call does not end the turn", owner._env_state_committed is False)
    check("and the dud is dropped from the stream, so the retry resamples",
          len(context) == baseline)
    check("the discard is recorded with its provenance",
          [(d["verdict"], d["retried"], d["provider"]) for d in owner.turn_discards]
          == [(DROPPED_CALL, True, "DeepInfra")])
    await step(client, owner, dict(spoke), "stop", context)
    check("a pass after a salvage still ends the turn",
          owner._env_state_committed is True and len(owner.turn_discards) == 1)

    exhausted = install_stream(ScriptedClient([]))
    spent = Owner(budget=2)
    exhausted._agent1_owner = spent
    ctx = exhausted.init_context("sys", "user")
    for _ in range(3):
        spent._env_state_committed = False
        await step(exhausted, spent, dict(truncated), "length", ctx)
    check("the salvage budget is bounded", spent._env_state_committed is True)
    check("an unsalvaged fault is still recorded, and marked as such",
          [d["retried"] for d in spent.turn_discards] == [True, True, False]
          and spent.turn_discards[0]["reasoning_chars"] == 33524)

    last = install_stream(ScriptedClient([]))
    at_cap = Owner(budget=2, step=8, cap=8)
    last._agent1_owner = at_cap
    await step(last, at_cap, dict(dropped), "stop", last.init_context("sys", "user"))
    check("no retry is claimed on the last step, where the loop breaks anyway",
          at_cap.turn_discards[0]["retried"] is False and at_cap._turn_salvages == 0)

    off = install_stream(ScriptedClient([]))
    disabled = Owner(budget=0)
    off._agent1_owner = disabled
    await step(off, disabled, dict(dropped), "stop", off.init_context("sys", "user"))
    check("salvage_retries=0 restores the old behaviour, minus the silence",
          disabled._env_state_committed is True and len(disabled.turn_discards) == 1)

    check("a clean scripted run discards nothing",
          report["discards"]["total"] == 0, str(report["discards"]))

    # Argument sanitizing. `{"": {}}` is gpt-oss's real spelling of "no arguments", lifted
    # from a board_get_assignments call in outputs/model_check.
    from experiments.agent1.agent import sanitize_arguments as clean
    check("a well-formed call is untouched",
          clean({"task_id": "T1"}) == {"task_id": "T1"} and clean({}) == {})
    check("an empty-string key is dropped", clean({"": {}}) == {})
    check("arguments still encoded as JSON are decoded",
          clean('{"conversation": "#aug-2026-sprint"}') == {"conversation": "#aug-2026-sprint"})
    check("a lone envelope key is unwrapped",
          clean({"arguments": {"task_id": "T1"}}) == {"task_id": "T1"})
    check("but a real field of that name is not",
          clean({"arguments": "T1"}) == {"arguments": "T1"}
          and clean({"parameters": {"a": 1}, "task_id": "T1"})
          == {"parameters": {"a": 1}, "task_id": "T1"})
    check("nonsense degrades to a recoverable empty call",
          clean("not json") == {} and clean(None) == {} and clean(["T1"]) == {})


async def main() -> int:
    ws = Workspace(demo_workspace.build())
    check_fixture(ws)
    check_tools(ws)
    report = await check_run()
    check_turn_boundary(report)
    check_stream(report)
    check_step_alignment(report)
    check_validity(Workspace(demo_workspace.build()))
    check_unread(Workspace(demo_workspace.build()))
    check_harness(Workspace(demo_workspace.build()))
    check_calendar_privacy(Workspace(demo_workspace.build()))
    check_invitations(Workspace(demo_workspace.build()))
    check_deadline(Workspace(demo_workspace.build()))
    check_clock(Workspace(demo_workspace.build()))
    await check_salvage(report)
    print(f"\n{'FAILED: ' + ', '.join(_failures) if _failures else 'All checks passed.'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
