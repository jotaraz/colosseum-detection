#!/bin/bash
# One status line for the reward_v3 campaign, plus the spend backstop.
#
# Prints: sweep progress until the runs exist, then per-run steps/best/spend. If combined spend
# passes CAP it touches STOP in each run's out-dir, which the loop honours between steps — so a
# capped run stops with every completed step already written and is resumable, never truncated
# mid-batch.
P=/fast/jtaraz/LIARS/colosseum-detection
CAP=${CAP:-37}
cd "$P" || exit 1
export PYTHONPATH="$P"
"$P/.venv/bin/python" - "$CAP" <<'PY'
import glob, json, os, re, sys
from pathlib import Path
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent3.cost_breakdown import breakdown

cap = float(sys.argv[1])
root = Path("experiments/agent3/outputs")
total = 0.0
parts = []
for run in ("run05", "run06"):
    d = root / run
    if not (d / "steps").exists():
        continue
    b = breakdown(d)
    if not b:
        continue
    spend = sum(v["cost"] for v in b["acc"].values())
    total += spend
    best = 0.0
    for f in sorted((d / "steps").glob("step_*.json")):
        s = json.loads(f.read_text())
        best = max([best] + [a["reward"] for a in s.get("attempts") or [] if a.get("ran")])
    # Liveness, from the HTCondor log rather than the step files: for an hour tonight both
    # runs were dead and this line still read "1 steps best 1.0", because step files persist.
    logs = sorted(glob.glob("cluster/agent3_%s_*.log" % run))
    dead = ""
    if logs:
        txt = Path(logs[-1]).read_text(errors="ignore")
        if "Job terminated" in txt or "Job was aborted" in txt:
            dead = " DEAD"
    parts.append("%s %d steps best %.1f $%.0f%s" % (run, b["n_steps"], best, spend, dead))

# the sweep's own spend, from its log
sweep = 0.0
for lg in glob.glob("cluster/agent3_sweepjv8_*.out"):
    sweep += 0.02 * len(re.findall(r"v3=", Path(lg).read_text(errors="ignore")))
total += sweep

# Before the first step file lands there is nothing to average, but the runs are not idle —
# report rollouts in flight so a 30-minute stretch of pings is not three identical lines.
for run, log in (("run05", "agent3_run05_*.out"), ("run06", "agent3_run06_*.out")):
    if any(p.startswith("%s " % run) for p in parts):
        continue
    outs = sorted(glob.glob("cluster/" + log))
    if not outs:
        continue
    txt = Path(outs[-1]).read_text(errors="ignore")
    step = re.findall(r"INFO step (\d+)/(\d+)", txt)
    if not step:
        continue
    done = len(re.findall(r"rollout v15__.*(?:converged|deadline|stalled)", txt))
    fail = len(re.findall(r"rollout .* failed", txt))
    parts.append("%s step %s/%s, %d rollouts in%s"
                 % (run, step[-1][0], step[-1][1], done, " %d failed" % fail if fail else ""))

if not parts:
    done = sum(len(re.findall(r"v3=", Path(p).read_text(errors="ignore")))
               for p in glob.glob("cluster/agent3_sweepjv8_*.out"))
    nz = sum(len(re.findall(r"-> +v3=[1-9]", Path(p).read_text(errors="ignore")))
             for p in glob.glob("cluster/agent3_sweepjv8_*.out"))
    parts.append("sweep %d rollouts done, %d non-zero" % (done, nz))

msg = " | ".join(parts) + (" | ~$%.0f total" % total)
if total >= cap:
    for run in ("run05", "run06"):
        d = root / run
        if d.exists():
            (d / "STOP").touch()
    msg += " | CAP HIT - STOP files placed, runs will halt between steps"
print(msg)
PY
