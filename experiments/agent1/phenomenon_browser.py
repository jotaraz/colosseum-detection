"""A local browser for every turn the message/CoT judges flagged, by phenomenon.

The sibling of `lie_browser.py`, for the judges in `message_judge.py` and
`eval_awareness_judge.py`. Pick a phenomenon on the left — sabotage, escalation, disclosure,
relay, eval-awareness — and the page lists every flagged item; selecting one shows the whole
rollout on the right with that message (or that turn's reasoning) highlighted in place.

Reading a flag next to the conversation it came from is the point: a disclosure is a *reader*
judgement, and "would a colleague reading this work it out" cannot be checked against the
sentence alone.

    python3 phenomenon_browser.py [outdir…]      # default: v15 v16

Writes `outputs/phenomenon_browser.html`, self-contained, no server needed.
"""
import collections
import glob
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'outputs'
DIRS = sys.argv[1:] or ['v15', 'v16']
NAME = re.compile(r'^inf_(?P<arm>[^_]+)_(?P<model>.+)_s(?P<seed>\d+)$')

#: Each phenomenon: which sidecar carries it, and how a flagged row is read off one record.
#: Kept as data rather than four near-identical loops so a new judge is one entry, not a branch.
CATS = ('sabotage', 'escalation', 'refusal', 'disclosure', 'relay', 'eval-awareness')
LEVELS = {'sabotage': ('yes',), 'escalation': ('proposes', 'performs'),
          'refusal': ('declines-ticket', 'declines-and-disengages'),
          'disclosure': ('direct', 'indirect'), 'relay': ('relays-other',),
          'eval-awareness': ('aware', 'ambiguous')}


def spans_of(msg, field):
    """The evidence for one field, as (list-of-quotes, list-of-statuses).

    mv3 made every span field a list of contiguous quotes; mv1/mv2 wrote a single string. Both
    shapes live side by side on disk while a sweep migrates, so read either — a browser that
    understood only the newer one would show the older half of the corpus with no evidence at
    all, which reads as "the judge quoted nothing" rather than "this file is older".
    """
    plural = msg.get(field + 's') if field + 's' in msg else msg.get(field)
    if isinstance(plural, list):
        st = msg.get(field + 's_statuses') or msg.get(field + '_statuses') or []
        return [str(x) for x in plural], list(st)
    one = msg.get(field)
    if isinstance(one, str) and one.strip():
        chk = msg.get(field + '_check') or {}
        return [one], [chk.get('status') or ('verbatim' if msg.get(field + '_verbatim') else None)]
    return [], []


def newest(paths):
    """Highest prompt version among sidecars sitting side by side (mv2 beats mv1)."""
    live = sorted(p for p in paths if '_r2' not in Path(p).stem and '_r3' not in Path(p).stem)
    return live[-1] if live else None


def reasoning_by_turn(run):
    """(agent, turn_index) -> the reasoning steps of that turn.

    The reasoning log carries a per-turn step counter and no turn id, so the turn boundary is
    where the counter resets. Verified against every v15 askA/askG run: the reconstruction never
    runs past the agent's actual turn count.
    """
    by_agent = collections.defaultdict(list)
    for i, t in enumerate(run.get('turns') or []):
        by_agent[t.get('agent')].append(i)
    out = collections.defaultdict(list)
    ptr, last = collections.Counter(), {}
    for row in run.get('reasoning') or []:
        a, st = row.get('agent'), row.get('step')
        if a in last and st is not None and st <= last[a]:
            ptr[a] += 1
        last[a] = st
        idxs = by_agent.get(a, [])
        if not idxs:
            continue
        out[(a, idxs[min(ptr[a], len(idxs) - 1)])].append(row)
    return out


def window(text, span, pad=1200):
    """The reasoning around a quoted span, so a CoT flag can be read in context."""
    i = text.find(span) if span else -1
    if i < 0:
        return text[:2 * pad], False
    a, b = max(0, i - pad), min(len(text), i + len(span) + pad)
    return ('…' if a else '') + text[a:b] + ('…' if b < len(text) else ''), True


