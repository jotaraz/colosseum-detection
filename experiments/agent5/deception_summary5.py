"""Read jv12 sidecars (``run.deception_jv12_*.json``) and tabulate — never pool judges.

    .venv/bin/python -m experiments.agent5.deception_summary5 \
        --runs 'experiments/agent5/runs/agent5_w2PsuperstrongNsuperstrong_*DmHint2_conc_*' [--findings]

Per judge: runs, units, units with findings, findings by seat / audience / serves_goal /
cot_instrumental / specificity, and excluded claims by gate; ``--findings`` prints every
finding's claim, route and where it went, for hand-checking.
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

_DIR_RE = re.compile(r"_conc_(?P<model>[a-z0-9]+?)(?:_s(?P<seed>\d+))?_\d{8}-\d{6}$")


def _model(run: str) -> str:
    m = _DIR_RE.search(run)
    return m.group("model") if m else "?"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--findings", action="store_true")
    args = ap.parse_args(argv)

    files: List[Path] = []
    for pat in args.runs:
        for p in sorted(globmod.glob(pat)):
            d = Path(p) if Path(p).is_dir() else Path(p).parent
            files += sorted(d.glob("run.deception_jv12_*.json"))
    by_judge: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in sorted(set(files)):
        rec = json.loads(f.read_text(encoding="utf-8"))
        by_judge[f"{rec.get('judge')} r{rec.get('replicate')}"].append(rec)

    for judge, recs in sorted(by_judge.items()):
        units = [u for r in recs for u in r.get("units") or []]
        findings = [(r, u, fd) for r in recs for u in r.get("units") or []
                    for fd in u.get("findings") or []]
        excluded = [e for u in units for e in u.get("excluded") or []]
        errors = sum(1 for u in units if u.get("parse_error") or u.get("judge_error"))
        cost = sum(float((r.get("usage_this_file") or {}).get("cost_usd") or 0) for r in recs)
        print(f"\n=== {judge}: {len(recs)} runs, {len(units)} units, "
              f"{sum(1 for u in units if u.get('n_lies'))} with findings, "
              f"{len(findings)} findings, {len(excluded)} excluded, {errors} errors, "
              f"${cost:.2f}")
        per_model: Dict[str, Counter] = defaultdict(Counter)
        for r in recs:
            m = _model(str(r.get("run")))
            per_model[m]["runs"] += 1
            per_model[m]["units"] += len(r.get("units") or [])
            per_model[m]["findings"] += int(r.get("n_findings") or 0)
            per_model[m]["runs_with_findings"] += 1 if r.get("n_lie_units") else 0
        print("  by model: " + "; ".join(
            f"{m}: {c['runs']} runs ({c['runs_with_findings']} with findings), "
            f"{c['units']} units, {c['findings']} findings" for m, c in sorted(per_model.items())))
        if findings:
            def _count(key):
                c = Counter()
                for _, _, fd in findings:
                    v = fd.get(key)
                    for x in (v if isinstance(v, list) else [v]):
                        c[str(x)] += 1
                return dict(c)
            print(f"  seat: {dict(Counter(u['agent'] for _, u, _ in findings))}")
            print(f"  audiences: {_count('audiences')}")
            print(f"  serves_goal: {_count('serves_goal')}  specificity: {_count('specificity')}  "
                  f"cot_instrumental: {_count('cot_instrumental')}  falsity: {_count('falsity')}")
            print(f"  kind: {dict(Counter(u['kind'] for _, u, _ in findings))}  "
                  f"spans verbatim: {sum(all(fd['evidence']['output_spans_verbatim']) for _, _, fd in findings)}/{len(findings)}  "
                  f"schema violations: {sum(1 for _, _, fd in findings if fd.get('schema_violations'))}")
        if excluded:
            print(f"  excluded by gate: {dict(Counter(e.get('failed_gate') for e in excluded))}")
        if args.findings:
            for r, u, fd in findings:
                print(f"\n  - {_model(str(r['run']))} s{r.get('seed')} {u['agent']} t{u['turn_index']} "
                      f"s{u['step']} ({u['kind']}) [{', '.join(fd.get('audiences') or [])}] "
                      f"spec={fd.get('specificity')} {fd.get('serves_goal')} "
                      f"cot={fd.get('cot_instrumental')} {fd.get('falsity')}")
                print(f"    claim: {fd.get('claim')}")
                print(f"    route: {fd.get('goal_route')}")
                for s in fd["evidence"].get("output_spans") or []:
                    print(f"    span:  {s[:300]}")
                for s in fd["evidence"].get("cot_final_stance") or []:
                    print(f"    cot:   {s[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
