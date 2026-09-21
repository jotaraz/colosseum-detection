import json, random, re, sys, glob
from pathlib import Path
REPO = Path('/Users/johannestaraz/Documents/GitHub/colosseum-detection')
sys.path.insert(0, str(REPO))
from experiments.agent5.eval_awareness5 import reasoning_rows
SCR = Path(sys.argv[1])
seats = [l.strip() for l in open(REPO/'experiments/agent5/outputs/ev1_5e8_seats_pure_none.txt') if l.strip()]
runs = [l.strip() for l in open(REPO/'experiments/agent5/outputs/ev1_5e8_runs.txt') if l.strip()]
batch1 = set(random.Random(20260916).sample(seats, 10))
rng = random.Random(20260916 + 1)
chosen = rng.sample([s for s in seats if s not in batch1], 10)
for seat in chosen:
    agent, model, s = seat.split('-'); seed = int(s[1:])
    cands = [r for r in runs if re.search(rf'_conc_{model}_s{seed}_\d{{8}}', r)]
    assert len(cands) == 1, (seat, cands)
    rd = REPO / cands[0]
    run = json.load(open(rd/'run.json'))
    sc = json.load(open(glob.glob(str(rd/'run.cot_evalaware_ev1_*.json'))[0]))
    assert sc['per_agent'][agent]['verdict'] == 'none'
    cv = [c.get('verdict') for c in sc['chunks'] if c.get('agent') == agent]
    assert all(v == 'none' for v in cv), (seat, cv)
    rows = [r for r in reasoning_rows(run) if r['agent'] == agent]
    txt = '\n\n'.join(f"### step {r['step']}\n{r['reasoning']}" for r in rows)
    (SCR/'seats2'/f'{seat}.txt').write_text(txt)
    print(seat, len(cv), len(rows), len(txt))
