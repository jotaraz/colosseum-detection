"""Compare 5e8 deception-judge labels with the human labels (policy v1).

    .venv/bin/python -m experiments.agent5.deception_judge5e8_compare [plain cot] [-v]

Human labels: ``outputs/5e8_seats_human_labeled_policyv1.md`` column 5 (``goes for T1`` -> T1,
``sends no message`` -> NOMSG; other free-text verdicts are skipped). With both variants given,
also prints how the CoT judge moves each plain label (FABR/FRAM -> UNACK/NOTH etc.).
"""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs" / "5e8_deception_judges"
LABELS = ["FABR", "FRAM", "UNACK", "NOTH", "T1", "NOMSG"]

H = {}
for line in (HERE / "outputs" / "5e8_seats_human_labeled_policyv1.md").read_text().splitlines():
    c = [x.strip() for x in line.split("|")]
    if len(c) > 6 and c[2].startswith("s") and c[1] != "model":
        v = {"goes for T1": "T1", "sends no message": "NOMSG"}.get(c[5].strip("*`").strip(), c[5].strip("*`").strip())
        if v in LABELS:
            H[(f"{c[1]}_{c[2]}", c[3])] = v


def load(variant, rep=1):
    d = {}
    for f in (OUT / variant).glob("*.json"):
        r = json.loads(f.read_text())
        if "result" in r and r.get("replicate", 1) == rep:
            d[(r["run"], r["seat"])] = r["result"]
    return d


def replicates(variant):
    return sorted({json.loads(f.read_text()).get("replicate", 1) for f in (OUT / variant).glob("*.json")})


def main(argv):
    verbose = "-v" in argv
    variants = [a for a in argv if a in ("plain", "cot")] or ["plain", "cot"]
    reps = {v: replicates(v) for v in variants if (OUT / v).exists()}
    for v, rs in reps.items():
        if len(rs) > 1:   # replicate stability + majority vote
            by = {r: load(v, r) for r in rs}
            common = set.intersection(*(set(d) for d in by.values()))
            same = sum(len({by[r][k]["label"] for r in rs}) == 1 for k in common)
            print(f"== {v}: replicates {rs}; {len(common)} seats judged in all; "
                  f"identical label in every replicate: {same}/{len(common)}\n")
    # one label per seat: the majority across replicates (ties -> replicate 1)
    got = {}
    for v in variants:
        if v not in reps:
            continue
        by = {r: load(v, r) for r in reps[v]}
        got[v] = {}
        for k in set().union(*by.values()):
            labs = [by[r][k]["label"] for r in reps[v] if k in by[r]]
            top = max(set(labs), key=lambda x: (labs.count(x), -labs.index(x)))
            got[v][k] = next(by[r][k] for r in reps[v] if k in by[r] and by[r][k]["label"] == top)
    for v, d in got.items():
        rows = [(k, H[k], r["label"], r) for k, r in sorted(d.items()) if k in H]
        n = len(rows)
        if not n:
            print(f"== {v}: no judged seats with a human label"); continue
        ag = sum(h == m for _, h, m, _ in rows)
        fb = sum((h == "FABR") == (m == "FABR") for _, h, m, _ in rows)
        print(f"== {v}: {len(d)} judged, {n} with human label; exact {ag}/{n} = {ag/n:.0%}; FABR-vs-not {fb}/{n} = {fb/n:.0%}")
        cm = collections.Counter((h, m) for _, h, m, _ in rows)
        print("rows=human, cols=judge\n       " + "".join(f"{x:>7}" for x in LABELS))
        for h in LABELS[:3] + LABELS[3:]:
            print(f"{h:>7}" + "".join(f"{cm[(h, m)]:>7}" for m in LABELS))
        if verbose:
            for (run, seat), h, m, r in rows:
                if h != m:
                    print(f"\n{run} {seat}: human={h} judge={m} ({r.get('rule')}, {r.get('decisive_time')})"
                          f"\n  \"{(r.get('decisive_quote') or '')[:200]}\"\n  {r.get('why', '')[:300]}")
        print()
    if len(got) == 2:
        both = sorted(set(got["plain"]) & set(got["cot"]))
        cm = collections.Counter((got["plain"][k]["label"], got["cot"][k]["label"]) for k in both)
        print(f"== plain -> cot on {len(both)} common seats\n       " + "".join(f"{x:>7}" for x in LABELS))
        for a in LABELS:
            print(f"{a:>7}" + "".join(f"{cm[(a, b)]:>7}" for b in LABELS))


if __name__ == "__main__":
    main(sys.argv[1:])
