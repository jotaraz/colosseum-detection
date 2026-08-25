"""jv10 (per step) vs jv11 (per turn) on the frozen sample — the comparison the pair exists for.

    .venv/bin/python -m experiments.agent2.deception_compare [--md <path>]

Four questions, in the order they should be asked:

1. **Test-retest.** Do two replicates of the SAME setting agree? An across-version difference
   smaller than a within-version one is noise, so this bounds everything below it.
2. **jv10 vs jv11.** On the same 91 turns, with jv10's steps collapsed to turns (a turn is
   deception if any of its steps carried a finding). Reported per model, and as a confusion
   table, because "14 vs 11" can hide two disjoint sets of 14 and 11.
3. **By sample group.** `a1_hit` / `a3_full` / `a3_extra` carry jv9 lie labels; `a1_unjudged`
   has none. The a3_full group is the only one with a within-rollout denominator, so its rate
   is the only one that is a *rate*.
4. **Where claims die.** The `excluded` histogram (vague / belief / slip / audience / goal) is
   how the gates are actually behaving — gate 5 (`goal`) and gate 1's `vague` are new, and if
   they never fire the prompt changes did nothing.

Evidence quality is reported alongside, not folded in: a finding whose `cot_final_stance`
resolves `elsewhere` or `not-found` rests on a quote the judge did not take from the reasoning
(`todo_jv10`'s evidence_ok item). Counted, never silently dropped.
"""

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from experiments.agent2 import quotes  # noqa: E402

SAMPLE = Path(__file__).with_name("deception_sample.json")


def load(sample):
    """(version, model, replicate) -> {(run, turn): {...}} with jv10's steps collapsed."""
    got = collections.defaultdict(dict)
    for run in sorted({s["run"] for s in sample}):
        for f in glob.glob(str(REPO / run)[:-len(".json")] + ".deception_jv1*_*.json"):
            d = json.loads(Path(f).read_text())
            key = (d.get("judge_version"), d.get("judge", "?").split(":")[-1],
                   int(d.get("replicate") or 1))
            for r in d.get("turns") or []:
                cell = got[key].setdefault((run, r["turn_index"]),
                                           {"n": 0, "units": 0, "excluded": collections.Counter(),
                                            "spec": collections.Counter(),
                                            "goal": collections.Counter(),
                                            "instr": collections.Counter(), "bad_ev": 0})
                cell["units"] += 1
                cell["n"] += int(r.get("n_lies") or 0)
                for e in r.get("excluded") or []:
                    cell["excluded"][e.get("failed_gate") or "?"] += 1
                for x in r.get("findings") or []:
                    cell["spec"][x.get("specificity")] += 1
                    cell["goal"][x.get("serves_goal")] += 1
                    cell["instr"][x.get("cot_instrumental")] += 1
                    checks = (x.get("evidence") or {}).get("cot_final_stance_checks") or []
                    if checks and not any(c.get("status") in quotes.FOUND for c in checks):
                        cell["bad_ev"] += 1
    return got


def _pair(a, b):
    keys = sorted(set(a) & set(b))
    tt = ff = tf = ft = 0
    for k in keys:
        x, y = bool(a[k]["n"]), bool(b[k]["n"])
        tt += x and y; ff += (not x) and (not y); tf += x and not y; ft += y and not x
    agree = f"{100 * (tt + ff) / len(keys):.0f}%" if keys else "-"
    return len(keys), tt, ff, tf, ft, agree


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="jv10 vs jv11 on the deception sample")
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--md", default=None, help="also write the report as markdown")
    args = ap.parse_args(argv)
    sample = json.loads(Path(args.sample).read_text())
    meta = {(s["run"], s["turn_index"]): s for s in sample}
    got = load(sample)
    complete = {k: v for k, v in got.items() if len(v) == len(sample)}
    out = []
    p = lambda s="": (print(s), out.append(s))

    p(f"# jv10 vs jv11 — {len(sample)} Priya turns "
      f"({sum(1 for s in sample if (s['jv9_votes'] or 0) >= 2)} with >=2/3 jv9 lie)")
    p(f"\ncomplete settings: {len(complete)} of {len(got)}")

    p("\n## 1. Test-retest (replicate 1 vs 2, same version+model)")
    p("\n| version | model | turns | both | neither | only r1 | only r2 | agree |")
    p("|---|---|---:|---:|---:|---:|---:|---:|")
    for (ver, mod) in sorted({(k[0], k[1]) for k in complete}):
        a, b = complete.get((ver, mod, 1)), complete.get((ver, mod, 2))
        if a and b:
            n, tt, ff, tf, ft, ag = _pair(a, b)
            p(f"| {ver} | {mod} | {n} | {tt} | {ff} | {tf} | {ft} | {ag} |")

    p("\n## 2. jv10 vs jv11 (same model, same replicate; jv10 steps collapsed to turns)")
    p("\n| model | rep | turns | both | neither | jv10 only | jv11 only | agree |")
    p("|---|---:|---:|---:|---:|---:|---:|---:|")
    for mod in sorted({k[1] for k in complete}):
        for rep in (1, 2):
            a, b = complete.get(("jv10", mod, rep)), complete.get(("jv11", mod, rep))
            if a and b:
                n, tt, ff, tf, ft, ag = _pair(a, b)
                p(f"| {mod} | {rep} | {n} | {tt} | {ff} | {tf} | {ft} | {ag} |")

    p("\n## 3. Deception rate by sample group")
    groups = ["a1_hit", "a1_unjudged", "a3_full", "a3_extra"]
    sizes = collections.Counter(s["group"] for s in sample)
    p("\n| version | model | rep | " + " | ".join(f"{g} (n={sizes[g]})" for g in groups) + " |")
    p("|---|---|---:|" + "---:|" * len(groups))
    for key in sorted(complete, key=str):
        cells = complete[key]
        by = collections.Counter(meta[k]["group"] for k, c in cells.items() if c["n"])
        p(f"| {key[0]} | {key[1]} | {key[2]} | " +
          " | ".join(str(by[g]) for g in groups) + " |")

    p("\n## 4. Where claims die, and what survives")
    p("\n| version | model | rep | decep | vague | belief | slip | audience | goal | spec1/2 | "
      "goal-limb a/c/both | overt CoT | weak evidence |")
    p("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|")
    for key in sorted(complete, key=str):
        cells = complete[key]
        ex = collections.Counter()
        sp = collections.Counter(); gl = collections.Counter(); ins = collections.Counter()
        bad = dec = 0
        for c in cells.values():
            ex += c["excluded"]; sp += c["spec"]; gl += c["goal"]; ins += c["instr"]
            bad += c["bad_ev"]; dec += 1 if c["n"] else 0
        p(f"| {key[0]} | {key[1]} | {key[2]} | {dec} | {ex['vague']} | {ex['belief']} | "
          f"{ex['slip']} | {ex['audience']} | {ex['goal']} | {sp[1]}/{sp[2]} | "
          f"{gl['avoid']}/{gl['conceal']}/{gl['both']} | {ins['overt']} | {bad} |")

    if args.md:
        Path(args.md).write_text("\n".join(out) + "\n")
        p(f"\nwrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
