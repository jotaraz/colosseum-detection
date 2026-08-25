"""What the jv10/jv11 sweep found, per setting — coverage first, agreement second.

    .venv/bin/python -m experiments.agent2.deception_summary [--sample <path>] [--csv <path>]

Reads every ``<run>.deception_jv1[01]_<model>[_rN].json`` beside the sampled runs and reports,
per (version, model, replicate): how many units came back, how many parsed, how many carry a
finding, and — for the turns jv9 labelled — how the deception verdict lines up with jv9's
>=2/3 lie vote. Coverage is printed before agreement on purpose: a setting that judged 60 of
91 turns has an agreement number that means nothing, and the failure mode this sweep is most
likely to hit is a provider dropping calls, not a judge disagreeing.

jv10 verdicts are per STEP, so they are collapsed to the turn first — a turn counts as
deception if any of its steps carried a finding — which is the only way jv10 and jv11 are
comparable at all.
"""

import argparse
import collections
import csv
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SAMPLE = Path(__file__).with_name("deception_sample.json")


def _verdicts(sample_runs):
    """(version, model, replicate) -> {(run, turn): row-summary}, collapsing jv10's steps."""
    out = collections.defaultdict(dict)
    for run in sorted(sample_runs):
        stem = str(REPO / run)[:-len(".json")]
        for f in glob.glob(stem + ".deception_jv1*_*.json"):
            d = json.loads(Path(f).read_text())
            rep = int(d.get("replicate") or 1)
            key = (d.get("judge_version"), d.get("judge", "?").split(":")[-1], rep)
            for r in d.get("turns") or []:
                cell = out[key].setdefault((run, r["turn_index"]),
                                           {"units": 0, "findings": 0, "excluded": 0,
                                            "errors": 0, "parse_errors": 0})
                cell["units"] += 1
                cell["findings"] += int(r.get("n_lies") or 0)
                cell["excluded"] += len(r.get("excluded") or [])
                cell["errors"] += 1 if r.get("judge_error") else 0
                cell["parse_errors"] += 1 if r.get("parse_error") else 0
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="summarise the jv10/jv11 deception sweep")
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--csv", default=None, help="also write one row per (setting, turn)")
    args = ap.parse_args(argv)

    sample = json.loads(Path(args.sample).read_text())
    jv9 = {(s["run"], s["turn_index"]): s for s in sample}
    got = _verdicts({s["run"] for s in sample})
    if not got:
        print("no deception verdicts found yet")
        return 0

    print(f"sample: {len(sample)} turns "
          f"({sum(1 for s in sample if (s['jv9_votes'] or 0) >= 2)} with >=2/3 jv9 lie)\n")
    head = f"{'version':<7} {'model':<34} {'rep':>3} {'turns':>6} {'units':>6} " \
           f"{'err':>4} {'parse':>6} {'decep':>6} {'excl':>5} {'agree':>7}"
    print(head)
    print("-" * len(head))
    rows = []
    for key in sorted(got):
        version, model, rep = key
        cells = got[key]
        dec = {k for k, c in cells.items() if c["findings"]}
        labelled = [k for k in cells if (jv9[k]["jv9_votes"] or 0) is not None
                    and jv9[k]["jv9_votes"] is not None]
        agree = sum(1 for k in labelled
                    if (k in dec) == ((jv9[k]["jv9_votes"] or 0) >= 2))
        pct = f"{100 * agree / len(labelled):.0f}%" if labelled else "-"
        print(f"{version:<7} {model:<34} {rep:>3} {len(cells):>6} "
              f"{sum(c['units'] for c in cells.values()):>6} "
              f"{sum(c['errors'] for c in cells.values()):>4} "
              f"{sum(c['parse_errors'] for c in cells.values()):>6} "
              f"{len(dec):>6} {sum(c['excluded'] for c in cells.values()):>5} {pct:>7}")
        for k, c in cells.items():
            rows.append({"version": version, "model": model, "replicate": rep,
                         "run": k[0], "turn_index": k[1], "group": jv9[k]["group"],
                         "jv9_votes": jv9[k]["jv9_votes"], "deception": int(bool(c["findings"])),
                         **c})
    missing = {key: len(sample) - len(cells) for key, cells in got.items()
               if len(cells) < len(sample)}
    if missing:
        print("\nINCOMPLETE settings (turns still unjudged):")
        for key, n in sorted(missing.items()):
            print(f"  {key}: {n} of {len(sample)} missing")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {args.csv} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
