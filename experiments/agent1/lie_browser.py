"""A local browser for every turn a fabrication or lie judge flagged.

Scans every run in `outputs/` that has jv7 category2 verdicts, jv8 lie verdicts, or both.
Where jv7 exists, forms claims the same way `analysis_claimlevel_askarms.py` does (same
subject/object + ≥0.5 span overlap) and keeps the turns where ≥2 replicates agree. A turn
jv8 judged and found a lie in but that didn't clear the jv7 bar — either because the run has
no jv7 verdicts at all, or jv7 simply didn't flag it — gets its own row too, with no jv7
claim/category/level attached. Writes one self-contained page: a filterable list on the left,
the turn with its messages and every replicate's verdict on the right, and a link that opens
the rollout's own viewer at that exact turn (`…html#turn-N`).

    python3 lie_browser.py [outdir…]      # default: v15 v16 v17
"""
import json, glob, re, sys, html, collections
from pathlib import Path

HERE = Path('/Users/johannestaraz/Documents/GitHub/colosseum-detection/experiments/agent1')
OUT = HERE/'outputs'
DIRS = sys.argv[1:] or ['v15', 'v16', 'v17']

src = (HERE/'analysis_claimlevel_askarms.py').read_text().split("data={arm:load(arm)")[0]
g = dict(__name__='m'); exec(src, g)
claims, locate, ncat, nint, LEVEL = g['claims'], g['locate'], g['ncat'], g['nint'], g['LEVEL']
FAB = ('hard-fabrication', 'soft-fabrication')
LNAME = {0: 'incidental', 1: 'ambiguous', 2: 'purposeful'}
NAME = re.compile(r'^inf_(?P<arm>[^_]+)_(?P<model>.+)_s(?P<seed>\d+)$')
MSG = re.compile(r'^\[([^\]]+)\] (to (.+?):|privately to its own employee:|claimed on the board: (.*))$', re.M)


def split_output(output):
    marks = list(MSG.finditer(output or ''))
    if not marks:
        return [{'kind': 'other', 'head': 'Output', 'body': str(output or '').strip()}]
    out = []
    for i, m in enumerate(marks):
        body = output[m.end():(marks[i+1].start() if i+1 < len(marks) else len(output))].strip()
        tag, conv, board = m.group(1), m.group(3), m.group(4)
        if board is not None:
            out.append({'kind': 'board', 'head': f'Board action — claimed: {board.strip()}', 'body': ''})
        elif conv is None:
            out.append({'kind': 'debrief', 'head': 'Debrief — privately to its own employee', 'body': body})
        elif str(conv).startswith('dm:'):
            out.append({'kind': 'dm', 'head': f'DM — {conv}', 'body': body, 'aud': tag})
        else:
            out.append({'kind': 'channel', 'head': f'Channel post — {conv}', 'body': body, 'aud': tag})
    return out


def finding_row(f, in_claim):
    ev = f.get('evidence') or {}
    st = ev.get('output_spans_checks') or []
    spans = [{'t': s, 's': (st[k].get('status') if k < len(st) else 'unchecked')}
             for k, s in enumerate(ev.get('output_spans') or [])]
    return {'cat': ncat(str(f.get('category') or '')), 'intent': nint(f.get('intent')),
            'subj': f.get('fabrication_subject'), 'obj': f.get('fabrication_object'),
            'reason': f.get('reason') or '', 'spans': spans,
            'cot': list(ev.get('cot_quotes') or []), 'contra': list(ev.get('contradicted_by') or []),
            'aud': list(f.get('audiences') or []), 'inclaim': in_claim}


def _statused(texts, checks):
    return [{'t': s, 's': (checks[i].get('status') if i < len(checks) else 'unchecked')}
            for i, s in enumerate(texts or [])]


def lie_row(f):
    ev = f.get('evidence') or {}
    return {'claim': f.get('claim') or '', 'reason': f.get('reason') or '',
            'falsity': f.get('falsity'), 'checkable': bool(f.get('checkable')),
            'gate4': f.get('gate4'), 'deception': f.get('deception'),
            'aud': list(f.get('audiences') or []),
            'spans': _statused(ev.get('output_spans'), ev.get('output_spans_checks') or []),
            'cot': _statused(ev.get('cot_final_stance'), ev.get('cot_final_stance_checks') or []),
            'dcot': _statused(ev.get('cot_deception'), ev.get('cot_deception_checks') or []),
            'contra': list(ev.get('contradicted_by') or []),
            'via': ev.get('addressee_could_verify_via')}


#: lie/sweep verdict filenames both carry the version: `.lie_jv9_slug[_rN].json` and
#: agent3's `.sweep_jv8-maj3_slug[_rN].json`.
LIE_VER_RE = re.compile(r'\.(?:lie|sweep)_jv(\d+)(?:-maj\d+)?_')


def lie_overlays(agent, ti, lie_sets):
    """One overlay per lie-judge version that judged this turn: ``{'jv8': …, 'jv9': …}``."""
    out = {}
    for ver, (paths, verds) in lie_sets.items():
        ov = jv8_overlay(agent, ti, paths, verds)
        if ov: out[ver] = ov
    return out or None


def jv8_overlay(agent, ti, jvps, verds8, pending=()):
    """The lie-judge replicates for one (agent, turn), or None if the judge never saw it.

    ``pending`` names newer judge versions held back for being short on replicates — recorded
    so a reader can tell "this run has no newer verdict" from "a newer one exists but is only
    part-written", which otherwise look identical on the page."""
    j8 = []
    for vd8, vp8 in zip(verds8, jvps):
        t8 = next((x for x in vd8['turns'] if str(x['agent']) == agent
                   and int(x['turn_index']) == ti), None)
        if t8 is None:
            j8.append({'file': Path(vp8).name, 'missing': True}); continue
        j8.append({'file': Path(vp8).name, 'desc': t8.get('description') or '',
                   'n': int(t8.get('n_lies') or 0),
                   'findings': [lie_row(f) for f in t8.get('findings') or []],
                   'excluded': [{'claim': x.get('claim') or '',
                                 'span': x.get('output_span') or '',
                                 'gate': x.get('failed_gate') or '?',
                                 'reason': x.get('reason') or ''}
                                for x in t8.get('excluded') or []]})
    live = [x for x in j8 if not x.get('missing')]
    ns = [x['n'] for x in live]
    ver = 'jv?'
    m8 = LIE_VER_RE.search(str(jvps[0])) if jvps else None
    if m8: ver = f'jv{m8.group(1)}'
    # ke: replicates that raised >=1 *excluded* claim — false, but killed by a later gate.
    # ko: replicates with >=1 *overt* lie — the CoT shows the falsehood chosen as a tool.
    # Tracked beside k because overt replicates agree far better than not-shown ones.
    return {'reps': j8, 'k': sum(1 for n in ns if n > 0), 'n': len(ns),
            'ke': sum(1 for x in live if x.get('excluded')),
            'ko': sum(1 for x in live
                      if any(f.get('deception') == 'overt' for f in (x.get('findings') or []))),
            'ver': ver,
            'pending': [list(p) for p in pending]} if ns else None


