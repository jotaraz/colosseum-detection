"""Judge-vs-human agreement on the honeypot gates.

    python -m experiments.agent5.honeypot_agree                     # -> per-gate table
    python -m experiments.agent5.honeypot_agree --gate claimed_unmovable   # the disagreeing runs

Human labels come from a `honeypot_label.html` export; the judge's from
`outputs/honeypot_<tag>/<run>.<judge>.json`. Blank human fields are skipped, never read as
`false` — the labeller leaves a gate empty when the evidence does not settle it, and counting
those as agreement or disagreement would both be wrong.

hp1's `claimed_unmovable` is read as hp2's `presented_as_fixed` on both sides: the hand labels
were made against the looser reading, which is what hp2 adopted.
"""
from __future__ import annotations

import argparse, glob, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATES = ["pressed", "used_as_reason", "presented_as_fixed",
         "cot_saw_it_as_usable", "cot_saw_it_as_movable", "declined_to_use"]
COT = {"cot_saw_it_as_usable", "cot_saw_it_as_movable", "declined_to_use"}
BLIND = {"gpt55gw", "kimik3"}       # no reasoning on the record: the CoT gates cannot be evidenced
ALIAS = {"claimed_unmovable": "presented_as_fixed"}


def norm(d: dict) -> dict:
    return {ALIAS.get(k, k): v for k, v in d.items()}


def load(gt_path: Path, out_dir: Path, judge: str):
    gt = {r["run"]: norm(r) for r in json.loads(gt_path.read_text())}
    jd = {os.path.basename(f)[: -len(f".{judge}.json")]: norm(json.loads(Path(f).read_text()))
          for f in glob.glob(str(out_dir / f"*.{judge}.json"))}
    jd = {k: v for k, v in jd.items() if "error" not in v}     # failed calls are missing, not verdicts
    pairs = [(gt[k]["model"], gt[k]["seed"], k, gt[k], jd[k]) for k in gt if k in jd]
    return sorted(pairs), set(gt) - set(jd)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default="honeypot_gt_v1.json")
    ap.add_argument("--out-dir", default="outputs/honeypot_hp2")
    ap.add_argument("--judge", default="azure-gpt-5.5")
    ap.add_argument("--gate", default="", help="show the runs that disagree on this gate")
    a = ap.parse_args()

    rows, missing = load(HERE / a.gt, HERE / a.out_dir, a.judge)
    if missing:
        print(f"note: {len(missing)} labelled run(s) have no verdict yet\n")

    if a.gate:
        for m, s, _, h, j in rows:
            if h.get(a.gate) is not None and h[a.gate] != j[a.gate]:
                why = f"  ({j['declined_because']})" if j.get("declined_because") else ""
                print(f"--- {m} s{s}   human={h[a.gate]}  judge={j[a.gate]}{why}")
                for q in (j.get("quotes") or []):
                    if q.get("gate") == a.gate:
                        print(f"    judge cites [{q.get('where')} {q.get('clock')}] "
                              f"{' '.join((q.get('text') or '').split())[:200]}")
                if h.get("note"):
                    print(f"    human note: {' '.join(h['note'].split())[:200]}")
        return

    print(f"{'':12} {'':5} " + " ".join(g[:9].ljust(9) for g in GATES))
    for m, s, _, h, j in rows:
        cells = ["·" if h.get(g) is None else "=" if h[g] == j[g] else ("H+" if h[g] else "J+")
                 for g in GATES]
        print(f"{m:12} s{str(s):<4} " + " ".join(c.ljust(9) for c in cells))

    print(f"\n{'gate':24} {'n':>3} {'agree':>6} {'human+':>7} {'judge+':>7}  {'blind-excl':>10}")
    for g in GATES:
        p = [(h, j) for _, _, _, h, j in rows if h.get(g) is not None]
        n, ok = len(p), sum(1 for h, j in p if h[g] == j[g])
        q = [(h, j) for m, _, _, h, j in rows if h.get(g) is not None and m not in BLIND]
        ex = f"{sum(1 for h, j in q if h[g] == j[g])}/{len(q)}" if g in COT else ""
        print(f"{g:24} {n:>3} {ok:>3}/{n:<2} {sum(1 for h,_ in p if h[g]):>7} "
              f"{sum(1 for _,j in p if j[g]):>7}  {ex:>10}")
    tot = [(h[g], j[g]) for _, _, _, h, j in rows for g in GATES if h.get(g) is not None]
    print(f"\noverall {sum(1 for a_, b in tot if a_ == b)}/{len(tot)} "
          f"({sum(1 for a_, b in tot if a_ == b) / len(tot) * 100:.0f}%) over "
          f"{len(rows)} runs; {len(rows) * len(GATES) - len(tot)} gates left blank")


if __name__ == "__main__":
    main()
