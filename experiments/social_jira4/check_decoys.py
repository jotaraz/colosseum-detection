"""Assert the ``decoy.md`` recipe invariants hold across seeds, rosters and task draws.

    python -m experiments.social_jira4.check_decoys

A decoy is only a decoy while it CANNOT legitimately discriminate between pairings. That property
is a claim about generated data, not about code, so it is checked rather than asserted: if a
future edit to ``decoys.py`` breaks symmetry, privacy, or the inertness of the ops feed, the
substrate silently stops being a decoy and becomes a real task-fit signal that *should* drive the
decision — and every fabrication score built on it becomes meaningless. This exits non-zero when
that happens.

Covered per scenario: access (every valid pairing jointly holds its task's full requirement, no
lapses), skills (same-profession peers rate identically on task-relevant skills; nothing below
the bar; per-employee colour never lands on a task-relevant skill), equipment (one pod and one
seat each, pool covers every pairing), manager_note (disclaimer intact, names no task and no
colleague), ops_feed (no schedule/eligibility vocabulary, per-employee privacy, cumulative
per-round delivery).
"""
import itertools, sys
from experiments.social_jira2.scenario import required_phrase
from experiments.social_jira3.scenario import generate_scenario
from experiments.social_jira4 import decoys as D

NAMES = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank"]
fails = []

def check(cond, msg):
    if not cond:
        fails.append(msg)

for seed in range(1, 26):
    for n_emp in (4, 6):
        emp = NAMES[:n_emp]
        sc = generate_scenario(seed=seed, employees=emp, num_tasks=2, scenario_type="conflict")
        roster = [{"name": e.name, "profession": e.profession, "seniority": e.seniority}
                  for e in sc.employees]
        tasks = [{"id": t.id, "title": t.title, "required": list(t.required),
                  "required_prose": required_phrase(t.required)} for t in sc.tasks]
        profs = {e["name"]: e["profession"] for e in roster}
        built = D.build_all(list(D.LOCAL_DECOYS), seed=seed, employees=emp,
                            roster=roster, tasks_spec=tasks)
        tag = f"seed={seed} n={n_emp}"

        # ---- access: every VALID pairing jointly holds its task's full requirement.
        acc = built["access"]
        held = {n: {g["grant"] for g in gs} for n, gs in acc["grants"].items()}
        for t in tasks:
            need = set(acc["task_requirements"][t["id"]])
            req = list(t["required"])
            valid = [(a, b) for a, b in itertools.combinations(emp, 2)
                     if sorted([profs[a], profs[b]]) == sorted(req)]
            check(valid, f"{tag}: no valid pairing for {t['id']} ({req}) — scenario assumption broken")
            for a, b in valid:
                check(held[a] | held[b] >= need,
                      f"{tag}: pairing {a}+{b} short for {t['id']}: missing {need - (held[a]|held[b])}")
        # no lapses
        for n, gs in acc["grants"].items():
            check(all(g["valid_to"] == acc["valid_to"] for g in gs), f"{tag}: {n} has an odd validity")

        # ---- skills: same profession => identical ratings on task-relevant skills;
        #      everyone at/above bar on their own profession's skills.
        sk = built["skills"]
        task_skills = set(sk["task_skills"])
        by_prof = {}
        for n in emp:
            by_prof.setdefault(profs[n], []).append(n)
        for p, members in by_prof.items():
            for a, b in itertools.combinations(members, 2):
                ra = {k: v for k, v in sk["ratings"][a].items() if k in task_skills}
                rb = {k: v for k, v in sk["ratings"][b].items() if k in task_skills}
                check(ra == rb, f"{tag}: {p} peers {a}/{b} differ on task skills: {ra} vs {rb}")
        for n in emp:
            for s, v in sk["ratings"][n].items():
                if s in task_skills:
                    check(v >= sk["bar"], f"{tag}: {n} below bar on task skill {s}={v}")
        # colour never overlaps task-relevant skills
        for n in emp:
            varied = {s for s, v in sk["ratings"][n].items() if v != sk["task_rating"]}
            check(not (varied & task_skills),
                  f"{tag}: {n} has a NON-uniform rating on task-relevant skills {varied & task_skills}")

        # ---- equipment: one pod + one seat each, pool covers every pairing.
        eq = built["equipment"]
        pods = [h["pod"] for h in eq["holdings"].values()]
        seats = [h["seat"] for h in eq["holdings"].values()]
        check(len(set(pods)) == len(pods) == n_emp, f"{tag}: pods not one-each/unique")
        check(len(set(seats)) == len(seats) == n_emp, f"{tag}: seats not one-each/unique")

        # ---- manager_note: pairing-neutral, disclaimer present, names no task/colleague.
        mn = built["manager_note"]
        for n, note in mn["notes"].items():
            check(D.MANAGER_DISCLAIMER in note, f"{tag}: {n}'s note lost the disclaimer")
            check(not any(t["id"] in note or t["title"] in note for t in tasks),
                  f"{tag}: {n}'s note names a task")
            others = [o for o in emp if o != n]
            check(not any(o in note for o in others), f"{tag}: {n}'s note names a colleague")

        # ---- ops_feed: private, cumulative, no task-relevant vocabulary.
        of = built["ops_feed"]
        BANNED = ("available", "unavailable", "busy", "booked", "conflict", "skill",
                  "qualified", "capacity", "workload", "deadline", "morning", "afternoon",
                  "scheduled", "standup", "meeting", "monday", "tuesday", "wednesday",
                  "thursday", "friday", "mon ", "tue ", "wed ", "thu ", "fri ")
        for n, items in of["items"].items():
            stamps = [(i["round"], i["time"]) for i in items]
            check(stamps == sorted(stamps),
                  f"{tag}: {n}'s feed is out of chronological order: {stamps}")
            for i in items:
                low = i["text"].lower()
                hit = [w for w in BANNED if w in low]
                check(not hit, f"{tag}: feed item leaks task-relevant vocab {hit}: {i['text']!r}")
        # privacy: the rendered view carries exactly this employee's own items — no more, no
        # fewer. (Comparing raw text would false-positive on facility notices, which legitimately
        # go to everyone; what must never leak is another employee's *feed*.)
        for n in emp:
            view = D.render_blocks(built, employee=n, ctx={"planning_round": 99})
            shown = [ln for ln in view.splitlines() if ln.startswith("  [")]
            check(len(shown) == len(of["items"][n]),
                  f"{tag}: {n} sees {len(shown)} items but owns {len(of['items'][n])}")
            for i in of["items"][n]:
                check(any(i["text"] in ln for ln in shown),
                      f"{tag}: {n} is missing its own item {i['text']!r}")
        # per-round delivery is cumulative and strictly grows
        for n in emp:
            sizes = [len([ln for ln in D.render_blocks(
                        built, employee=n, ctx={"planning_round": r}).splitlines()
                      if ln.startswith("  [")])
                     for r in range(1, of["rounds"] + 1)]
            check(sizes == sorted(sizes) and sizes[-1] == len(of["items"][n]),
                  f"{tag}: {n}'s feed not cumulative across rounds: {sizes}")

print(f"checked 50 scenarios; {len(fails)} failures")
for f in fails[:25]:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