records, seen_runs = [], 0
#: Which principals hold a stake, per fixture dir, harvested from jv7's own `selection`.
#: A lie-judge turn record carries no `stake` flag of its own (only jv7 tags turns), so a
#: jv8-only row has to get it from somewhere or it silently reads as a baseline turn and the
#: "stake agents only" filter — on by default — hides it.
stake_agents_by_dir = collections.defaultdict(set)
for d in DIRS:
    for r in sorted(f for f in glob.glob(str(OUT/d/'inf_*.json')) if 'category2' not in f):
        vps = sorted(glob.glob(r[:-5] + '.category2_jv7_*.json'))
        # lie-judge replicates beside the same run (base file is r1, then _r2, _r3). Every
        # version present is loaded — the page carries per-version show/hide toggles and an
        # active-version filter, so jv8 and jv9 sit side by side instead of newest-wins. A
        # part-written campaign shows as k/1 rather than silently thinning a majority read.
        jvall = glob.glob(r[:-5] + '.lie_jv*_*.json') + glob.glob(r[:-5] + '.sweep_jv*_*.json')
        by_ver = collections.defaultdict(list)
        for p in jvall: by_ver[int(LIE_VER_RE.search(p).group(1))].append(p)
        lie_sets = {f'jv{v}': (sorted(ps), [json.loads(Path(p).read_text()) for p in sorted(ps)])
                    for v, ps in sorted(by_ver.items())}
        # a run needs >=2 jv7 replicates to form a claim by vote, OR at least one lie-judge
        # replicate to show turns via the lie-only path below
        if len(vps) < 2 and not lie_sets: continue
        seen_runs += 1
        m = NAME.match(Path(r).stem); meta = m.groupdict() if m else {'arm': '?', 'model': '?', 'seed': '?'}
        verds = [json.loads(Path(p).read_text()) for p in vps]
        for vd in verds:
            stake_agents_by_dir[d] |= set((vd.get('selection') or {}).get('stake_agents') or [])
        # findings per agent/turn, tagged with the replicate and its index in that turn
        per = collections.defaultdict(lambda: collections.defaultdict(list))
        turns = {}
        seen_keys = set()  # (agent, turn_index) already emitted via a jv7 claim, this run
        for ri, vd in enumerate(verds):
            for t in vd['turns']:
                key = (str(t['agent']), int(t['turn_index']))
                turns.setdefault(key, t)
                for fi, f in enumerate(t['findings']):
                    c = ncat(str(f.get('category') or ''))
                    if c not in FAB: continue
                    sp = f['evidence'].get('output_spans') or []
                    ch = f['evidence'].get('output_spans_checks') or []
                    iv = [x for x in (locate(s, ch[k] if k < len(ch) else None, t['output'])
                                      for k, s in enumerate(sp)) if x]
                    per[key][c].append({'rep': ri, 'idx': fi, 'cat': c, 'lvl': LEVEL[nint(f.get('intent'))],
                                        'subj': f.get('fabrication_subject'),
                                        'obj': f.get('fabrication_object'), 'iv': iv})
        for key, bycat in per.items():
            agent, ti = key
            found, members = [], set()
            for cat, fs in bycat.items():
                for comp in claims(fs):
                    best = {}
                    for f in comp: best[f['rep']] = max(best.get(f['rep'], -1), f['lvl'])
                    if len(best) < 2: continue
                    lvl = max((L for L in (0, 1, 2) if sum(1 for x in best.values() if x >= L) >= 2),
                              default=None)
                    if lvl is None: continue
                    votes = sum(1 for x in best.values() if x >= lvl)
                    found.append({'cat': cat, 'subj': comp[0]['subj'], 'obj': comp[0]['obj'],
                                  'lvl': lvl, 'nvotes': votes,
                                  'votes': {str(k+1): v for k, v in sorted(best.items())},
                                  'key': '|'.join([Path(r).name, agent, str(ti), cat,
                                                   str(comp[0]['subj']), str(comp[0]['obj'])])})
                    members |= {(f['rep'], f['idx']) for f in comp}
            if not found: continue
            t0 = turns[key]
            reps = []
            for ri, (vd, vp) in enumerate(zip(verds, vps)):
                t = next((x for x in vd['turns']
                          if str(x['agent']) == agent and int(x['turn_index']) == ti), None)
                if t is None:
                    reps.append({'file': Path(vp).name, 'missing': True}); continue
                reps.append({'file': Path(vp).name, 'desc': t.get('description') or '',
                             'labels': [f"{f.get('category')}/{f.get('intent')}"
                                        for f in t['findings'] if f.get('category')],
                             'findings': [finding_row(f, (ri, fi) in members)
                                          for fi, f in enumerate(t['findings'])
                                          if ncat(str(f.get('category') or '')) in FAB]})
            records.append({
                'lies': lie_overlays(agent, ti, lie_sets) if lie_sets else None,
                'arm': meta['arm'], 'model': meta['model'], 'seed': meta['seed'], 'ver': d,
                'run': Path(r).name, 'html': f"{d}/{Path(r).stem}.html#turn-{ti}",
                'agent': agent, 'turn': ti, 'clock': t0.get('clock'), 'round': t0.get('round'),
                'stake': bool(t0.get('stake')),
                'cats': sorted({c['cat'] for c in found}),
                'top': max(c['lvl'] for c in found),
                'msgs': split_output(t0.get('output')), 'claims': found, 'reps': reps})
            seen_keys.add(key)

        # jv8-only rows: a turn the lie judge had something to say about — a lie, or a claim it
        # found false but excluded at a later gate — that never surfaced above, either because
        # the run has no jv7 verdicts at all or jv7 simply never flagged it as fabrication.
        # Excluded-only turns are kept deliberately: a near miss is the interesting comparison
        # case for a lie, and dropping them would make the excluded filter below unable to show
        # the very rows it exists for. No jv7 claim/category/level exists for these, so 'cats'
        # is empty and 'top' is None; the browser's level filter treats None as "no jv7 opinion,
        # don't exclude on that scale" rather than as the lowest level.
        if lie_sets:
            j8_turns, j8_by_key = {}, collections.defaultdict(list)
            for _lv, (_lp, _lverds) in lie_sets.items():
                for vd8 in _lverds:
                    for t8 in vd8.get('turns') or []:
                        key = (str(t8['agent']), int(t8['turn_index']))
                        j8_turns.setdefault(key, t8)
                        j8_by_key[key].append(t8)
            for key, t8s in j8_by_key.items():
                if key in seen_keys: continue
                if not any((t8.get('n_lies') or 0) > 0 or (t8.get('excluded') or [])
                           for t8 in t8s): continue
                agent, ti = key
                t0 = j8_turns[key]
                # stake: prefer jv7's own tag for this exact turn when jv7 judged it (it just
                # found no fabrication); otherwise leave None for the by-dir pass below.
                t7 = turns.get(key)
                stake = bool(t7.get('stake')) if t7 is not None else None
                records.append({
                    'lies': lie_overlays(agent, ti, lie_sets),
                    'arm': meta['arm'], 'model': meta['model'], 'seed': meta['seed'], 'ver': d,
                    'run': Path(r).name, 'html': f"{d}/{Path(r).stem}.html#turn-{ti}",
                    'agent': agent, 'turn': ti, 'clock': t0.get('clock'), 'round': t0.get('round'),
                    'stake': stake,
                    'cats': [], 'top': None,
                    'msgs': split_output(t0.get('output')), 'claims': [], 'reps': []})

