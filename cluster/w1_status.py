"""One-line-per-run status of the w1 rollouts. Run on the cluster (or locally after a pull).

    python3 cluster/w1_status.py            # every w1 run
    python3 cluster/w1_status.py w1P0N0     # only cells whose name contains this

A run directory with a run.json is finished; without one it is still going, and the
world_calls count is the only progress signal there is.
"""

import glob
import json
import os
import sys

PAT = sys.argv[1] if len(sys.argv) > 1 else "w1"
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "experiments", "agent5", "runs")

rows = []
for d in sorted(glob.glob(os.path.join(ROOT, "*%s*" % PAT))):
    if os.path.basename(d).startswith("_"):   # archived/superseded batches
        continue
    short = os.path.basename(d).replace("agent5_", "").replace("_askReasonable_conc", "")
    short = short.split("_2026")[0]
    rj, wc = os.path.join(d, "run.json"), os.path.join(d, "world_calls.jsonl")
    if os.path.exists(rj):
        try:
            r = json.load(open(rj))
        except json.JSONDecodeError:          # being written right now
            rows.append((short, "writing", "", "", ""))
            continue
        s = r.get("score") or {}
        pairs = " ".join("%s:%s" % (k, "+".join(v)) for k, v in sorted((s.get("pairs") or {}).items()))
        # board_shape distinguishes a refusal (everyone assigned, a ticket left short —
        # the signal) from a board that never got finished (breakage). Older run records
        # predate the field, so fall back to reconstructing it from complete/valid.
        shape = s.get("board_shape") or (
            "valid" if s.get("valid") else "unstaffed" if s.get("complete") else "incomplete")
        flag = {"valid": "", "unstaffed": "  UNSTAFFED(refusal?)",
                "incomplete": "  INCOMPLETE", "empty": "  EMPTY"}.get(shape, "  " + shape)
        rows.append((short, "DONE", "turns=%s" % len(r.get("turns", [])),
                     r.get("outcome", "?"), pairs + flag))
    else:
        n = sum(1 for _ in open(wc)) if os.path.exists(wc) else 0
        rows.append((short, "running", "calls=%s" % n, "", ""))

for a, b, c, d, e in sorted(rows):
    print("  %-26s %-8s %-11s %-10s %s" % (a, b, c, d, e))
done = sum(1 for r in rows if r[1] == "DONE")
print("  %d/%d finished" % (done, len(rows)))
