#!/bin/bash
# Submit the six v4 meta-gate loops: {realism, fabrication, admissibility} x {dspro, gpt54}
# prompter, all cold-start, 12 steps. Run from the login node:
#
#     experiments/social_jira4/cluster/submit_mg_all.sh [bid]        # default bid 100, all 6
#     experiments/social_jira4/cluster/submit_mg_all.sh 100 admissibility_dspro
#
# Each job takes 4 H100-80GB and runs one loop end to end; two jobs pack onto an 8-GPU node, so all
# six want 24 GPUs — three nodes' worth. Ports are distinct per job (8140-8151), so any start order
# is safe if some of them queue.
set -euo pipefail
cd "$(dirname "$0")"

BID="${1:-100}"
shift || true
JOBS=("$@")
if [ "${#JOBS[@]}" -eq 0 ]; then
    JOBS=()
    for p in dspro gpt54; do
        for q in realism fabrication admissibility; do JOBS+=("${q}_${p}"); done
    done
fi

for j in "${JOBS[@]}"; do
    sub="mg_${j}.sub"
    [ -f "$sub" ] || { echo "no such submit file: $sub" >&2; exit 1; }
    echo "== $sub (bid $BID)"
    condor_submit_bid "$BID" "$sub"
done

echo
echo "watch:   condor_q jtaraz"
echo "tail:    tail -f experiments/social_jira4/cluster/mg_*.out"
echo "cost:    cat experiments/social_jira4/outputs/v4_mg_*/cost.json"
