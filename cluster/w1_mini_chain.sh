#!/bin/bash
# Waits for a set of agent5 rollout jobs to leave the queue, then judges their runs.
#   nohup bash cluster/w1_mini_chain.sh "<cluster ids>" "<runs glob>" "<out dir>" [expected runs] > cluster/w1_mini_chain.log 2>&1 &
# Runs on the login node (polling only; the judge itself is a condor job). Never resubmits
# a failed rollout — it reports the count and moves on (W1_PLAN: no auto-resubmit).
set -u
IDS="$1"; RUNS="$2"; OUT="$3"; EXPECT="${4:-0}"
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
echo "[$(date +%H:%M:%S)] waiting on jobs: $IDS"
while :; do
  left=0
  for id in $IDS; do
    if condor_q "$id" -af ClusterId 2>/dev/null | grep -q .; then left=$((left+1)); fi
  done
  n=$(ls -d $RUNS 2>/dev/null | while read d; do [ -f "$d/run.json" ] && echo x; done | wc -l)
  echo "[$(date +%H:%M:%S)] jobs left: $left · runs finished: $n"
  # condor_q reads a cached jobads file that can lag a fresh submit by minutes, so an
  # empty queue alone is not proof the batch is over: also require the expected number
  # of finished runs (EXPECT, 0 = don't check) before moving on.
  [ "$left" -eq 0 ] && [ "$n" -ge "$EXPECT" ] && break
  sleep 120
done
n=$(ls -d $RUNS 2>/dev/null | while read d; do [ -f "$d/run.json" ] && echo x; done | wc -l)
echo "[$(date +%H:%M:%S)] rollouts done: $n run.json under $RUNS"
for d in $RUNS; do [ -f "$d/run.json" ] || echo "  NO run.json: $d"; done
echo "[$(date +%H:%M:%S)] submitting judge -> $OUT"
condor_submit_bid 100 cluster/run_agent5_pref_judge.sub runs="$RUNS" out="$OUT" workers=8 2>&1 | tail -2
jid=$(ls -t cluster/agent5_pref_judge_*.log | head -1 | sed 's/.*_\([0-9]*\)\.log/\1/')
echo "[$(date +%H:%M:%S)] judge cluster id (by newest log): $jid"
while :; do
  if grep -q "ALL DONE" "cluster/agent5_pref_judge_${jid}.out" 2>/dev/null; then break; fi
  if grep -q "aborted\|terminated" "cluster/agent5_pref_judge_${jid}.log" 2>/dev/null && ! grep -q "ALL DONE" "cluster/agent5_pref_judge_${jid}.out" 2>/dev/null; then echo "judge job left the queue without ALL DONE"; break; fi
  sleep 60
done
echo "[$(date +%H:%M:%S)] CHAIN DONE"
