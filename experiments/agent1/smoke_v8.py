"""Offline end-to-end check of the v8 deadline: warning, close, refusal, final turns.

Drives the real Runner with a scripted client that only ever posts, so the clock is the only
thing that can end the run.
"""
import asyncio, json, sys
sys.path.insert(0, "/Users/johannestaraz/Documents/GitHub/colosseum-detection")

from experiments.agent1 import run as run_module
from experiments.agent1.smoke import ScriptedClient  # the same fake model the suite uses
from experiments.agent1.workspace import Workspace

FIXTURE = "experiments/agent1/fixtures/aug2026_v8_renamed.json"
fails = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not ok:
        fails.append(label)


def chatty(name):
    """A client that posts to the sprint channel every turn and never stops."""
    # Every step posts, so a turn always ends on the step cap and always leaves an event for
    # the others: the conversation can never go quiet, and the clock is the only way out.
    step = {"tools": [("slack_post_message", {"conversation": "#aug-2026-sprint",
                                              "text": f"{name}: still talking."})]}
    return ScriptedClient([step] * 400)


settings = {
    "max_rounds": 50,          # backstop only; the clock must be what ends this
    "max_conversation_steps": 4,
    "turn_seconds": 90, "turn_jitter": 0, "seed": 1,
    "confidentiality": "mindful", "discussion_norms": "off",
    "harness": "full", "start_with": "Priya",
    "workspace": FIXTURE, "model_name": "scripted",
}
runner = run_module.build(FIXTURE, settings, chatty)
report = asyncio.run(runner.run())

ws = runner.ws
turns = report["turns"]
work = [t for t in turns if t["kind"] == "work"]
closing = [t for t in turns if t["kind"] == "closing"]
sprint = report["transcript"]["#aug-2026-sprint"]
warnings = [m for m in sprint if m["from"] == "ops-bot" and "10 minutes" in m["text"]]

print("\nv8 deadline machinery")
check("outcome is `deadline`", report["outcome"] == "deadline", report["outcome"])
check("record carries the deadline", report["deadline"]["at"] == "2026-08-10T10:00:00",
      str(report["deadline"]))
check("no work turn starts at or after 10:00",
      all(t["clock"] < "2026-08-10T10:00:00" for t in work),
      f"last work turn {work[-1]['clock'][11:16]}")
check("the last work turn is close to the wire",
      work[-1]["clock"] >= "2026-08-10T09:57:00", work[-1]["clock"][11:16])
check("the round cap did not end it", report["config"]["max_rounds"] == 50
      and len({t["round"] for t in work}) < 50, f"{len({t['round'] for t in work})} rounds used")

check("warning fired exactly once", len(warnings) == 1, str(len(warnings)))
if warnings:
    check("warning lands in the 09:50-10:00 window", "09:5" in warnings[0]["time"],
          warnings[0]["time"])
    check("warning names the deadline and the close",
          "10:00" in warnings[0]["text"] and "closes" in warnings[0]["text"])
    check("warning names no principal",
          not any(p in warnings[0]["text"] for p in ws.principals), warnings[0]["text"])
    check("warning woke everyone (all four acted after it)",
          len({t["agent"] for t in work if t["clock"][11:16] >= warnings[0]["time"][-5:]}) == 4)

check("one closing turn per principal", len(closing) == len(ws.principals),
      f"{len(closing)} of {len(ws.principals)}")
check("closing turns are after the close",
      all(t["clock"] >= "2026-08-10T10:00:00" for t in closing))
check("closing prompt says the window shut",
      all("closed at 10:00" in (t["message_in"] or "") for t in closing))

refused = [c for t in closing for c in (t["tool_calls"] or [])
           if c["tool"] == "slack_post_message"
           and isinstance(c.get("result"), dict) and c["result"].get("status") == "retry"]
check("posts after the close are refused", len(refused) >= 1,
      f"{len(refused)} refused; e.g. {refused[0]['result']['reason'][:60] if refused else '-'}")
check("nothing was appended to the channel after 10:00",
      all(m["time"] < "Mon 10 Aug 10:00" for m in sprint), sprint[-1]["time"])

print("\nFAILED: " + ", ".join(fails) if fails else "\nAll v8 checks passed.")
sys.exit(1 if fails else 0)
