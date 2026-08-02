#!/bin/bash
# File-first status snapshot for the 3 social_jira1 n4 effort sweeps (stable against
# condor_q cache flapping). One line per effort: <eff> <STATE> cr=<N> fr=<N>
cd /fast/jtaraz/LIARS/colosseum-detection 2>/dev/null || exit 0
declare -A CL=( [low]=17364079 [medium]=17364080 [high]=17364081 )
for eff in low medium high; do
  cl=${CL[$eff]}
  d="experiments/social_jira1/outputs/social_jira1_n4_gptoss_120b_${eff}"
  prog=$(ls -t $d/*/progress.json 2>/dev/null | head -1)
  status=none; cr=0; fr=0
  if [ -n "$prog" ]; then
    read status cr fr < <(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('status','none'),d.get('completed_runs',0),d.get('failed_runs',0))" "$prog" 2>/dev/null)
  fi
  log=$(ls -t $d/*/experiment.log 2>/dev/null | head -1)
  started=0; rend=0
  if [ -n "$log" ]; then
    grep -q "EXPERIMENT START" "$log" && started=1
    grep -q "RUN END" "$log" && rend=1
  fi
  js=$(condor_q "$cl" -af JobStatus 2>/dev/null | head -1)
  hist=$(condor_history "$cl" -limit 1 -af JobStatus 2>/dev/null | head -1)
  if [ "$status" = "completed" ]; then state=COMPLETED
  elif { [ "$hist" = "3" ] || [ "$hist" = "4" ]; }; then state=GONE_INCOMPLETE   # left queue w/o completing
  elif [ "${rend:-0}" = "1" ]; then state=RUNNING_RESULTS
  elif [ "$started" = "1" ]; then state=RUNNING_SWEEP
  elif [ "$js" = "2" ]; then state=RUNNING_BOOT
  elif [ "$js" = "5" ]; then state=HELD
  else state=QUEUED
  fi
  echo "$eff ${state:-QUEUED} cr=${cr:-0} fr=${fr:-0}"
done
