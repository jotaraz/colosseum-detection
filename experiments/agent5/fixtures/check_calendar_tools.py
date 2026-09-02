"""Exercise the four calendar tools against the w1 world. No model, no runner, no ports.

``@tanager_mcp.tool()`` leaves the function itself in the module namespace, so each tool is
called directly with a stub Context — the same path a request takes once ``_uid`` has read
the ``X-Agent-Name`` header off it.

    .venv/bin/python experiments/agent5/fixtures/check_calendar_tools.py

(the project venv, not a bare python3: importing the server pulls in uvicorn and mcp)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from experiments.agent5 import slack_server as S  # noqa: E402
from experiments.agent5.slack_world import SlackWorld  # noqa: E402

FAILURES = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def ctx(name: str):
    """A Context carrying the agent's identity the way _uid reads it: ctx.headers, lowercased."""
    return SimpleNamespace(headers={"x-agent-name": name})


def call(tool, who, **kw):
    return json.loads(tool(ctx=ctx(who), **kw))


def dms_from_bot(to: str):
    bot = S.W.uid_by_name["calendar-bot"]
    rcpt = S.W.uid_by_name[to]
    conv = next((c for c in S.W.convs.values()
                 if c.get("is_im") and set(c["members"]) == {bot, rcpt}), None)
    return [m["text"] for m in (conv or {}).get("messages", [])]


def main() -> None:
    fixture = HERE / "tanager_slack_w1P0N0.json"
    os.environ["TZ"] = json.loads(fixture.read_text()).get("tz") or "Europe/Berlin"
    time.tzset()
    S.W = SlackWorld.load(fixture)
    S.NOTIFICATIONS = []
    # _run logs every call to OUT/world_calls.jsonl; the server sets OUT from --out.
    S.OUT = Path(tempfile.mkdtemp(prefix="w1-calendar-check-"))
    tools = {t: getattr(S, t) for t in
             ("calendar_list_events", "calendar_create_event", "calendar_respond",
              "calendar_cancel_event")}

    print("calendar tool surface")
    check("four calendar tools exist", len(tools) == 4)

    print("\ncalendar_list_events")
    r = call(tools["calendar_list_events"], "Priya")
    check("reads your own calendar", r["employee"] == "Priya" and r["events"])
    r = call(tools["calendar_list_events"], "Priya", employee="Nadia")
    check("refuses someone else's calendar", r.get("status") == "refused", str(r)[:120])

    print("\ncalendar_create_event")
    r = call(tools["calendar_create_event"], "Matthieu", title="T1 kickoff",
             start="2026-09-07 16:00", end="2026-09-07 16:30", attendees=["Nadia"])
    eid = r.get("id")
    check("organiser creates and gets an id", r.get("ok") and eid, str(r)[:120])
    check("event lands on the invitee's calendar",
          any(e.get("id") == eid for e in S.W.calendars["Nadia"]))
    check("invitee is told by calendar-bot",
          any("invited you" in t and "T1 kickoff" in t for t in dms_from_bot("Nadia")))

    print("\ncalendar_cancel_event — permissions")
    r = call(tools["calendar_cancel_event"], "Nadia", event_id=eid)
    check("an attendee cannot cancel someone else's meeting",
          r.get("status") == "refused" and "Matthieu" in r.get("reason", ""), str(r)[:140])
    check("refusal points at the tool that would work",
          "calendar_respond" in r.get("reason", ""))
    r = call(tools["calendar_cancel_event"], "Matthieu", event_id="EV-999")
    check("unknown id is a retry, not a crash", r.get("status") == "retry", str(r)[:120])
    standup = next(e for e in S.W.calendars["Priya"] if e["title"] == "Standup")
    check("fixture events carry no id, so they cannot be cancelled", "id" not in standup)

    print("\ncalendar_cancel_event — effect")
    before = len(dms_from_bot("Nadia"))
    r = call(tools["calendar_cancel_event"], "Matthieu", event_id=eid,
             note="Clash with the on-call handover.")
    check("organiser can cancel", r.get("ok") and r["cancelled_for"] == ["Matthieu", "Nadia"],
          str(r)[:140])
    check("gone from the organiser's calendar",
          not any(e.get("id") == eid for e in S.W.calendars["Matthieu"]))
    check("gone from the attendee's calendar",
          not any(e.get("id") == eid for e in S.W.calendars["Nadia"]))
    msgs = dms_from_bot("Nadia")
    check("attendee is told it was cancelled", len(msgs) == before + 1 and "cancelled" in msgs[-1],
          msgs[-1] if msgs else "no DM")
    check("the note reaches the attendee verbatim",
          "Clash with the on-call handover." in msgs[-1], msgs[-1])

    print("\ncancellation reaches an attendee who had already declined")
    r = call(tools["calendar_create_event"], "Rafael", title="T2 kickoff",
             start="2026-09-07 16:00", end="2026-09-07 16:30", attendees=["Priya"])
    eid2 = r["id"]
    call(tools["calendar_respond"], "Priya", event_id=eid2, response="decline")
    check("declining removes it from the decliner's calendar",
          not any(e.get("id") == eid2 for e in S.W.calendars["Priya"]))
    n_before = len(dms_from_bot("Priya"))
    r = call(tools["calendar_cancel_event"], "Rafael", event_id=eid2)
    check("cancel still succeeds after a decline", r.get("ok"), str(r)[:120])
    check("the decliner is still told of the cancellation",
          len(dms_from_bot("Priya")) == n_before + 1 and "cancelled" in dms_from_bot("Priya")[-1])

    print("\nsolo event")
    r = call(tools["calendar_create_event"], "Priya", title="Focus", start="2026-09-08 09:00")
    eid3 = r["id"]
    r = call(tools["calendar_cancel_event"], "Priya", event_id=eid3)
    check("a solo event cancels with nobody to notify",
          r.get("ok") and r["cancelled_for"] == ["Priya"], str(r)[:120])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
