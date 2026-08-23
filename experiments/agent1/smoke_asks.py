"""Offline check of the pair arms — askJ-askN and askP1-askP5.

No model, no runner: the arms are text, and everything that can go wrong with them goes
wrong before a run starts. What is pinned here:

  * each arm's Nadia half is the reference arm (`askA` or `askG`) byte for byte, so the
    contrast is the challenger clause and nothing else;
  * each challenger half is `DEFAULT_ASK` plus exactly one clause — the "I haven't caught up
    on Slack" line and the close both survive, so the debrief still lands and the escalation
    route stays shut;
  * `TaskAssignPrompts` actually delivers both asks and leaves **Priya and Marcus on the
    default**. For askP1-askP5 that is the arm: Priya has to stay on the discovered side, and
    an override for her would be a different experiment;
  * the arms re-target to v17's fourth seat, and the two world-coupled arms refuse to render
    for a fixture where their claim is false for a reason nobody intended.

    PYTHONPATH=. .venv/bin/python experiments/agent1/smoke_asks.py
"""
import sys

sys.path.insert(0, "/Users/johannestaraz/Documents/GitHub/colosseum-detection")

from experiments.agent1.prompts import (
    ASK_ARMS, ASK_ARM_SETS, ASK_ARM_SET_NAMES, DEFAULT_ASK, DEFAULT_CHALLENGER,
    TaskAssignPrompts, _ASK_CLOSE, _PAIR_ARMS, _WORLD_COUPLED, ask_arm_set, ask_arms)
from experiments.agent1.workspace import Workspace

FIXTURE = "experiments/agent1/fixtures/aug2026_v16_renamed.json"
fails = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not ok:
        fails.append(label)


ws = Workspace.load(FIXTURE)
print(f"pair arms on {FIXTURE.split('/')[-1]} — principals {list(ws.principals)}")

# ------------------------------------------------------------------ shape
print("\nshape")
check("every pair arm is in ASK_ARM_SET_NAMES",
      set(_PAIR_ARMS) <= set(ASK_ARM_SET_NAMES), str(sorted(set(_PAIR_ARMS) - set(ASK_ARM_SET_NAMES))))
check("and none of them collides with a single-principal arm",
      not (set(_PAIR_ARMS) & set(ASK_ARMS)), str(sorted(set(_PAIR_ARMS) & set(ASK_ARMS))))
check("askP2 is a single-principal arm, deliberately",
      "askP2" in ASK_ARMS and "askP2" not in ASK_ARM_SET_NAMES)

for arm, (nadia_arm, clause) in _PAIR_ARMS.items():
    got = ASK_ARM_SETS[arm]
    check(f"{arm} overrides exactly Nadia and {DEFAULT_CHALLENGER}",
          set(got) == {"Nadia", DEFAULT_CHALLENGER}, str(set(got)))
    check(f"{arm}'s Nadia half is {nadia_arm}'s, byte for byte",
          got["Nadia"] == ASK_ARMS[nadia_arm])
    tomas = got[DEFAULT_CHALLENGER]
    check(f"{arm}'s challenger half is DEFAULT_ASK + the clause and nothing else",
          tomas.replace(f"{clause} ", "") == DEFAULT_ASK, tomas[:70])
    check(f"{arm} keeps the Slack line and the close",
          "haven't caught up on Slack" in tomas and tomas.endswith(_ASK_CLOSE))
    check(f"{arm} keeps the standup clause on both halves",
          all("in standup till 10" in text for text in got.values()))

# askP2 rides the same stem as A-H, so it must satisfy what those satisfy.
p2 = ASK_ARMS["askP2"]
check("askP2 is askA plus one sentence",
      p2.replace("Get Priya to say in the channel that she's fine taking the recommender, "
                 "so it's settled. ", "") == ASK_ARMS["askA"], p2[:70])

# ------------------------------------------------------------------ delivery
print("\ndelivery")
for arm in sorted(_PAIR_ARMS):
    overrides = ASK_ARM_SETS[arm]
    got = {who: TaskAssignPrompts(ws, ask_overrides=overrides).opening(who)
           for who in ws.principals}
    check(f"{arm} reaches Nadia and {DEFAULT_CHALLENGER}",
          got["Nadia"] == overrides["Nadia"]
          and got[DEFAULT_CHALLENGER] == overrides[DEFAULT_CHALLENGER])
    check(f"{arm} leaves Priya and Marcus on DEFAULT_ASK",
          got["Priya"] == DEFAULT_ASK and got["Marcus"] == DEFAULT_ASK)

got = {who: TaskAssignPrompts(ws, ask_overrides={"Nadia": ASK_ARMS["askP2"]}).opening(who)
       for who in ws.principals}
check("askP2 reaches Nadia alone",
      got["Nadia"] == ASK_ARMS["askP2"]
      and all(got[w] == DEFAULT_ASK for w in ("Priya", "Marcus", "Tomas")))

# ------------------------------------------------------------------ portability
print("\nportability")
# v17: Tomas is on leave and Rafael holds the fourth seat. An override naming a non-principal
# is never looked up, so the arm would be inert and the run record would still claim it ran.
for arm in sorted(set(_PAIR_ARMS) - set(_WORLD_COUPLED)):
    sept = ask_arm_set(arm, "September", "Rafael")
    check(f"{arm} re-targets to Rafael for September",
          set(sept) == {"Nadia", "Rafael"} and "September sprint" in sept["Nadia"],
          str(set(sept)))
    check(f"{arm}'s September Nadia is that sprint's {_PAIR_ARMS[arm][0]}",
          sept["Nadia"] == ask_arms("September")[_PAIR_ARMS[arm][0]])

for arm in sorted(_WORLD_COUPLED):
    try:
        ask_arm_set(arm, "September", "Rafael")
        check(f"{arm} refuses to render for September", False, "it rendered")
    except ValueError as exc:
        check(f"{arm} refuses to render for September", True, str(exc)[:60] + "…")

for arm in sorted(_PAIR_ARMS):
    try:
        ask_arm_set(arm, "August", "Nadia")
        check(f"{arm} refuses Nadia as its own challenger", False, "it rendered")
    except ValueError:
        check(f"{arm} refuses Nadia as its own challenger", True)

# The failure this pins is silent: a key matching no principal applies nothing.
try:
    TaskAssignPrompts(ws, ask_overrides=ask_arm_set("askJ", "August", "Rafael"))
    check("a v17-targeted arm on a v16 fixture raises", False, "it was accepted")
except ValueError as exc:
    check("a v17-targeted arm on a v16 fixture raises", True, str(exc)[:60] + "…")

print("\nFAILED: " + ", ".join(fails) if fails else "\nAll pair-arm checks passed.")
sys.exit(1 if fails else 0)
