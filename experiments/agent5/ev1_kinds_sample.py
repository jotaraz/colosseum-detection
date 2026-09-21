"""Collapsible per-`kind` example sheet over the ev1 eval-awareness sidecars.

    .venv/bin/python experiments/agent5/ev1_kinds_sample.py

Groups every flagged chunk (`aware`/`ambiguous`) of the 5.e.viii sweep by the judge's `kind`,
then samples up to 10 from the focal seats (Nadia, Priya) and up to 5 from the rest of the cast.
Reads the per-chunk kinds straight from `chunks[]` — never `per_agent`, whose `kind` is only the
first strongest chunk's label. Writes findings/eval-awareness-kinds.md as <details> blocks, each
item summarised by its first span. Seeded, so the same seed reproduces the same sheet.
"""

import json, glob, random, re
from collections import defaultdict

FOCAL = ('Nadia', 'Priya')
bykind = defaultdict(lambda: {'focal': [], 'other': []})
for f in sorted(glob.glob('experiments/agent5/runs/*DmHint2Strict_conc_*/run.cot_evalaware_ev1_gpt56terra.json')):
    d = json.load(open(f))
    m = re.search(r'_conc_([a-z0-9]+)_s(\d+)_', d['run'])
    seats = {a: {c['kind'] for c in d['chunks']
                 if c['agent'] == a and c['verdict'] in ('aware', 'ambiguous')}
             for a in {c['agent'] for c in d['chunks']}}
    for c in d['chunks']:
        if c['verdict'] in ('aware', 'ambiguous'):
            row = {'seat': '%s-%s-s%s' % (c['agent'], m.group(1), m.group(2)),
                   'run': d['run'], 'model': d['model'].split('/')[-1],
                   'chunk': c['chunk'], 'n_chunks': c['n_chunks'], 'first_step': c['first_step'],
                   'verdict': c['verdict'], 'conf': c['confidence'], 'reason': c['reason'],
                   'spans': c['spans'], 'sv': c.get('spans_verbatim') or [],
                   'pure': len(seats[c['agent']]) == 1}
            bykind[c['kind']]['focal' if c['agent'] in FOCAL else 'other'].append(row)

def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


rng = random.Random(20260917)
L = ['# ev1 / gpt-5.6-terra — flagged chunks by `kind`', '',
     'Per kind: up to 10 examples from the focal seats (Nadia, Priya) and up to 5 from the rest of',
     'the cast (Helena, Matthieu, Rafael), drawn at random with seed 20260917 from the 320 flagged',
     'chunks of the 5.e.viii sweep. Each chunk carries exactly one `kind`, straight from the sidecar',
     '`chunks[]`. "single-kind seat" says whether the rest of that seat agreed on the kind.', '',
     'Every kind and every item below is collapsible — click to expand. Each item\'s summary carries',
     'its first span, so the collapsed view reads as a list of quotes.', '',
     'Some cells are short of the quota — `graded-or-scored` has no focal chunk at all (all 6 are',
     'Rafael), and `outside-author` has only 9 focal and 3 non-focal. Counts are given per section.', '',
     '> The kinds are never defined in `CRITIC_EVALAWARE.md` — they appear once, at line 77, as a',
     '> bare enum in the output schema. These examples are the only guide to where the judge draws',
     '> the lines.', '']


def block(prefix, rows, take, note=''):
    out = []
    if not rows:
        return ['_None._', '']
    picked = rows if len(rows) <= take else rng.sample(rows, take)
    for i, r in enumerate(picked, 1):
        lead = (r['spans'][0] if r['spans'] else '(no span returned)').replace('\n', ' ')
        if len(lead) > 120:
            lead = lead[:120] + '…'
        out += ['<details>',
                '<summary><b>%s.%d</b> <code>%s</code> — %s</summary>'
                % (prefix, i, r['seat'], esc(lead)), '',
                '%s · chunk %d/%d (from step `%s`) · verdict `%s` · confidence `%s` · '
                'single-kind seat: %s'
                % (r['model'], r['chunk'] + 1, r['n_chunks'], r['first_step'], r['verdict'],
                   r['conf'], 'yes' if r['pure'] else 'no'), '',
                '**Spans.**', '']
        for j, s in enumerate(r['spans']):
            ok = r['sv'][j] if j < len(r['sv']) else None
            out += ['> %s' % s.replace('\n', ' ')]
            if not ok:
                out += ['>', '> — *not verbatim; snapped to nearby text*']
            out += ['']
        if not r['spans']:
            out += ['_(none returned)_', '']
        out += ['**Judge:** %s' % r['reason'], '', '</details>', '']
    return out


for k in ['being-tested', 'fictional-world', 'graded-or-scored', 'outside-author',
          'training-or-rl', 'none']:
    foc, oth = bykind[k]['focal'], bykind[k]['other']
    head = '<code>%s</code> — %d flagged chunks (%d focal, %d other)' % (k, len(foc) + len(oth), len(foc), len(oth))
    if k == 'none':
        head += ' — schema violation'
    if not foc and not oth:
        L += ['<details>', '<summary><b>%s</b></summary>' % head, '',
              '_Never emitted in this sweep._', '', '</details>', '']
        continue
    L += ['<details open>', '<summary><b>%s</b></summary>' % head, '']
    if k == 'none':
        L += ['The judge returned an `aware`/`ambiguous` verdict while labelling the kind `none` — the two',
              'fields contradict each other. Every such chunk is listed here, not a sample.', '']
    for label, rows, take, tag in [('Nadia / Priya', foc, 10, 'F'),
                                   ('Helena / Matthieu / Rafael', oth, 5, 'O')]:
        L += ['<details open>',
              '<summary>%s — %d of %d shown</summary>' % (label, min(take, len(rows)), len(rows)), '']
        L += block(k[:4].upper() + tag, rows, take)
        L += ['</details>', '']
    L += ['</details>', '']

out = 'experiments/agent5/findings/eval-awareness-kinds.md'
open(out, 'w').write('\n'.join(L))
print('wrote', out)
for k in bykind:
    print('  %-18s focal %3d  other %3d' % (k, len(bykind[k]['focal']), len(bykind[k]['other'])))
