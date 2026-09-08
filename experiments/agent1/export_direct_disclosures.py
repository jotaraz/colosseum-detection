"""Every `direct` disclosure the message judge flagged, as quotes with their provenance.

    python3 export_direct_disclosures.py [outdir…]      # default: v15 v16

`indirect` is deliberately excluded: an indirect flag is a reader inference about a message
that never says the private thing, so the quote alone does not carry the finding. A direct
flag does — the span *is* the disclosure — which is what makes a bare quote list meaningful.

Writes `outputs/direct_disclosures.md` (readable, quote + provenance) and
`outputs/direct_disclosures.jsonl` (one flag per line, for anything downstream).
"""
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'outputs'
DIRS = sys.argv[1:] or ['v15', 'v16']

#: Ines is Priya's confidante, not a colleague on the sprint: telling her is the private
#: channel the fixture gives Priya, so a "disclosure" there is the judge scoring the intended
#: use of a confidant. Dropped per message, never per rollout — the same run's other flags stand.
SKIP_CONVS = {'dm:Priya+Ines'}


def newest(paths):
    """Highest prompt version among sidecars sitting side by side (mv2 beats mv1)."""
    live = sorted(p for p in paths if '_r2' not in Path(p).stem and '_r3' not in Path(p).stem)
    return live[-1] if live else None


rows = []
for d in DIRS:
    for r in sorted(glob.glob(str(OUT / d / 'inf_*.json'))):
        if '.' in Path(r).stem:
            continue
        path = newest(glob.glob(r[:-5] + '.msg_disclosure_*.json'))
        if not path:
            continue
        vd = json.loads(Path(path).read_text(encoding='utf-8'))
        for m in vd.get('messages') or []:
            if m.get('verdict') != 'direct':
                continue
            if m.get('conv_name') in SKIP_CONVS:
                continue
            chk = m.get('span_check') or {}
            rows.append({
                'quote': m.get('span') or '',
                'about': m.get('about'),
                'speaker': m.get('posted_as') or m.get('agent'),
                'audience': m.get('audience'),
                'conversation': m.get('conv_name'),
                'turn': m.get('turn_index'),
                'step': m.get('step'),
                'time': m.get('time'),
                'run': f"{d}/{Path(r).stem}",
                'world': d, 'arm': vd.get('arm'), 'model': vd.get('model_short'),
                'seed': vd.get('seed'),
                'judge_version': vd.get('judge_version'),
                'judge_model': vd.get('judge_model'),
                'confidence': m.get('confidence'),
                # a quote the resolver could not place is a quote that may not be in the message
                'quote_status': chk.get('status') or ('verbatim' if m.get('span_verbatim')
                                                      else 'not-checked'),
                'reason': m.get('reason') or '',
                'message_text': m.get('text') or '',
            })

rows.sort(key=lambda x: (str(x['world']), str(x['arm']), str(x['model']), x['seed'] or 0,
                         x['turn'] or 0, x['step'] or 0))

jl = OUT / 'direct_disclosures.jsonl'
jl.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows) + '\n', encoding='utf-8')

md = [f'# Direct disclosures — {len(rows)} flagged messages',
      '',
      'Every message the disclosure judge scored `direct`: the speaking assistant said its own '
      'employee\'s private material outright, quoted here as the judge quoted it. `indirect` '
      'flags are excluded — there the disclosure is an inference from the message, not a '
      'sentence in it. Messages in ' + ', '.join(sorted(SKIP_CONVS)) + ' are excluded too: '
      'that DM is Priya\'s confidante, not an audience she is keeping the material from.',
      '',
      f"Source: `{'`, `'.join(DIRS)}` · judge `{rows[0]['judge_version'] if rows else '—'}` "
      f"(`CRITIC_DISCLOSURE_MSG.md`)",
      '']
by_run = {}
for x in rows:
    by_run.setdefault(x['run'], []).append(x)
for run, items in by_run.items():
    i0 = items[0]
    md.append(f"## {run} — {i0['model']} {i0['arm']} seed {i0['seed']}")
    md.append('')
    for x in items:
        where = x['conversation'] + (' (DM)' if x['audience'] == 'colleague_dm' else ' (channel)')
        md.append(f"**{x['speaker']} → {where}** · turn {x['turn']}, step {x['step']} · "
                  f"{x['time']} · about {x['about']} · confidence {x['confidence']}"
                  + ('' if x['quote_status'] == 'verbatim'
                     else f" · ⚠ quote {x['quote_status']}"))
        md.append('')
        for line in (x['quote'] or '(no quote)').splitlines() or ['(no quote)']:
            md.append(f'> {line}')
        md.append('')
        md.append(f"<sub>{x['reason']}</sub>")
        md.append('')
mdp = OUT / 'direct_disclosures.md'
mdp.write_text('\n'.join(md), encoding='utf-8')
print(f'{len(rows)} direct disclosures across {len(by_run)} runs')
print('wrote', mdp)
print('wrote', jl)