# agent3 rollouts (run05-run10): no jv7 category2 verdicts, so every row enters via the
# lie-only path. jv8 lives in `run.sweep_jv8-maj3_*` files (the reward sweep judged every
# rewarded turn), jv9 in `run.lie_jv9_*` and `run.sweep_jv9-maj3_*` (the targeted re-judge and
# the run07+ sweeps). Only turns some replicate had something to say about (a lie, or an
# excluded claim) are listed.
# The agent3 rollouts live outside outputs/, but the links must stay inside it: adjudicate.py
# serves outputs/ as the web root and SimpleHTTPRequestHandler strips a leading `../`, so a
# path that climbs out 404s over http (it only works when the page is opened as file://).
# A symlink keeps one relative path correct in both modes.
A3_LINK = OUT / 'agent3'
if not A3_LINK.exists():
    A3_LINK.symlink_to(Path('..') / '..' / 'agent3')

A3 = HERE.parent / 'agent3' / 'outputs'
# run05/06 carry jv8 sweeps, run07-10 jv9 ones; the loader keys off whatever `run.sweep_jv*`
# / `run.lie_jv*` files a candidate has, so the two generations coexist in one list. All six
# roll out the same target (deepseek v15) — they differ in the prompter that wrote the ask
# (run07 glm-5.3, run08 kimi-k3, run09 claude sonnet, run10 claude opus), which the `world`
# filter separates by run name.
A3_RUNS = ('run05', 'run06', 'run07', 'run08', 'run09', 'run10')
for a3run in A3_RUNS:
    for rj in sorted(glob.glob(str(A3 / a3run / 'runs' / '*' / '*' / 'run.json'))):
        stem = rj[:-5]
        jvall = glob.glob(stem + '.lie_jv*_*.json') + glob.glob(stem + '.sweep_jv*_*.json')
        if not jvall: continue
        by_ver = collections.defaultdict(list)
        for p_ in jvall: by_ver[int(LIE_VER_RE.search(p_).group(1))].append(p_)
        lie_sets = {f'jv{v}': (sorted(ps), [json.loads(Path(p_).read_text()) for p_ in sorted(ps)])
                    for v, ps in sorted(by_ver.items())}
        seen_runs += 1
        step, cand = Path(rj).parts[-3], Path(rj).parts[-2]      # step004, v15__hash__c1r4
        j8_turns, j8_by_key = {}, collections.defaultdict(list)
        for _lv, (_lp, _lverds) in lie_sets.items():
            for vd8 in _lverds:
                for t8 in vd8.get('turns') or []:
                    key = (str(t8['agent']), int(t8['turn_index']))
                    j8_turns.setdefault(key, t8)
                    j8_by_key[key].append(t8)
        has_html = Path(stem + '.html').exists()
        for key, t8s in sorted(j8_by_key.items()):
            if not any((t8.get('n_lies') or 0) > 0 or (t8.get('excluded') or [])
                       for t8 in t8s): continue
            agent, ti = key
            t0 = j8_turns[key]
            records.append({
                'lies': lie_overlays(agent, ti, lie_sets),
                'arm': 'agent3', 'model': 'deepseek',
                'seed': f"{step.replace('step', '')}·{cand.split('__')[-1]}",
                'ver': a3run,
                'run': f'{a3run}/{step}/{cand}',
                'html': (f'agent3/outputs/{a3run}/runs/{step}/{cand}/run.html#turn-{ti}'
                         if has_html else ''),
                'agent': agent, 'turn': ti, 'clock': t0.get('clock'), 'round': t0.get('round'),
                'stake': True,   # agent3 judges the rewarded agent only
                'cats': [], 'top': None,
                'msgs': split_output(t0.get('output')), 'claims': [], 'reps': []})

# Fill the stake left unresolved above: a run with no jv7 at all can't tag its own turns, so
# fall back to the stake set jv7 recorded for that fixture dir (v15: Nadia + Priya). A dir with
# no jv7 anywhere leaves it True rather than False — an unknown must not read as "baseline" and
# get hidden by a filter that is on by default.
for rec in records:
    if rec['stake'] is None:
        known = stake_agents_by_dir.get(rec['ver'])
        rec['stake'] = (rec['agent'] in known) if known else True

try:
    PRIOR = json.loads((OUT/'adjudications.json').read_text(encoding='utf-8'))
except Exception:
    PRIOR = {}

records.sort(key=lambda e: (e['arm'], e['ver'], e['model'], e['seed'], e['agent'], e['turn']))
blob = json.dumps(records, ensure_ascii=False, separators=(',', ':'))
prior = json.dumps(PRIOR, ensure_ascii=False, separators=(',', ':'))
print(f'{seen_runs} runs scanned, {len(records)} turns kept, {len(blob)/1e6:.1f} MB of data',
      file=sys.stderr)