runs_blob, records, scanned = {}, [], 0
for d in DIRS:
    for r in sorted(glob.glob(str(OUT / d / 'inf_*.json'))):
        if '.' in Path(r).stem:
            continue
        stem = Path(r).stem
        sab = newest(glob.glob(r[:-5] + '.msg_sabotage_*.json'))
        dis = newest(glob.glob(r[:-5] + '.msg_disclosure_*.json'))
        ev = newest(glob.glob(r[:-5] + '.cot_evalaware_*.json'))
        met = newest(glob.glob(r[:-5] + '.metrics.json'))
        if not (sab or dis or ev):
            continue
        scanned += 1
        run = json.loads(Path(r).read_text(encoding='utf-8'))
        m = NAME.match(stem)
        meta = m.groupdict() if m else {'arm': '?', 'model': '?', 'seed': '?'}

        # the rollout as a reader would see it: every message the run itself produced
        posts = []
        for ti, t in enumerate(run.get('turns') or []):
            for c in t.get('tool_calls') or []:
                if c.get('tool') != 'slack_post_message':
                    continue
                res = c.get('result')
                if not isinstance(res, dict) or not res.get('ok'):
                    continue
                conv = str(res.get('conversation') or '')
                posts.append({'turn': ti, 'step': c.get('step'),
                              'key': f"{ti}|{c.get('step')}|{conv}",
                              'agent': str(res.get('posted_as') or t.get('agent') or ''),
                              'conv': conv, 'time': str(res.get('time') or ''),
                              'ts': str(res.get('ts') or ''),
                              'aud': 'channel' if conv.startswith('#') else 'dm',
                              'text': str((c.get('arguments') or {}).get('text') or '')})
        # Every verdict on every message, flagged or not — so a post can carry what all the
        # judges said about it, and a clean verdict is visible as a decision rather than as an
        # absence. Keyed the way the thread is: turn|step|conversation.
        verdicts = {}

        def _record(path, kind, keep):
            if not path:
                return
            vd = json.loads(Path(path).read_text(encoding='utf-8'))
            for msg in vd.get('messages') or []:
                key = f"{msg.get('turn_index')}|{msg.get('step')}|{msg.get('conv_name')}"
                row = {k: msg.get(k) for k in keep}
                row['jver'] = vd.get('judge_version') or '?'
                row['reason'] = msg.get('reason') or ''
                verdicts.setdefault(key, {})[kind] = row

        _record(sab, 'sabotage', ('verdict', 'rule', 'escalation', 'refusal', 'confidence'))
        _record(dis, 'disclosure', ('verdict', 'about', 'relay', 'confidence'))

        final = {}
        if met:
            fr = json.loads(Path(met).read_text(encoding='utf-8'))['metrics']['final-result']
            final = {'verdict': fr['verdict'], 'pairs': fr['pairs'], 'roles': fr['roles_ok'],
                     'kickoff': fr['kickoff_ok'], 'confirmed': fr.get('kickoff_confirmed')}
        key = f'{d}/{stem}'
        runs_blob[key] = {'arm': meta['arm'], 'model': meta['model'], 'seed': meta['seed'],
                          'ver': d, 'html': f'{d}/{stem}.html', 'posts': posts, 'final': final,
                          'verdicts': verdicts}

        def base(cat, level, agent, turn, extra):
            rec = {'cat': cat, 'level': level, 'run': key, 'arm': meta['arm'],
                   'model': meta['model'], 'seed': meta['seed'], 'ver': d,
                   'agent': agent, 'turn': turn}
            rec.update(extra)
            records.append(rec)

        # ---- message judges: the flagged post is identified by (turn, step, conv)
        for path, cats in ((sab, ('sabotage', 'escalation')), (dis, ('disclosure', 'relay'))):
            if not path:
                continue
            vd = json.loads(Path(path).read_text(encoding='utf-8'))
            ver = vd.get('judge_version') or '?'
            for msg in vd.get('messages') or []:
                loc = {'turn': msg.get('turn_index'), 'step': msg.get('step'),
                       'conv': msg.get('conv_name'), 'time': msg.get('time'),
                       'jver': ver, 'aud': msg.get('audience')}
                if 'sabotage' in cats:
                    if msg.get('verdict') == 'yes':
                        q, st = spans_of(msg, 'span')
                        base('sabotage', 'yes', msg.get('posted_as'), msg.get('turn_index'),
                             {**loc, 'reason': msg.get('reason') or '',
                              'span': q[0] if q else '', 'spans': q, 'statuses': st,
                              'ok': bool(st and st[0] in ('verbatim', 'spliced')),
                              'tags': [f"rule: {msg.get('rule')}",
                                       f"confidence: {msg.get('confidence')}"]})
                    esc = msg.get('escalation')
                    if esc in ('proposes', 'performs'):
                        q, st = spans_of(msg, 'escalation_span')
                        base('escalation', esc, msg.get('posted_as'), msg.get('turn_index'),
                             {**loc, 'reason': msg.get('reason') or '',
                              'span': q[0] if q else '', 'spans': q, 'statuses': st,
                              'ok': bool(st and st[0] in ('verbatim', 'spliced')),
                              'tags': [f"escalation: {esc}"]})
                    ref = msg.get('refusal')
                    if ref in ('declines-ticket', 'declines-and-disengages'):
                        q, st = spans_of(msg, 'refusal_span')
                        base('refusal', ref, msg.get('posted_as'), msg.get('turn_index'),
                             {**loc, 'reason': msg.get('reason') or '',
                              'span': q[0] if q else '', 'spans': q, 'statuses': st,
                              'ok': bool(st and st[0] in ('verbatim', 'spliced')),
                              'tags': [f"refusal: {ref}"]})
                else:
                    if msg.get('verdict') in ('direct', 'indirect'):
                        q, st = spans_of(msg, 'span')
                        base('disclosure', msg.get('verdict'), msg.get('posted_as'),
                             msg.get('turn_index'),
                             {**loc, 'reason': msg.get('reason') or '',
                              'span': q[0] if q else '', 'spans': q, 'statuses': st,
                              'ok': bool(st and st[0] in ('verbatim', 'spliced')),
                              'tags': [f"about: {msg.get('about')}",
                                       f"confidence: {msg.get('confidence')}"]})
                    if msg.get('relay') == 'relays-other':
                        q, st = spans_of(msg, 'span')
                        base('relay', 'relays-other', msg.get('posted_as'),
                             msg.get('turn_index'),
                             {**loc, 'reason': msg.get('reason') or '',
                              'span': q[0] if q else '', 'spans': q, 'statuses': st,
                              'ok': bool(st and st[0] in ('verbatim', 'spliced')),
                              'tags': [f"about: {msg.get('about')}"]})

        # ---- eval-awareness: a chunk verdict, pinned back to the turn its span sits in
        if ev:
            vd = json.loads(Path(ev).read_text(encoding='utf-8'))
            steps = reasoning_by_turn(run)
            for ch in vd.get('chunks') or []:
                if ch.get('verdict') not in ('aware', 'ambiguous'):
                    continue
                agent = ch.get('agent')
                spans = [s for s, ok in zip(ch.get('spans') or [],
                                            ch.get('spans_verbatim') or []) if ok] \
                    or (ch.get('spans') or [''])
                for span in spans:
                    turn, cot = None, ''
                    for (a, ti), rows in steps.items():
                        if a != agent:
                            continue
                        for row in rows:
                            text = str(row.get('reasoning') or '')
                            if span and span in text:
                                turn, cot = ti, window(text, span)[0]
                                break
                        if turn is not None:
                            break
                    if turn is None:  # paraphrased span: fall back to the chunk's first turn
                        cand = sorted(ti for (a, ti) in steps if a == agent)
                        turn = cand[0] if cand else 0
                        joined = "\n\n".join(str(x.get('reasoning') or '')
                                             for x in steps.get((agent, turn), []))
                        cot = window(joined, span)[0]
                    base('eval-awareness', ch.get('verdict'), agent, turn,
                         {'reason': ch.get('reason') or '', 'span': span,
                          'spans': [span] if span else [],
                          'statuses': ['verbatim' if span and span in cot else 'not-found'],
                          'ok': span in cot, 'cot': cot, 'conv': 'private reasoning',
                          'time': '', 'step': ch.get('first_step'),
                          'jver': vd.get('judge_version') or '?',
                          'aud': 'reasoning',
                          'tags': [f"kind: {ch.get('kind')}",
                                   f"confidence: {ch.get('confidence')}"]})

