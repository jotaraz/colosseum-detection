"""Does every verdict file in the ask-arm tables belong to the rollout sitting next to it?

Re-derives the judged turn set from the local rollout with the judge's own machinery
(assemble_turns -> select_turns -> render_output) and compares it to what the verdict stored.
"""
import json, glob, sys, collections
from pathlib import Path
REPO = Path('/Users/johannestaraz/Documents/GitHub/colosseum-detection')
sys.path.insert(0, str(REPO))
from experiments.agent1.workspace import Workspace
from experiments.agent2.critic import render_output
from experiments.agent2.target_run import assemble_turns
from experiments.agent2.category2_over_agent1 import select_turns

OUT = REPO/'experiments/agent1/outputs'
ARMS = ['askA','askG','askK','askL','askM','askP1','askP2','askP3','askP4','askP5']
M = ['deepseek','kimi','glm']; V = ['v15','v16']
bad = collections.Counter(); rows = []
ws_cache = {}
for arm in ARMS:
    for m in M:
        for v in V:
            for r in sorted(f for f in glob.glob(str(OUT/v/f'inf_{arm}_{m}_s*.json')) if 'category2' not in f):
                rp = Path(r); rep = json.loads(rp.read_text())
                cfg = rep.get('config') or {}
                wsp = str(cfg['workspace'])
                if wsp not in ws_cache: ws_cache[wsp] = Workspace.load(REPO/wsp)
                ws = ws_cache[wsp]
                verds = sorted(glob.glob(r[:-5]+'.category2_jv7_*.json'))
                sel = None
                for vp in verds:
                    d = json.loads(Path(vp).read_text())
                    tag = f'{v}/{rp.stem}#{Path(vp).name.split("0731")[-1] or "_r1"}'
                    # 1. the path the verdict claims
                    if Path(str(d.get('run'))).name != rp.name:
                        bad['run-path-mismatch'] += 1; rows.append((tag,'run path',str(d.get('run')))); continue
                    # 2. fixture + model under test
                    if (d.get('fixture') or {}).get('sha') != (rep.get('fixture') or {}).get('sha'):
                        bad['fixture-sha'] += 1; rows.append((tag,'fixture sha',f"{(d.get('fixture') or {}).get('sha')} vs {(rep.get('fixture') or {}).get('sha')}"))
                    if d.get('model_under_test') != cfg.get('model_name'):
                        bad['model'] += 1; rows.append((tag,'model',f"{d.get('model_under_test')} vs {cfg.get('model_name')}"))
                    # 3. the judged turns, re-derived from the rollout on disk
                    if sel is None:
                        s = d.get('selection') or {}
                        sel = select_turns(assemble_turns(rep, ws), ws,
                                           roles=list(s.get('roles') or ['Data Scientist']),
                                           baseline=int(s['baseline']))
                        sel = {(str(t.get('agent')), int(t.get('turn_index') or 0)): t for t in sel}
                    vt = {(str(t.get('agent')), int(t.get('turn_index') or 0)): t for t in d['turns']}
                    if set(vt) != set(sel):
                        bad['turn-set'] += 1
                        rows.append((tag,'turn set',f'verdict {sorted(vt)} vs rollout {sorted(sel)}')); continue
                    for k, t in vt.items():
                        if t.get('output') != render_output(sel[k]):
                            bad['output-mismatch'] += 1
                            rows.append((tag,f'output {k}', repr(str(t.get("output"))[:80])+' vs '+repr(render_output(sel[k])[:80])))
                        if str(t.get('clock')) != str(sel[k].get('clock')) or str(t.get('round')) != str(sel[k].get('round')):
                            bad['clock/round'] += 1; rows.append((tag,f'clock/round {k}',''))
                    bad['verdicts-checked'] += 1
                # the base verdict predates the `replicate` field on some arms; it is r1
                reps = sorted((json.loads(Path(p).read_text()).get('replicate')
                               or (2 if p.endswith('_r2.json') else 3 if p.endswith('_r3.json') else 1))
                              for p in verds)
                if len(verds) != 3 or reps != [1,2,3]:
                    bad['replicate-numbering'] += 1; rows.append((f'{v}/{rp.stem}','replicates',str(reps)))
                bad['rollouts-checked'] += 1
print('SUMMARY', json.dumps(dict(bad), indent=1))
for t in rows[:40]: print(' !', t)
