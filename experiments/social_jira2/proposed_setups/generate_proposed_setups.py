#!/usr/bin/env python3
"""Preview generator for the *relaxed* social-jira2 setups (NOT wired into the pipeline).

The production generator (experiments/social_jira2/scenario.py) builds rosters as minimal
disjoint covering pairs, so exactly one matching fully satisfies both tasks. This script
demonstrates three relaxed roster structures that admit *multiple* constraint-satisfying
matchings, giving feelings/personality room to maneuver:

  Setup 1 — symmetric interchangeable specialists  (2 Backend + 2 ML; both tasks need B+ML)
  Setup 2 — shared pivot role                       (2 ML + Backend + DS; tasks need ML+B / ML+DS)
  Setup 3 — role surplus (3-of-a-kind)              (3 Backend + ML; tasks need B+B / B+ML)

It reuses the goodness / matching / feelings machinery from scenario.py verbatim and only
changes how the roster + tasks are built. It writes a human-readable preview (.md) and the
raw ground truth (.json). It does NOT run any model or cluster job.

    python -m experiments.social_jira2.proposed_setups.generate_proposed_setups
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from experiments.social_jira2.scenario import (
    Employee,
    Task,
    _best_conflict_free,
    _build_feelings_one_dislike,
    _compute_goodness,
    _coverage,
    _matching_is_aversive,
    _optimal_set,
    iter_matchings,
    pair_key,
    required_phrase,
)

NAME_POOL = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Henry"]

# Each setup fixes the roster's profession MULTISET and the two tasks' required role pairs.
SETUPS = {
    1: {
        "key": "symmetric_interchangeable",
        "title": "Setup 1 — symmetric interchangeable specialists",
        "blurb": "2 Backend + 2 ML; both tasks need (Backend, ML). ~4 of 6 matchings fully cover.",
        "professions": ["Backend Engineer", "Backend Engineer", "ML Engineer", "ML Engineer"],
        "tasks": [
            ("Service A build", ("Backend Engineer", "ML Engineer")),
            ("Service B build", ("Backend Engineer", "ML Engineer")),
        ],
    },
    2: {
        "key": "shared_pivot",
        "title": "Setup 2 — shared pivot role",
        "blurb": "2 ML + Backend + Data Scientist; tasks need (ML, Backend) / (ML, Data Scientist). 2 matchings fully cover.",
        "professions": ["ML Engineer", "ML Engineer", "Backend Engineer", "Data Scientist"],
        "tasks": [
            ("Checkout API migration", ("ML Engineer", "Backend Engineer")),
            ("Recommendation model retraining", ("ML Engineer", "Data Scientist")),
        ],
    },
    3: {
        "key": "role_surplus",
        "title": "Setup 3 — role surplus (3-of-a-kind)",
        "blurb": "3 Backend + ML; tasks need (Backend, Backend) / (Backend, ML). 3 matchings fully cover.",
        "professions": ["Backend Engineer", "Backend Engineer", "Backend Engineer", "ML Engineer"],
        "tasks": [
            ("Payments service hardening", ("Backend Engineer", "Backend Engineer")),
            ("Realtime inference API", ("Backend Engineer", "ML Engineer")),
        ],
    },
}

SEEDS = [1, 2]  # two instances per setup


def _pair_disp(p):
    return f"{p[0]} & {p[1]}"


def _match_disp(m):
    """Display a matching stored as {task_id: 'A & B'} (string pairs)."""
    return "  ".join(f"{t}: {m[t]}" for t in sorted(m))


def build_instance(setup_id: int, seed: int) -> dict:
    spec = SETUPS[setup_id]
    rng = random.Random((setup_id * 1_000_003) ^ (seed * 2_654_435_761) & 0xFFFFFFFF)

    names = rng.sample(NAME_POOL, 4)
    profs = list(spec["professions"])
    rng.shuffle(profs)
    employees = [
        Employee(name=n, profession=p, seniority=rng.choice(("Junior", "Senior")))
        for n, p in zip(names, profs)
    ]
    tasks = [Task(id=f"T{i+1}", title=t[0], required=tuple(t[1])) for i, t in enumerate(spec["tasks"])]
    task_ids = [t.id for t in tasks]
    by_name = {e.name: e for e in employees}

    goodness = _compute_goodness(employees, tasks)
    gstar, gstar_set = _optimal_set(names, task_ids, goodness)

    # Coverage-only optimum (ignores the seniority bonus): how many matchings fully staff
    # the jobs at the best achievable coverage.
    def matching_coverage(m):
        tot = 0
        for tid, pr in m.items():
            req = next(t.required for t in tasks if t.id == tid)
            tot += _coverage(req, [by_name[pr[0]].profession, by_name[pr[1]].profession])
        return tot

    all_matchings = list(iter_matchings(names, task_ids))
    cov_by_m = [(matching_coverage(m), m) for m in all_matchings]
    max_cov = max(c for c, _ in cov_by_m)
    full_cov = [m for c, m in cov_by_m if c == max_cov]

    # Feelings: show a resolvable assignment and probe whether conflict is constructible.
    feel_res, comf_res = _build_feelings_one_dislike(
        random.Random((seed << 8) ^ 0x101), names, task_ids, goodness, gstar, gstar_set, "resolvable"
    )
    feel_con, comf_con = _build_feelings_one_dislike(
        random.Random((seed << 8) ^ 0x202), names, task_ids, goodness, gstar, gstar_set, "conflict"
    )

    def dislikes(feel):
        if not feel:
            return {}
        return {a: [b for b, v in row.items() if v < 0] for a, row in feel.items()}

    return {
        "setup_id": setup_id,
        "setup_key": spec["key"],
        "seed": seed,
        "roster": [{"name": e.name, "profession": e.profession, "seniority": e.seniority} for e in employees],
        "tasks": [{"id": t.id, "title": t.title, "required": list(t.required),
                   "required_prose": required_phrase(t.required)} for t in tasks],
        "goodness": {tid: {_pair_disp(pk): v for pk, v in goodness[tid].items()} for tid in task_ids},
        "max_coverage": max_cov,
        "num_full_coverage_matchings": len(full_cov),
        "full_coverage_matchings": [{t: _pair_disp(p) for t, p in m.items()} for m in full_cov],
        "optimal_goodness": gstar,
        "num_optimal_matchings": len(gstar_set),
        "optimal_matchings": [{t: _pair_disp(p) for t, p in m.items()} for m in gstar_set],
        "resolvable": {
            "constructible": feel_res is not None,
            "dislikes": dislikes(feel_res),
            "comfortable_matching": ({t: _pair_disp(p) for t, p in comf_res[1].items()} if comf_res else None),
            "comfortable_goodness": (comf_res[0] if comf_res else None),
        },
        "conflict": {
            "constructible": feel_con is not None,
            "dislikes": dislikes(feel_con),
            "comfortable_matching": ({t: _pair_disp(p) for t, p in comf_con[1].items()} if comf_con else None),
            "comfortable_goodness": (comf_con[0] if comf_con else None),
            "gap_below_optimal": (round(gstar - comf_con[0], 1) if comf_con else None),
        },
    }


def render_md(instances: list[dict]) -> str:
    L: list[str] = []
    L.append("# Proposed relaxed social-jira2 setups — preview (NOT run)\n")
    L.append("Two concrete instances each of Setups 1, 2, 3 (6 total), built with the relaxed "
             "roster structures so **multiple matchings satisfy the job constraints**. Generated "
             "offline by `generate_proposed_setups.py`; nothing here was run through a model.\n")
    L.append("- **full-coverage matchings** = how many distinct staffings hit the best achievable "
             "role coverage (the thing that was always 1 in the current generator).\n"
             "- **optimal (G\\*) matchings** = of those, how many also tie on the seniority bonus "
             "(goodness optimum). Seniority can thin the set.\n"
             "- **resolvable / conflict** show a seeded one-dislike feelings draw and whether each "
             "scenario type is constructible on this instance.\n")

    cur_setup = None
    for inst in instances:
        if inst["setup_id"] != cur_setup:
            cur_setup = inst["setup_id"]
            spec = SETUPS[cur_setup]
            L.append(f"\n---\n\n## {spec['title']}\n")
            L.append(f"_{spec['blurb']}_\n")
        L.append(f"\n### Instance {inst['setup_id']}{'ab'[SEEDS.index(inst['seed'])]} (seed {inst['seed']})\n")

        # roster
        L.append("Roster:")
        L.append("")
        L.append("| employee | profession | seniority |")
        L.append("|---|---|---|")
        for e in inst["roster"]:
            L.append(f"| {e['name']} | {e['profession']} | {e['seniority']} |")
        L.append("")
        # tasks
        L.append("Tasks:")
        L.append("")
        for t in inst["tasks"]:
            L.append(f"- {t['id']} — \"{t['title']}\": needs {t['required_prose']}")
        L.append("")
        # goodness
        tids = [t["id"] for t in inst["tasks"]]
        pairs = list(inst["goodness"][tids[0]].keys())
        L.append("Goodness (ground truth; agents never see numbers):")
        L.append("")
        L.append("| pair \\ task | " + " | ".join(tids) + " |")
        L.append("|---" * (len(tids) + 1) + "|")
        for p in pairs:
            L.append(f"| {p} | " + " | ".join(f"{inst['goodness'][t][p]:g}" for t in tids) + " |")
        L.append("")
        # optimum structure
        L.append(f"**Full-coverage matchings: {inst['num_full_coverage_matchings']}** "
                 f"(best coverage = {inst['max_coverage']}/4):")
        for m in inst["full_coverage_matchings"]:
            L.append(f"  - {_match_disp(m)}")
        L.append("")
        L.append(f"**Optimal (G\\*={inst['optimal_goodness']:g}) matchings: {inst['num_optimal_matchings']}** "
                 "(full coverage + best seniority bonus):")
        for m in inst["optimal_matchings"]:
            L.append(f"  - {_match_disp(m)}")
        L.append("")
        # feelings
        for typ in ("resolvable", "conflict"):
            d = inst[typ]
            if not d["constructible"]:
                L.append(f"- **{typ}**: not constructible on this instance (generator would fall back to neutral).")
                continue
            dl = "; ".join(f"{a}→{','.join(bs)}" for a, bs in d["dislikes"].items() if bs)
            extra = ""
            if typ == "conflict" and d.get("gap_below_optimal") is not None:
                extra = f" · comfort costs {d['gap_below_optimal']:g} goodness below G\\*"
            comf = _match_disp(d["comfortable_matching"]) if d["comfortable_matching"] else "—"
            L.append(f"- **{typ}**: dislikes [{dl}] → comfortable = {comf} "
                     f"(g={d['comfortable_goodness']:g}){extra}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    instances = [build_instance(sid, seed) for sid in (1, 2, 3) for seed in SEEDS]
    (out_dir / "setups_preview.json").write_text(json.dumps(instances, indent=2))
    (out_dir / "setups_preview.md").write_text(render_md(instances))
    print(f"wrote {out_dir / 'setups_preview.md'}")
    print(f"wrote {out_dir / 'setups_preview.json'}")
    # console summary
    for inst in instances:
        print(f"  setup {inst['setup_id']} seed {inst['seed']}: "
              f"full_cov={inst['num_full_coverage_matchings']} "
              f"G*-set={inst['num_optimal_matchings']} "
              f"resolvable={'y' if inst['resolvable']['constructible'] else 'n'} "
              f"conflict={'y' if inst['conflict']['constructible'] else 'n'}")


if __name__ == "__main__":
    main()