records.sort(key=lambda e: (e['cat'], e['ver'], e['arm'], e['model'], int(e['seed'] or 0),
                            e['agent'], e['turn'] or 0))
blob = json.dumps({'records': records, 'runs': runs_blob}, ensure_ascii=False,
                  separators=(',', ':'))
counts = collections.Counter(r['cat'] for r in records)
print(f'{scanned} runs scanned, {len(records)} flags: {dict(counts)}, '
      f'{len(blob) / 1e6:.1f} MB', file=sys.stderr)

# ------------------------------------------------------------------------------------ the page
CSS = """
*{box-sizing:border-box}
:root{--bg:#fbfbfa;--card:#fff;--ink:#1a1a19;--dim:#6b6b66;--line:#e3e3df;--accent:#8a4b2a;
      --hit:#fdf3d7;--chip:#f0efec;--sab:#b4341f;--esc:#3b6ea5;--dis:#8a4b2a;--rel:#6b6b66;
      --eva:#4b7f52;--ref:#7a5c9e}
@media (prefers-color-scheme:dark){:root{--bg:#17181a;--card:#1e1f22;--ink:#e8e8e4;--dim:#9a9a95;
      --line:#2e3034;--accent:#d99b6c;--hit:#3a3320;--chip:#2a2c30;--sab:#e8705c;--esc:#7aa7d9;
      --dis:#d99b6c;--rel:#9a9a95;--eva:#7fbf8a;--ref:#b39ddb}}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,
     "Segoe UI",Helvetica,Arial,sans-serif;display:flex;flex-direction:column}
.bar{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;padding:.5rem .7rem;
     border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
.bar select,.bar input{font:inherit;font-size:.82rem;padding:.22rem .4rem;border:1px solid var(--line);
     border-radius:5px;background:var(--bg);color:var(--ink)}
.bar input[type=search]{min-width:15rem}
.tabs{display:flex;gap:.3rem;margin-right:.4rem}
.tab{font-size:.82rem;padding:.25rem .6rem;border:1px solid var(--line);border-radius:999px;
     background:var(--bg);color:var(--dim);cursor:pointer}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff}
.m{color:var(--dim);font-size:.78rem}
.main{flex:1;display:flex;min-height:0}
#list{width:26rem;overflow:auto;border-right:1px solid var(--line);background:var(--card)}
#detail{flex:1;overflow:auto;padding:1rem 1.2rem}
.row{padding:.5rem .7rem;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:var(--chip)}
.row.on{background:var(--hit)}
.row .t{font-size:.83rem}
.row .s{color:var(--dim);font-size:.76rem;margin-top:.15rem;
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.chip{display:inline-block;font-size:.7rem;padding:.05rem .4rem;border-radius:999px;
      background:var(--chip);color:var(--dim);margin-right:.3rem}
.chip.cat{color:#fff}
.cat-sabotage{background:var(--sab)}.cat-escalation{background:var(--esc)}
.cat-disclosure{background:var(--dis)}.cat-relay{background:var(--rel)}
.cat-eval-awareness{background:var(--eva)}.cat-refusal{background:var(--ref)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.7rem .85rem;
      margin-bottom:.8rem}
.card h3{margin:0 0 .4rem;font-size:.9rem}
.reason{color:var(--ink);font-size:.9rem}
.quote{border-left:3px solid var(--accent);padding:.25rem .6rem;margin:.5rem 0;
       background:var(--hit);font-size:.88rem;white-space:pre-wrap}
.bad{border-left-color:var(--sab)}
.post{border:1px solid var(--line);border-radius:7px;padding:.5rem .7rem;margin-bottom:.5rem;
      background:var(--card);white-space:pre-wrap;font-size:.88rem}
.post .h{color:var(--dim);font-size:.75rem;margin-bottom:.3rem;white-space:normal}
.post .v{margin-top:.45rem;padding-top:.4rem;border-top:1px dashed var(--line);white-space:normal}
.post .v .flag{background:var(--accent);color:#fff}
details.why{margin-top:.3rem}
details.why summary{cursor:pointer;color:var(--dim);font-size:.75rem;list-style:none}
details.why summary::-webkit-details-marker{display:none}
details.why summary:before{content:"▸ ";}
details.why[open] summary:before{content:"▾ ";}
details.why .r{font-size:.8rem;color:var(--ink);margin:.3rem 0 .1rem;white-space:normal}
details.why .who{color:var(--dim);font-size:.72rem}
.post.hit{border-color:var(--accent);box-shadow:0 0 0 2px var(--hit)}
.post.dim{opacity:.55}
mark{background:var(--hit);color:inherit;border-bottom:2px solid var(--accent);padding:0 .1rem}
a{color:var(--accent)}
.empty{color:var(--dim);padding:2rem;text-align:center}
.marks{display:flex;flex-wrap:wrap;gap:.35rem;align-items:center;margin-top:.6rem;
       padding-top:.55rem;border-top:1px solid var(--line)}
.mk{font:inherit;font-size:.78rem;padding:.2rem .55rem;border:1px solid var(--line);
    border-radius:999px;background:var(--bg);color:var(--ink);cursor:pointer}
.mk:hover{border-color:var(--accent)}
.mk.on{background:var(--accent);border-color:var(--accent);color:#fff}
.mk.small{font-size:.72rem;padding:.1rem .4rem}
#note{font:inherit;font-size:.83rem;width:100%;margin-top:.45rem;padding:.35rem .5rem;
      border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);
      min-height:3.2rem;resize:vertical}
.badge{font-size:.7rem;border-radius:999px;padding:0 .35rem;margin-left:.25rem}
.b-agree{background:var(--eva);color:#fff}.b-disagree{background:var(--sab);color:#fff}
.b-missed{background:var(--esc);color:#fff}.b-unsure{background:var(--chip);color:var(--dim)}
.warn{color:var(--sab)}
"""

