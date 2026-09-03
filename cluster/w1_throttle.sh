#!/bin/bash
# Throttled submitter for agent5 rollouts. Runs on the login node under nohup (polling only —
# every rollout is a CPU compute job via run_agent5_conc.sub).
#   nohup bash cluster/w1_throttle.sh <list-file> <max-in-queue> > cluster/w1_throttle.log 2>&1 &
# Submits the configs in <list-file> in order, keeping at most <max-in-queue> of *its own*
# jobs in the queue (running or idle). Never resubmits a failed job (W1_PLAN: no auto-resubmit);
# it only reports. Resumable: configs already submitted are listed in <list-file>.submitted.
set -u
LIST="$1"; MAX="${2:-24}"
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
DONE="$LIST.submitted"; touch "$DONE"
REQ='requirements = (TARGET.Machine =!= "g110.internal.cluster.is.localnet") && (TARGET.Machine =!= "g166.internal.cluster.is.localnet") && (TARGET.Machine =!= "g132.internal.cluster.is.localnet")'
IDS=()
# resume: jobs submitted by an earlier invocation, recovered from the .submitted list's ids
[ -f "$DONE.ids" ] && while read -r id; do [ -n "$id" ] && IDS+=("$id"); done < "$DONE.ids"
touch "$DONE.ids"
while read -r cfg; do
  [ -z "$cfg" ] && continue
  grep -qx "$cfg" "$DONE" && continue
  # in-flight = submitted jobs whose condor log has no termination record yet. (condor_q
  # lags a fresh submit by minutes, so counting the queue lets the loop overshoot the cap.)
  while :; do
    n=0
    for id in "${IDS[@]:-}"; do
      [ -n "$id" ] || continue
      grep -q "Job terminated\|Job was aborted\|Job was evicted" "cluster/agent5_conc_$id.log" 2>/dev/null || n=$((n+1))
    done
    [ "$n" -lt "$MAX" ] && break
    sleep 120
  done
  out=$(condor_submit_bid 100 cluster/run_agent5_conc.sub config="experiments/agent5/configs/$cfg" repeats=1 -append "$REQ" 2>&1)
  id=$(echo "$out" | grep -o "cluster [0-9]*" | head -1 | cut -d" " -f2)
  if [ -n "$id" ]; then
    IDS+=("$id"); echo "$cfg" >> "$DONE"; echo "$id" >> "$DONE.ids"
    echo "[$(date +%H:%M:%S)] submitted $id  $cfg  (in queue: $((n+1)))"
  else
    echo "[$(date +%H:%M:%S)] SUBMIT FAILED for $cfg: $out"
    sleep 60
  fi
done < "$LIST"
echo "[$(date +%H:%M:%S)] all submitted (${#IDS[@]} jobs): ${IDS[*]}"
while :; do
  left=0; for id in "${IDS[@]}"; do grep -q "Job terminated\|Job was aborted\|Job was evicted" "cluster/agent5_conc_$id.log" 2>/dev/null || left=$((left+1)); done
  fails=$(for id in "${IDS[@]}"; do grep -l "rc=1" "cluster/agent5_conc_$id.out" 2>/dev/null; done | wc -l)
  echo "[$(date +%H:%M:%S)] in queue: $left · failed (rc=1): $fails"
  [ "$left" -eq 0 ] && break
  sleep 300
done
echo "[$(date +%H:%M:%S)] THROTTLE DONE"