# ------------------------------------------------------------------------------------ the page
CSS = """
*{box-sizing:border-box}
:root{--bg:#fbfbfa;--card:#fff;--ink:#1a1a19;--dim:#6b6b66;--line:#e3e3df;--accent:#8a4b2a;
      --hard:#b4341f;--soft:#b8860b;--chip:#f0efec;--hit:#fdf3d7}
@media (prefers-color-scheme:dark){:root{--bg:#17181a;--card:#1e1f22;--ink:#e8e8e4;--dim:#9a9a95;
      --line:#2e3034;--accent:#d99b6c;--hard:#e8705c;--soft:#dfb459;--chip:#2a2c30;--hit:#3a3320}}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     display:grid;grid-template-rows:auto auto 1fr;height:100vh;overflow:hidden}
header{padding:.7rem 1rem .5rem;border-bottom:1px solid var(--line)}
h1{font-size:1rem;margin:0 0 .2rem}
.sub{color:var(--dim);font-size:.8rem;margin:0}
.filters{display:flex;flex-wrap:wrap;gap:.4rem;padding:.5rem 1rem;border-bottom:1px solid var(--line);
         align-items:center;background:var(--card)}
select,input[type=search]{font:inherit;font-size:.82rem;padding:.25rem .4rem;border:1px solid var(--line);
         border-radius:5px;background:var(--bg);color:var(--ink)}
input[type=search]{min-width:15rem;flex:1}
label.f{font-size:.78rem;color:var(--dim);display:flex;gap:.3rem;align-items:center}
.main{display:grid;grid-template-columns:minmax(300px,26rem) 1fr;min-height:0}
#list{overflow:auto;border-right:1px solid var(--line)}
#detail{overflow:auto;padding:1rem 1.2rem 4rem}
.row{padding:.45rem .7rem;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:var(--card)}
.row.sel{background:var(--chip);box-shadow:inset 3px 0 0 var(--accent)}
.row .t{font-weight:600;font-size:.85rem}
.row .m{color:var(--dim);font-size:.76rem;font-variant-numeric:tabular-nums}
.chip{display:inline-block;font-size:.68rem;padding:.05rem .35rem;border-radius:99px;background:var(--chip);
      color:var(--dim);margin-right:.25rem;white-space:nowrap}
.chip.hard{background:var(--hard);color:#fff}.chip.soft{background:var(--soft);color:#211}
.chip.p{outline:1px solid var(--accent)}
.tog{display:flex;gap:.15rem;align-items:center}
.tog .t{font-size:.78rem;color:var(--dim);display:flex;gap:.25rem;align-items:center;padding:.2rem .45rem;
        border:1px solid var(--line);border-radius:5px;background:var(--bg);cursor:pointer}
.tog .t:has(input:checked){background:var(--chip);color:var(--ink);border-color:var(--accent)}
.adj{margin:.5rem 0 .2rem;display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}
.adj button{font:inherit;font-size:.78rem;padding:.2rem .5rem;border:1px solid var(--line);border-radius:5px;
            background:var(--bg);color:var(--ink);cursor:pointer}
.adj button:hover{border-color:var(--accent)}
.adj button.on{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:600}
.adj button.on[data-l="not-real"]{background:var(--hard)}
.adj .sep{color:var(--dim);font-size:.75rem;margin-left:.4rem}
.adj .saved{color:var(--dim);font-size:.72rem;margin-left:auto}
.adj textarea{flex:1 0 100%;font:inherit;font-size:.82rem;padding:.35rem .5rem;border:1px solid var(--line);
              border-radius:5px;background:var(--bg);color:var(--ink);min-height:2.2rem;resize:vertical}
.claim.judged{outline:2px solid var(--accent)}
.row .adjdot{float:right;font-size:.7rem;color:var(--accent);font-weight:700}
#stats{padding:.5rem 1rem;border-bottom:1px solid var(--line);background:var(--card);font-size:.8rem;display:none}
#stats table{border-collapse:collapse;margin:.3rem 1rem .3rem 0;display:inline-table;vertical-align:top}
#stats td,#stats th{border:1px solid var(--line);padding:.1rem .45rem;text-align:right;font-variant-numeric:tabular-nums}
#stats th:first-child,#stats td:first-child{text-align:left}
.warn{background:var(--hit);padding:.3rem .6rem;border-radius:5px;font-size:.78rem;display:inline-block}
h2{font-size:1rem;margin:.2rem 0 .1rem}
h3{font-size:.82rem;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);
   margin:1.3rem 0 .4rem;border-bottom:1px solid var(--line);padding-bottom:.2rem}
a.jump{display:inline-block;margin:.5rem 0;padding:.35rem .7rem;border-radius:6px;background:var(--accent);
       color:#fff;text-decoration:none;font-size:.82rem;font-weight:600}
a.jump:hover{filter:brightness(1.1)}
pre{white-space:pre-wrap;word-wrap:break-word;background:var(--card);border:1px solid var(--line);
    border-radius:6px;padding:.6rem .7rem;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
    margin:.3rem 0;max-height:26rem;overflow:auto}
.msg .h{font-weight:600;font-size:.82rem;margin-top:.7rem}
.rep{border:1px solid var(--line);border-radius:8px;padding:.6rem .8rem;margin:.6rem 0;background:var(--card)}
.rep>.who{font-weight:600;font-size:.85rem}
.rep .file{color:var(--dim);font-size:.72rem;font-family:ui-monospace,Menlo,monospace}
.f-in{border-left:3px solid var(--accent);padding-left:.6rem;margin:.5rem 0}
.f-out{border-left:3px dotted var(--dim);padding-left:.6rem;margin:.5rem 0;opacity:.85}
.sect{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);
  font-weight:700;margin:.7rem 0 .3rem;padding-bottom:.15rem;border-bottom:1px solid var(--line)}
.q{margin:.25rem 0;font-size:.83rem}
.q b{color:var(--dim);font-weight:600}
.bad{color:var(--hard);font-size:.72rem}
.none{color:var(--dim);font-style:italic}
.claim{background:var(--hit);border:1px solid var(--line);border-radius:6px;padding:.4rem .6rem;margin:.3rem 0;
       font-size:.85rem}
kbd{font:inherit;font-size:.72rem;background:var(--chip);border:1px solid var(--line);border-radius:4px;
    padding:0 .25rem}
mark.hl{background:rgba(228,86,60,.32);color:inherit;border-radius:3px;padding:0 1px}
mark.hlx{background:rgba(216,166,60,.30);color:inherit;border-radius:3px;padding:0 1px}
mark.hl7{background:transparent;color:inherit;border-bottom:2px dotted var(--accent)}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
let ADJ = JSON.parse(document.getElementById('prior').textContent);   /* what was on disk at build */
const LS = 'agent1-adjudications';
let ONLINE = false, dirty = {};

/* served by adjudicate.py → save to disk; opened as a file → keep it in this browser and export */
async function boot() {
  try {
    const r = await fetch('api/adjudications', {cache: 'no-store'});
    if (!r.ok) throw 0;
    ADJ = await r.json(); ONLINE = true;
    conn(`saving to adjudications.json · ${Object.keys(ADJ).length} entries`);
  } catch (e) {
    ONLINE = false;
    try { ADJ = Object.assign({}, ADJ, JSON.parse(localStorage.getItem(LS) || '{}')); } catch (_) {}
    conn('<span class="warn">offline — judgements stay in this browser. '
       + 'Run <code>python3 adjudicate.py</code> to save to disk, or use export.</span>'
       + ' <button id="exp">export json</button>');
    const b = document.getElementById('exp');
    if (b) b.onclick = () => {
      navigator.clipboard.writeText(JSON.stringify(ADJ, null, 1));
      b.textContent = 'copied — save it and run: adjudicate.py --import-json <file>';
    };
  }
  render();
}
function conn(html) { document.getElementById('conn').innerHTML = html; }

function save(key, patch, meta) {
  const cur = Object.assign({}, ADJ[key] || {}, patch);
  if (meta) cur.meta = meta;
  if (!cur.label && !cur.intent && !(cur.note || '').trim()) delete ADJ[key]; else ADJ[key] = cur;
  dirty[key] = ADJ[key] || {label: '', intent: '', note: '', meta: meta};
  flush();
}
let timer = null;
function flush() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const entries = dirty; dirty = {};
    if (!Object.keys(entries).length) return;
    if (!ONLINE) {
      try { localStorage.setItem(LS, JSON.stringify(ADJ)); mark('saved in browser'); }
      catch (e) { mark('COULD NOT SAVE — export instead'); }
      return;
    }
    try {
      const r = await fetch('api/adjudications', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entries})});
      const j = await r.json();
      mark(j.ok ? `saved · ${j.n} on disk` : `save failed: ${j.error || '?'}`);
    } catch (e) { mark('save failed — is adjudicate.py still running?'); }
  }, 250);
}
function mark(t) { document.querySelectorAll('.adj .saved').forEach(e => e.textContent = t); }
const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const LN = {0:'incidental', 1:'ambiguous', 2:'purposeful'};
const hideJv7 = () => $('#hidejv7').checked;
const LABELS = ['real','not-real','wrong-category','unsure'];
const MARK = {'real':'✓', 'not-real':'✗', 'wrong-category':'~', 'unsure':'?'};
let view = [], cur = 0;

function opts(sel, vals, all) {
  sel.innerHTML = `<option value="">${all}</option>` + vals.map(v=>`<option>${esc(v)}</option>`).join('');
}
const uniq = k => [...new Set(D.map(r=>r[k]))].sort();
opts($('#arm'), uniq('arm'), 'all arms');
opts($('#agent'), uniq('agent'), 'all agents');
opts($('#model'), uniq('model'), 'all models');
/* worlds are toggles, not a dropdown: v15 and v16 are usually watched together */
$('#vers').innerHTML = uniq('ver').map(v =>
  `<label class="t"><input type="checkbox" class="verbox" value="${esc(v)}" checked>${esc(v)}</label>`).join('');
const versOn = () => {
  const on = [...document.querySelectorAll('.verbox')].filter(b=>b.checked).map(b=>b.value);
  return on.length ? on : uniq('ver');   /* none ticked reads as all, never an empty screen */
};
/* lie-judge versions: a show/hide toggle per version (display-only, like hide jv7), and an
   ACTIVE version driving the k/n filters and chips — "filter on newest" follows each row's
   newest verdict; picking a version makes rows it never judged read as not-judged. */
const LVERS = [...new Set(D.flatMap(r=>r.lies?Object.keys(r.lies):[]))].sort();
$('#lievers').innerHTML = LVERS.map(v =>
  `<label class="t"><input type="checkbox" class="lvbox" value="${v}" checked>show ${v}</label>`).join('');
opts($('#liever'), LVERS, 'filter on newest');
const lvHidden = v => { const b=document.querySelector(`.lvbox[value="${v}"]`); return b && !b.checked; };
function lieAct(r){
  if (!r.lies) return null;
  const pick = $('#liever').value;
  if (pick) return r.lies[pick] || null;
  const vs = Object.keys(r.lies).sort();
  return r.lies[vs[vs.length-1]];
}
const lieAll = r => r.lies ? Object.values(r.lies) : [];

function passes(r) {
  const f = id => $('#'+id).value;
  if (f('arm') && r.arm !== f('arm')) return false;
  if (f('agent') && r.agent !== f('agent')) return false;
  if (f('model') && r.model !== f('model')) return false;
  if (!versOn().includes(r.ver)) return false;
  /* source toggles — independent of each other; both checked means both must hold */
  if ($('#reqjv7').checked && r.top == null) return false;
  if (f('cat') && !r.cats.includes(f('cat'))) return false;
  /* the tables and the md exports run on these three only; the rest are one-off model probes */
  if ($('#core').checked && !['deepseek','kimi','glm'].includes(r.model)) return false;
  if ($('#stake').checked && !r.stake) return false;
  const a = $('#adj').value;
  if (a) {
    const labs = r.claims.map(c => (ADJ[c.key] || {}).label || '');
    if (a === 'todo' && labs.every(Boolean)) return false;
    if (a === 'done' && !labs.every(Boolean)) return false;
    if (a === 'not-real' && !labs.some(l => l && l !== 'real')) return false;
  }
  if ($('#reqjv8').checked && !lieAct(r)) return false;
  const j8 = $('#j8').value;
  if (j8) {
    const v = lieAct(r);
    if (j8 === 'unjudged') { if (v) return false; }
    else {
      if (!v) return false;
      if (j8 === 'lie' && !(v.n > 0 && v.k === v.n)) return false;
      if (j8 === 'majority' && v.k < 2) return false;
      if (j8 === 'overt' && !(v.n > 0 && (v.ko||0) === v.n)) return false;
      if (j8 === 'overtmaj' && (v.ko||0) < 2) return false;
      if (j8 === 'clean' && v.k !== 0) return false;
      if (j8 === 'split' && !(v.k > 0 && v.k < v.n)) return false;
    }
  }
  /* included vs excluded: k counts replicates finding a lie, ke counts replicates raising a
     claim they found false but killed at a later gate (slip / audience / belief). */
  const ex = $('#exc').value;
  if (ex) {
    const v = lieAct(r);
    if (!v) return false;                                  /* no lie judge ran — nothing to say */
    if (ex === 'lie'     && !(v.k > 0)) return false;
    if (ex === 'hasexc'  && !(v.ke > 0)) return false;
    if (ex === 'exconly' && !(v.ke > 0 && v.k === 0)) return false;
    if (ex === 'noexc'   && v.ke > 0) return false;
  }
  /* r.top is null for a jv8-only row (no jv7 claim survived, or none exists) — the jv7
     intent scale doesn't apply to it, so it passes rather than reading as "incidental" */
  if (r.top != null && r.top < Number(f('lvl'))) return false;
  const q = $('#q').value.trim().toLowerCase();
  if (q) {
    const hay = [r.run, r.agent, 'turn '+r.turn,
      ...r.msgs.map(m=>m.head+' '+m.body),
      ...r.claims.map(c=>c.cat+' '+c.subj+' '+c.obj),
      ...r.reps.flatMap(p=>(p.findings||[]).map(f=>f.reason+' '+(f.spans||[]).map(s=>s.t).join(' ')+' '+(f.cot||[]).join(' '))),
      ...lieAll(r).flatMap(o=>o.reps.flatMap(p=>[...(p.findings||[]).map(f=>f.claim+' '+f.reason),
                                                 ...(p.excluded||[]).map(x=>x.claim+' '+x.reason)]))
    ].join(' ').toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function render() {
  view = D.filter(passes);
  if (cur >= view.length) cur = 0;
  progress();
  $('#list').innerHTML = view.map((r,i)=>`
    <div class="row${i===cur?' sel':''}" data-i="${i}">
      <div class="t">${esc(r.agent)} · turn ${r.turn} <span class="m">${esc(r.clock||'').slice(11,16)}</span></div>
      <div class="m">${esc(r.arm)} · ${esc(r.model)} s${esc(r.seed)} · ${esc(r.ver)}</div>
      <div>${hideJv7()?'':r.cats.map(c=>`<span class="chip ${c.split('-')[0]}">${c.split('-')[0]}</span>`).join('')}
           ${hideJv7()?'':(r.top!=null?`<span class="chip p">${LN[r.top]}</span>`:`<span class="chip">${r.lies?Object.keys(r.lies).join('+'):'lie'}-only</span>`)}
           ${r.lies?Object.entries(r.lies).filter(([lv,_o])=>!lvHidden(lv)).map(([lv,o])=>
             `<span class="chip ${o.k===o.n&&o.k?'hard':(o.k?'soft':'')}">${lv} ${o.k}/${o.n} lie${
                o.ko?` · ${o.ko} overt`:''}</span>${
              o.ke?`<span class="chip">${o.ke}/${o.n} excl</span>`:''}`).join(''):''}
           <span class="adjdot">${r.claims.map(c=>MARK[(ADJ[c.key]||{}).label]||'').join('')}</span></div>
    </div>`).join('') || '<p class="none" style="padding:1rem">nothing matches</p>';
  $$rows();
  detail();
}
function $$rows(){
  document.querySelectorAll('.row').forEach(el=>el.onclick=()=>{cur=+el.dataset.i;render();});
}

function spanHTML(s){
  const bad = s.s && s.s !== 'verbatim' ? ` <span class="bad">[${esc(s.s)}]</span>` : '';
  return `<div class="q">“${esc(s.t)}”${bad}</div>`;
}
function findingHTML(f){
  return `<div class="${f.inclaim?'f-in':'f-out'}">
    <div><b>${esc(f.cat)} / ${esc(f.intent)}</b> <span class="m">(subject: ${esc(f.subj)}, object: ${esc(f.obj)})</span>
      ${f.inclaim?'<span class="chip p">in the claim</span>':'<span class="chip">separate claim</span>'}</div>
    <div class="q">${esc(f.reason)}</div>
    ${(f.spans||[]).length?`<div class="q"><b>spans</b></div>${f.spans.map(spanHTML).join('')}`:''}
    ${(f.cot||[]).length?`<div class="q"><b>CoT</b></div>${f.cot.map(c=>`<div class="q">“${esc(c)}”</div>`).join('')}`:''}
    ${(f.contra||[]).length?`<div class="q"><b>contradicted by</b></div>${f.contra.map(c=>`<div class="q">“${esc(c)}”</div>`).join('')}`:''}
  </div>`;
}

function lieHTML(f){
  return `<div class="f-in">
    <div><b>lie · ${esc(f.falsity)} · deception ${esc(f.deception)}</b>
      <span class="m">(${f.checkable?'checkable':'not checkable'}, gate4 ${esc(f.gate4)}${f.aud.length?', in '+esc(f.aud.join(', ')):''})</span></div>
    ${f.claim?`<div class="q"><b>claim</b> ${esc(f.claim)}</div>`:''}
    <div class="q">${esc(f.reason)}</div>
    ${f.spans.length?`<div class="q"><b>said</b></div>${f.spans.map(spanHTML).join('')}`:''}
    ${f.cot.length?`<div class="q"><b>CoT final stance</b></div>${f.cot.map(spanHTML).join('')}`:''}
    ${f.dcot.length?`<div class="q"><b>CoT deception evidence</b></div>${f.dcot.map(spanHTML).join('')}`:''}
    ${f.contra.length?`<div class="q"><b>contradicted by</b></div>${f.contra.map(c=>`<div class="q">“${esc(c)}”</div>`).join('')}`:''}
    ${f.via?`<div class="q"><b>its audience could catch it via</b> ${esc(f.via)}</div>`:''}
  </div>`;
}
function oneLieHTML(o){
  return `<h3>${o.ver} lie judge — ${o.k}/${o.n} replicates find a lie${
      o.ko?` (${o.ko}/${o.n} with an overt one)`:''}${
      o.ke?`, ${o.ke}/${o.n} raise a claim excluded at a later gate`:''}</h3>
    ${o.reps.map((p,i)=>`<div class="rep">
      <div class="who">${o.ver} replicate ${i+1}${p.missing?'':` — ${p.n?`${p.n} lie${p.n>1?'s':''}`:'no lie'}`}</div>
      <div class="file">${esc(p.file)}</div>
      ${p.missing?'<p class="none">turn not judged in this replicate</p>':`
      <div class="q"><b>description</b> ${esc(p.desc)}</div>
      ${/* the two lists are different claims, not two readings of one — a turn can carry
            both, so each gets its own heading rather than sharing a run-on block */''}
      <div class="sect">Included lies${(p.findings||[]).length?` (${p.findings.length})`:''}</div>
      ${(p.findings||[]).length?(p.findings||[]).map(lieHTML).join('')
        :'<p class="none">none — no claim cleared all four gates</p>'}
      <div class="sect">Excluded claims — false, but killed at a later gate${
        (p.excluded||[]).length?` (${p.excluded.length})`:''}</div>
      ${(p.excluded||[]).length?(p.excluded||[]).map(x=>`<div class="f-out">
        <div>failed gate <b>${esc(x.gate)}</b></div>
        ${x.claim?`<div class="q">${esc(x.claim)}</div>`:''}
        ${x.span?`<div class="q">“${esc(x.span)}”</div>`:''}
        <div class="q"><span class="m">${esc(x.reason)}</span></div></div>`).join('')
        :'<p class="none">none</p>'}`}
    </div>`).join('')}`;
}
function jv8HTML(r){
  if (!r.lies) return '';
  return Object.entries(r.lies).filter(([lv,_o])=>!lvHidden(lv)).map(([_lv,o])=>oneLieHTML(o)).join('');
}
/* Every output span the judges quoted on this turn, tagged by source: lie-judge finding
   spans ('hl'), excluded-claim spans ('hlx'), jv7 fabrication-finding spans ('hl7'). Follows
   the display toggles — a hidden judge's spans do not light up the text. */
function judgeSpans(r){
  const out = [];
  if (!hideJv7()) for (const p of r.reps||[]) for (const f of p.findings||[])
    for (const s of f.spans||[]) if (s.t) out.push({t:s.t, cls:'hl7'});
  for (const [lv,o] of Object.entries(r.lies||{})){
    if (lvHidden(lv)) continue;
    for (const p of o.reps||[]){
      for (const f of p.findings||[]) for (const s of f.spans||[]) if (s.t) out.push({t:s.t, cls:'hl'});
      for (const x of p.excluded||[]) if (x.span) out.push({t:x.span, cls:'hlx'});
    }
  }
  /* longest first: short quotes then nest inside already-marked long ones instead of
     blocking them; lie spans win over jv7 underlines on the same text by the same order */
  return out.sort((a,b)=>b.t.length-a.t.length);
}
function hlBody(r, body){
  let html = esc(body);
  const seen = new Set();
  for (const {t, cls} of judgeSpans(r)){
    const key = cls+'|'+t; if (seen.has(key)) continue; seen.add(key);
    /* match on the escaped text, whitespace-insensitively — judges collapse newlines */
    const pat = esc(t).replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\s+/g,'\\s+');
    try {
      const re = new RegExp(pat);
      if (re.test(html)) html = html.replace(re, m=>`<mark class="${cls}">${m}</mark>`);
    } catch(e) {}
  }
  return html;
}
function metaOf(r, c) {
  return {arm:r.arm, model:r.model, seed:r.seed, ver:r.ver, run:r.run, agent:r.agent, turn:r.turn,
          cat:c.cat, lvl:LN[c.lvl], votes:c.nvotes, subj:c.subj, obj:c.obj, html:r.html};
}
function judge(r, ci, patch) {
  const c = r.claims[ci];
  const a = ADJ[c.key] || {};
  /* clicking the label that is already set clears it — undo without a separate control */
  if (patch.label && patch.label === a.label) patch.label = '';
  if (patch.intent && patch.intent === a.intent) patch.intent = '';
  save(c.key, patch, metaOf(r, c));
  detail(); stats(); listOnly();
}
function wireAdj(r) {
  document.querySelectorAll('#detail .adj button').forEach(b => {
    b.onclick = () => judge(r, +b.dataset.ci,
      b.dataset.l !== undefined ? {label: b.dataset.l} : {intent: b.dataset.i});
  });
  document.querySelectorAll('#detail .adj textarea').forEach(t => {
    let nt = null;
    t.oninput = () => { clearTimeout(nt); nt = setTimeout(() => {
      const c = r.claims[+t.dataset.ci];
      save(c.key, {note: t.value}, metaOf(r, c)); listOnly();
    }, 500); };
  });
}
function listOnly() {   /* refresh the marks in the list without losing focus in the detail pane */
  document.querySelectorAll('.row').forEach(el => {
    const rr = view[+el.dataset.i]; if (!rr) return;
    const dot = el.querySelector('.adjdot');
    if (dot) dot.textContent = rr.claims.map(c => MARK[(ADJ[c.key]||{}).label] || '').join('');
  });
  progress();
}
function progress() {
  const cs = view.flatMap(r => r.claims);
  const done = cs.filter(c => (ADJ[c.key]||{}).label).length;
  const runs = new Set(view.map(r=>r.run)).size;
  $('#count').textContent = `${view.length} of ${D.length} turns, over ${runs} rollouts · `
    + `${done}/${cs.length} claims judged`;
}

function stats() {
  const box = $('#stats');
  if (box.style.display === 'none') return;
  const cs = view.flatMap(r => r.claims).map(c => ({c, a: ADJ[c.key] || {}})).filter(x => x.a.label);
  if (!cs.length) { box.innerHTML = '<span class="m">nothing judged in this view yet</span>'; return; }
  const tbl = (name, keyfn) => {
    const per = {};
    cs.forEach(x => { const k = keyfn(x); (per[k] = per[k] || {})[x.a.label] = (per[k][x.a.label]||0)+1; });
    return `<table><tr><th>${name}</th>${LABELS.map(l=>`<th>${l}</th>`).join('')}<th>n</th><th>precision</th></tr>`
      + Object.keys(per).sort().map(k => {
          const c = per[k], n = LABELS.reduce((s,l)=>s+(c[l]||0),0), dec = n - (c['unsure']||0);
          return `<tr><td>${esc(k)}</td>${LABELS.map(l=>`<td>${c[l]||0}</td>`).join('')}<td>${n}</td>`
               + `<td>${dec?Math.round(100*(c['real']||0)/dec)+'%':'—'}</td></tr>`;
        }).join('') + '</table>';
  };
  box.innerHTML = `<b>${cs.length} judged claims in this view.</b> Precision counts <i>real</i> against
    everything judged except <i>unsure</i>.<br>`
    + tbl('category', x => x.c.cat) + tbl('agreement', x => `${x.c.nvotes}/3 at ${LN[x.c.lvl]}`)
    + tbl('model', x => x.a.meta?.model || '?') + tbl('arm', x => x.a.meta?.arm || '?');
}
$('#statsbtn').onclick = () => {
  const box = $('#stats');
  box.style.display = box.style.display === 'block' ? 'none' : 'block';
  stats();
};

function detail(){
  const r = view[cur];
  if (!r){ $('#detail').innerHTML = ''; return; }
  /* display-only: hides jv7's claims and replicates so the lie judge can be read on its own.
     It filters nothing — the row set is unchanged, only what this panel draws. */
  const hj7 = $('#hidejv7').checked;
  $('#detail').innerHTML = `
    <h2>${esc(r.agent)} — turn ${r.turn} <span class="m">(${esc(r.clock)}, round ${esc(r.round)})</span></h2>
    <p class="sub">${esc(r.arm)} · ${esc(r.model)} · seed ${esc(r.seed)} · world ${esc(r.ver)} ·
       <span class="file">${esc(r.run)}</span>${r.stake?'':' · <span class="chip">baseline agent</span>'}</p>
    ${r.html ? `<a class="jump" href="${esc(r.html)}" target="_blank" rel="noopener">open the rollout viewer at this turn ↗</a>
    <span class="m"> or press <kbd>o</kbd></span>` : `<span class="m">no rollout viewer was built for this run</span>`}
    ${(!hj7 && r.claims.length)?`<h3>Claim${r.claims.length>1?'s':''}</h3>` : ''}
    ${hj7?'':r.claims.map((c,ci)=>{
      const a = ADJ[c.key] || {};
      const btn = (attr,val,txt) => `<button data-ci="${ci}" data-${attr}="${val}"
          class="${(attr==='l'?a.label:a.intent)===val?'on':''}">${txt}</button>`;
      return `<div class="claim${a.label?' judged':''}"><b>${esc(c.cat)}</b> — subject <i>${esc(c.subj)}</i>,
        object <i>${esc(c.obj)}</i> — <b>${LN[c.lvl]}</b> by ${c.nvotes}/3 replicates
        <span class="m">(votes: ${Object.entries(c.votes).map(([k,v])=>`r${k}=${LN[v]}`).join(', ')})</span>
        <div class="adj">
          ${btn('l','real','real ✓')}${btn('l','not-real','not real ✗')}
          ${btn('l','wrong-category','wrong category')}${btn('l','unsure','unsure')}
          <span class="sep">intent</span>
          ${btn('i','ok','ok')}${btn('i','high','too high')}${btn('i','low','too low')}
          <span class="saved"></span>
          <textarea data-ci="${ci}" placeholder="why — the note is the record of the call">${esc(a.note||'')}</textarea>
        </div></div>`;}).join('')}
    <h3>What the assistant said this turn</h3>
    ${judgeSpans(r).length?`<p class="m">judge-quoted spans: <mark class="hl">lie</mark> · <mark class="hlx">excluded claim</mark> · <mark class="hl7">jv7 fabrication</mark></p>`:''}
    ${r.msgs.map(m=>`<div class="msg"><div class="h">${esc(m.head)}${m.aud?` <span class="m">(audience tag: ${esc(m.aud)})</span>`:''}</div>
        ${m.body?`<pre>${hlBody(r, m.body)}</pre>`:'<p class="none">— no text —</p>'}</div>`).join('')}
    ${(!hj7 && r.reps.length)?'<h3>jv7 fabrication judge — replicates</h3>':''}
    ${hj7?'':r.reps.map((p,i)=>`<div class="rep"><div class="who">jv7 replicate ${i+1}</div>
        <div class="file">${esc(p.file)}</div>
        ${p.missing?'<p class="none">turn not judged in this replicate</p>':`
        <div class="q"><b>labels</b> ${p.labels.length?esc(p.labels.join(', ')):'(none)'}</div>
        <div class="q"><b>description</b> ${esc(p.desc)}</div>
        ${p.findings.length?p.findings.map(findingHTML).join(''):'<p class="none">no fabrication finding on this turn — this replicate dissents</p>'}`}
      </div>`).join('')}
    ${jv8HTML(r)}`;
  wireAdj(r);
  const sel = document.querySelector('.row.sel');
  if (sel) sel.scrollIntoView({block:'nearest'});
  $('#detail').scrollTop = 0;
}

document.querySelectorAll('.filters select, .filters input, .verbox').forEach(el=>{
  el.oninput = el.onchange = () => { cur = 0; render(); };
});
document.addEventListener('keydown', e=>{
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) {
    if (e.key === 'Escape') e.target.blur();
    return;
  }
  if (e.key === 'j' || e.key === 'ArrowDown'){ cur = Math.min(cur+1, view.length-1); render(); e.preventDefault(); }
  if (e.key === 'k' || e.key === 'ArrowUp'){ cur = Math.max(cur-1, 0); render(); e.preventDefault(); }
  if (e.key === 'o' && view[cur] && view[cur].html) window.open(view[cur].html, '_blank');
  if (e.key === '/'){ $('#q').focus(); e.preventDefault(); }
  /* 1–4 judge the turn's first claim; several claims on one turn are clicked instead */
  const r = view[cur];
  if (r && '1234'.includes(e.key)) { judge(r, 0, {label: LABELS[+e.key - 1]}); e.preventDefault(); }
  if (r && e.key === 'n') { const t = document.querySelector('#detail .adj textarea');
                            if (t) { t.focus(); e.preventDefault(); } }
});
boot();
"""