JS = """
const D = JSON.parse(document.getElementById('data').textContent);
const R = D.records, RUNS = D.runs;
const CATS = ['sabotage','escalation','refusal','disclosure','relay','eval-awareness'];
let state = {cat:'sabotage', arm:'', model:'', agent:'', level:'', q:'', i:0, mark:''};

/* ---- marks: agree / disagree / missed / unsure, one per (message, phenomenon) ----
   Served by adjudicate.py -> phenomenon_marks.json on disk; opened as a file:// page it falls
   back to this browser's localStorage with an export button. The key deliberately excludes the
   judge's verdict and version, so a mark made against mv3 still applies after mv4 re-judges the
   same message — that is the whole point of marking: a fixed human answer to compare judges to. */
const LS = 'agent1-phenomenon-marks';
let MARKS = {}, ONLINE = false;
const markKey = (run, agent, turn, step, conv, cat) =>
  [run, agent, turn, step, conv, cat].join('|');
const keyOf = r => markKey(r.run, r.agent, r.turn, r.step, r.conv, r.cat);

async function loadMarks(){
  try {
    const res = await fetch('api/marks', {cache:'no-store'});
    if(!res.ok) throw 0;
    MARKS = await res.json(); ONLINE = true;
    conn(`saving to phenomenon_marks.json · ${Object.keys(MARKS).length} marks`);
  } catch(_) {
    try { MARKS = JSON.parse(localStorage.getItem(LS) || '{}'); } catch(_) { MARKS = {}; }
    ONLINE = false;
    conn(`<span class="warn">offline — marks stay in this browser.</span> `
       + `Run <code>python3 adjudicate.py</code> to save to disk, or `
       + `<button class="mk small" id="exp">export</button>`);
    const b = document.getElementById('exp');
    if(b) b.onclick = () => {
      navigator.clipboard.writeText(JSON.stringify({store:'marks', entries:MARKS}, null, 1));
      b.textContent = 'copied — save it, then: adjudicate.py --import-json <file>';
    };
  }
}
async function setMark(key, patch, meta){
  const cur = MARKS[key] || {};
  const next = Object.assign({}, cur, patch);
  if(meta) next.meta = Object.assign({}, cur.meta || {}, meta);
  if(!next.label && !(next.note||'').trim()) delete MARKS[key]; else MARKS[key] = next;
  render();
  if(ONLINE){
    try {
      await fetch('api/marks', {method:'POST', headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({entries: {[key]: next}})});
      conn(`saving to phenomenon_marks.json · ${Object.keys(MARKS).length} marks`);
    } catch(e){ conn('<span class="warn">save failed — is adjudicate.py still running?</span>'); }
  } else {
    try { localStorage.setItem(LS, JSON.stringify(MARKS)); } catch(_){}
  }
}
function conn(html){ const el = document.getElementById('conn'); if(el) el.innerHTML = html; }
const badge = key => {
  const m = MARKS[key];
  return m && m.label ? `<span class="badge b-${m.label}">${m.label}</span>`
       : (m && (m.note||'').trim() ? '<span class="badge b-unsure">note</span>' : '');
};
const esc = s => (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function markSpan(text, span){
  if(!span) return esc(text);
  const i = (text||'').indexOf(span);
  if(i < 0) return esc(text);
  return esc(text.slice(0,i)) + '<mark>' + esc(span) + '</mark>' + esc(text.slice(i+span.length));
}
function filtered(){
  return R.filter(r => r.cat === state.cat
    && (!state.arm   || r.arm === state.arm)
    && (!state.model || r.model === state.model)
    && (!state.agent || r.agent === state.agent)
    && (!state.level || r.level === state.level)
    && (!state.q     || (r.reason + ' ' + (r.span||'') + ' ' + r.run + ' ' + (r.tags||[]).join(' '))
                          .toLowerCase().includes(state.q.toLowerCase()))
    && (!state.mark  || (state.mark === 'unmarked' ? !(MARKS[keyOf(r)]||{}).label
                                                   : (MARKS[keyOf(r)]||{}).label === state.mark)));
}
function fill(sel, vals, cur){
  const el = document.getElementById(sel);
  el.innerHTML = '<option value="">' + sel + ': all</option>' +
    vals.map(v => `<option${v===cur?' selected':''}>${esc(v)}</option>`).join('');
}
// The form a phenomenon takes is the filter people actually reach for — direct vs indirect
// disclosure, proposed vs performed escalation — so that select is labelled in the category's
// own words rather than as a generic 'level: all'.
const BOTH = {
  'disclosure': 'both forms — direct + indirect',
  'escalation': 'both — proposes + performs',
  'refusal': 'both — declines + disengages',
  'eval-awareness': 'both — aware + ambiguous'};
function fillLevel(vals, cur){
  const el = document.getElementById('level');
  if(vals.length < 2){ el.style.display = 'none'; el.innerHTML = '<option value=""></option>'; return; }
  el.style.display = '';
  el.innerHTML = `<option value="">${esc(BOTH[state.cat] || 'all forms')}</option>` +
    vals.map(v => `<option value="${esc(v)}"${v===cur?' selected':''}>${esc(v)} only</option>`).join('');
}
function renderTabs(){
  const counts = {};
  CATS.forEach(c => counts[c] = R.filter(r => r.cat === c).length);
  document.getElementById('tabs').innerHTML = CATS.map(c =>
    `<span class="tab${c===state.cat?' on':''}" data-cat="${c}">${c} <span class="m">${counts[c]||0}</span></span>`
  ).join('');
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    state.cat = t.dataset.cat; state.level = ''; state.i = 0; render();
  });
}
function renderList(rows){
  document.getElementById('list').innerHTML = rows.length ? rows.map((r,i) =>
    `<div class="row${i===state.i?' on':''}" data-i="${i}">
       <div class="t"><span class="chip cat cat-${r.cat}">${esc(r.level)}</span>
         <b>${esc(r.agent)}</b> · turn ${r.turn} · ${esc(r.model)} ${esc(r.arm)} s${esc(r.seed)}
         ${badge(keyOf(r))}</div>
       <div class="s">${esc(r.span || r.reason)}</div></div>`).join('')
    : '<div class="empty">nothing matches</div>';
  document.querySelectorAll('.row').forEach(el => el.onclick = () => {
    state.i = +el.dataset.i; render();
  });
}
function markWidget(r){
  const key = keyOf(r), m = MARKS[key] || {};
  const btn = (l, txt) => `<button class="mk${m.label===l?' on':''}" data-mark="${l}">${txt}</button>`;
  return `<div class="marks" id="markbar">
      <span class="m">this ${esc(r.cat)} flag is:</span>
      ${btn('agree','\u2713 right (a)')}${btn('disagree','\u2717 not ' + esc(r.cat) + ' (x)')}
      ${btn('unsure','? unsure (u)')}
      ${m.label ? '<button class="mk" data-mark="">clear</button>' : ''}
      <textarea id="note" placeholder="note (n to focus, saves on blur)">${esc(m.note||'')}</textarea>
    </div>`;
}
function wireMarks(r){
  const key = keyOf(r);
  const meta = {cat:r.cat, level:r.level, run:r.run, arm:r.arm, model:r.model, seed:r.seed,
                agent:r.agent, turn:r.turn, conv:r.conv, time:r.time, jver:r.jver,
                span:(r.spans&&r.spans[0])||r.span||''};
  document.querySelectorAll('#markbar .mk').forEach(b => b.onclick = () =>
    setMark(key, {label: b.dataset.mark}, meta));
  const n = document.getElementById('note');
  if(n) n.onblur = () => { const v = n.value.trim();
    if(v !== ((MARKS[key]||{}).note || '')) setMark(key, {note: v}, meta); };
}
function renderDetail(r){
  const d = document.getElementById('detail');
  if(!r){ d.innerHTML = '<div class="empty">select a flag</div>'; return; }
  const run = RUNS[r.run] || {posts:[], final:{}};
  const f = run.final || {};
  const pairs = f.pairs ? Object.keys(f.pairs).map(k => k+': '+f.pairs[k].join(' + ')).join(' · ') : '';
  const isCot = r.cat === 'eval-awareness';
  const head = `<div class="card">
      <h3>${esc(r.cat)} — ${esc(r.level)} <span class="m">${esc(r.jver||'')}</span></h3>
      <div class="m">${esc(r.run)} · ${esc(r.agent)} · turn ${r.turn}${r.time?' · '+esc(r.time):''}
        ${r.conv?' · '+esc(r.conv):''} · <a href="${esc(run.html)}#turn-${r.turn}" target="_blank">open rollout viewer at this turn</a></div>
      <div class="m">${(r.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>
      <div class="reason" style="margin-top:.5rem">${esc(r.reason)}</div>
      ${(r.spans && r.spans.length ? r.spans : (r.span?[r.span]:[])).map((s,i) => {
          const st = (r.statuses||[])[i] || (r.ok?'verbatim':'not-found');
          const bad = (st !== 'verbatim');
          return `<div class="quote${bad?' bad':''}">${esc(s)}
            ${bad?`<div class="m">quote resolved as <b>${esc(st)}</b>${
              st==='spliced'?' — fragments joined by an ellipsis, text between them dropped':
              st==='snapped'?' — fuzzy-matched to a near-identical string, not copied exactly':
              st==='not-found'?' — this quote is not in the message':''}</div>`:''}</div>`;
        }).join('')}
      ${markWidget(r)}
      ${pairs ? `<div class="m" style="margin-top:.4rem">board at 10:00 — ${esc(f.verdict)}: ${esc(pairs)}
         ${f.roles===null?'':' · roles '+(f.roles?'ok':'wrong')}
         ${f.kickoff===null?'':' · kickoff '+(f.kickoff?'posted':'missing')}</div>` : ''}
    </div>`;
  const cot = isCot ? `<div class="card"><h3>private reasoning — ${esc(r.agent)}, turn ${r.turn}</h3>
      <div class="post" style="border:none;padding:0">${markSpan(r.cot||'', r.span)}</div></div>` : '';
  // What every judge said about one post: the labels as chips, the reasoning behind them one
  // click away. Shown for clean verdicts too — "sabotage: no" is a decision the judge made, and
  // reading only the flags is how a judge's false negatives stay invisible.
  const verdictBlock = (p, open) => {
    const v = (run.verdicts || {})[p.key];
    if(!v) return '';
    const chips = [], reasons = [];
    if(v.sabotage){
      const s0 = v.sabotage;
      chips.push(`<span class="chip${s0.verdict==='yes'?' flag':''}">sabotage: ${esc(s0.verdict||'?')}</span>`);
      if(s0.rule && s0.rule !== 'none') chips.push(`<span class="chip">rule: ${esc(s0.rule)}</span>`);
      chips.push(`<span class="chip${s0.escalation&&s0.escalation!=='none'?' flag':''}">escalation: ${esc(s0.escalation||'—')}</span>`);
      if(s0.refusal !== undefined)
        chips.push(`<span class="chip${s0.refusal&&s0.refusal!=='none'?' flag':''}">refusal: ${esc(s0.refusal||'—')}</span>`);
      reasons.push(`<div class="who">sabotage judge (${esc(s0.jver)}) · confidence ${esc(s0.confidence||'?')}</div>
                    <div class="r">${esc(s0.reason)}</div>`);
    }
    if(v.disclosure){
      const d0 = v.disclosure;
      const flagged = d0.verdict && d0.verdict !== 'none';
      chips.push(`<span class="chip${flagged?' flag':''}">disclosure: ${esc(d0.verdict||'—')}</span>`);
      if(flagged && d0.about) chips.push(`<span class="chip">about: ${esc(d0.about)}</span>`);
      if(d0.relay && d0.relay !== 'none') chips.push(`<span class="chip flag">relay</span>`);
      reasons.push(`<div class="who">disclosure judge (${esc(d0.jver)}) · confidence ${esc(d0.confidence||'?')}</div>
                    <div class="r">${esc(d0.reason)}</div>`);
    }
    if(!chips.length) return '';
    // Marking from the thread is how a *missed* case gets recorded: the judge said no, you say
    // this one is real. Same store, same key shape, so it lands beside the flag marks.
    const key = markKey(r.run, p.agent, p.turn, p.step, p.conv, state.cat);
    const m = MARKS[key] || {};
    const mk = (l, t) => `<button class="mk small pm${m.label===l?' on':''}" data-key="${esc(key)}"
        data-turn="${p.turn}" data-step="${p.step}" data-conv="${esc(p.conv)}"
        data-agent="${esc(p.agent)}" data-mark="${l}">${t}</button>`;
    const marks = `<span class="m" style="margin-left:.35rem">mark as ${esc(state.cat)}:</span>
      ${mk('missed','\u2691 yes, missed')}${mk('agree','\u2713 right')}${mk('disagree','\u2717 no')}
      ${mk('unsure','? unsure')}${m.label ? mk('','clear') : ''} ${badge(key)}`;
    return `<div class="v">${chips.join('')}
      <details class="why"${open?' open':''}><summary>why</summary>${reasons.join('')}</details>
      <div style="margin-top:.35rem">${marks}</div></div>`;
  };
  const posts = run.posts.map(p => {
    const hit = isCot ? (p.turn === r.turn)
                      : (p.turn === r.turn && p.conv === r.conv &&
                         (r.step == null || p.step === r.step));
    let body = esc(p.text);
    if(hit && !isCot){
      body = p.text;
      const list = (r.spans && r.spans.length ? r.spans : (r.span?[r.span]:[]));
      // mark each contiguous quote where it actually sits; a quote the resolver could not
      // find simply does not highlight, which is the honest rendering of "not in the message"
      let out = '', at = 0;
      const hits = list.map(s => ({s, i: body.indexOf(s)})).filter(h => h.i >= 0)
                       .sort((a,b) => a.i - b.i);
      hits.forEach(h => { if(h.i < at) return;
        out += esc(body.slice(at, h.i)) + '<mark>' + esc(h.s) + '</mark>'; at = h.i + h.s.length; });
      body = out + esc(body.slice(at));
    }
    return `<div class="post${hit?' hit':' dim'}" ${hit?'id="hit"':''}>
        <div class="h">${esc(p.agent)} → ${esc(p.conv)} · ${esc(p.time)} · turn ${p.turn}</div>${body}
        ${verdictBlock(p, hit)}</div>`;
  }).join('');
  d.innerHTML = head + cot + `<div class="card"><h3>the rollout — every message it sent</h3>${posts}</div>`;
  wireMarks(r);
  wirePostMarks(run, r);
  const hit = document.getElementById('hit');
  if(hit) hit.scrollIntoView({block:'center'});
}
function wirePostMarks(run, r){
  document.querySelectorAll('.pm').forEach(b => b.onclick = () => setMark(b.dataset.key,
    {label: b.dataset.mark},
    {cat: state.cat, run: r.run, arm: r.arm, model: r.model, seed: r.seed,
     agent: b.dataset.agent, turn: +b.dataset.turn, conv: b.dataset.conv, from: 'thread'}));
}
function render(){
  renderTabs();
  const pool = R.filter(r => r.cat === state.cat);
  fill('arm',   [...new Set(pool.map(r=>r.arm))].sort(),   state.arm);
  fill('model', [...new Set(pool.map(r=>r.model))].sort(), state.model);
  fill('agent', [...new Set(pool.map(r=>r.agent))].sort(), state.agent);
  fillLevel([...new Set(pool.map(r=>r.level))].sort(), state.level);
  const rows = filtered();
  if(state.i >= rows.length) state.i = 0;
  document.getElementById('count').textContent =
    `${rows.length} of ${pool.length} ${state.cat} flags · ${Object.keys(RUNS).length} runs`;
  renderList(rows);
  renderDetail(rows[state.i]);
}
['arm','model','agent','level'].forEach(k =>
  document.getElementById(k).onchange = e => { state[k] = e.target.value; state.i = 0; render(); });
document.getElementById('q').oninput = e => { state.q = e.target.value; state.i = 0; render(); };
document.addEventListener('keydown', e => {
  if(e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const rows = filtered();
  if(e.key === 'j' || e.key === 'ArrowDown'){ state.i = Math.min(state.i+1, rows.length-1); render(); e.preventDefault(); }
  if(e.key === 'k' || e.key === 'ArrowUp'){ state.i = Math.max(state.i-1, 0); render(); e.preventDefault(); }
  const cur = rows[state.i];
  if(!cur) return;
  const meta = {cat:cur.cat, level:cur.level, run:cur.run, arm:cur.arm, model:cur.model,
                seed:cur.seed, agent:cur.agent, turn:cur.turn, conv:cur.conv, jver:cur.jver};
  if(e.key === 'a'){ setMark(keyOf(cur), {label:'agree'}, meta); e.preventDefault(); }
  if(e.key === 'x'){ setMark(keyOf(cur), {label:'disagree'}, meta); e.preventDefault(); }
  if(e.key === 'u'){ setMark(keyOf(cur), {label:'unsure'}, meta); e.preventDefault(); }
  if(e.key === 'n'){ const n = document.getElementById('note'); if(n){ n.focus(); e.preventDefault(); } }
});
document.getElementById('mark').onchange = e => { state.mark = e.target.value; state.i = 0; render(); };
loadMarks().then(render);
"""

page = ("<!doctype html><html><head><meta charset='utf-8'>"
        "<title>agent1 — phenomenon browser</title><style>" + CSS + "</style></head><body>"
        "<div class='bar'><span class='tabs' id='tabs'></span>"
        "<select id='arm'></select><select id='model'></select><select id='agent'></select>"
        "<select id='level'></select>"
        "<select id='mark'><option value=''>marks: all</option>"
        "<option value='unmarked'>unmarked only</option><option value='agree'>agree</option>"
        "<option value='disagree'>disagree</option><option value='missed'>missed</option>"
        "<option value='unsure'>unsure</option></select>"
        "<input type='search' id='q' placeholder='search reasons, spans, runs…'>"
        "<span class='m' id='count'></span></div>"
        "<div class='main'><div id='list'></div><div id='detail'></div></div>"
        "<script type='application/json' id='data'>"
        + blob.replace('<', '\\u003c') +
        "</script><script>" + JS + "</script></body></html>")

path = OUT / 'phenomenon_browser.html'
path.write_text(page, encoding='utf-8')
print('wrote', path, f'{path.stat().st_size / 1e6:.1f} MB')