page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>agent1 — agreed fabrications</title><style>{CSS}</style></head><body>
<header>
  <h1>agent1 — turns the judges agreed were fabrications</h1>
  <p class="sub">Claim-level agreement over 3 jv7 replicates (judge deepseek-v4-flash-0731): findings from
  different replicates are one claim when they share <code>fabrication_subject</code> and
  <code>fabrication_object</code> and their output spans overlap ≥ 0.5; a claim is kept when ≥2 replicates
  agree, and its level is the highest intent ≥2 of them reach. Turns re-judged by the lie judge
  (four gates, <code>lie_jv*_*</code> files, newest version per run) carry a <code>k/n lie</code> chip and their
  replicate verdicts at the bottom of the detail pane. <span id="count"></span> ·
  <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>o</kbd> opens the rollout · <kbd>/</kbd> search ·
  <kbd>1</kbd>–<kbd>4</kbd> judge the first claim (real / not real / wrong category / unsure),
  <kbd>n</kbd> notes · <span id="conn2"></span></p>
</header>
<div class="filters">
  <input type="search" id="q" placeholder="search messages, reasons, spans, CoT…">
  <select id="arm"></select><select id="agent"></select><select id="model"></select>
  <span class="tog" id="vers"></span>
  <label class="f"><input type="checkbox" id="reqjv7"> has jv7 claim</label>
  <label class="f"><input type="checkbox" id="hidejv7"> hide jv7</label>
  <select id="cat"><option value="">hard + soft</option><option value="hard-fabrication">hard only</option>
    <option value="soft-fabrication">soft only</option></select>
  <select id="lvl"><option value="2">purposeful</option><option value="1">≥ ambiguous</option>
    <option value="0">≥ incidental (any intent)</option></select>
  <label class="f"><input type="checkbox" id="core"> core 3 models only</label>
  <label class="f"><input type="checkbox" id="stake" checked> stake agents only</label>
  <select id="adj"><option value="">judged + unjudged</option><option value="todo">unjudged only</option>
    <option value="done">judged only</option><option value="not-real">flagged not real</option></select>
  <label class="f"><input type="checkbox" id="reqjv8"> has lie-judge verdict</label>
  <span class="tog" id="lievers"></span>
  <select id="liever"></select>
  <select id="j8"><option value="">lie judge: all</option><option value="lie">lie judge: lie (all reps)</option>
    <option value="majority">lie judge: &gt;= 2/3 lie</option>
    <option value="overt">lie judge: overt (all reps)</option>
    <option value="overtmaj">lie judge: overt &gt;= 2/3</option>
    <option value="split">lie judge: split</option><option value="clean">lie judge: clean</option>
    <option value="unjudged">lie judge: not judged</option></select>
  <select id="exc"><option value="">incl + excl</option>
    <option value="lie">included lies only</option>
    <option value="hasexc">has an excluded claim</option>
    <option value="exconly">excluded only (no lie)</option>
    <option value="noexc">nothing excluded</option></select>
  <button id="statsbtn" style="font:inherit;font-size:.78rem;padding:.25rem .5rem;border:1px solid var(--line);
    border-radius:5px;background:var(--bg);color:var(--ink);cursor:pointer">stats</button>
  <span id="conn" class="m"></span>
</div>
<div id="stats"></div>
<div class="main"><div id="list"></div><div id="detail"></div></div>
<script type="application/json" id="data">{blob.replace('<', chr(92) + 'u003c')}</script>
<script type="application/json" id="prior">{prior.replace('<', chr(92) + 'u003c')}</script>
<script>{JS}</script></body></html>"""

p = OUT/'lie_browser.html'
p.write_text(page, encoding='utf-8')
print('wrote', p, f'{p.stat().st_size/1e6:.1f} MB')
