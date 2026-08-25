"""Serve `outputs/` and persist claim-level adjudications from the lie browser.

    python3 adjudicate.py                # serve http://localhost:8765/lie_browser.html
    python3 adjudicate.py --report       # markdown summary of what has been adjudicated
    python3 adjudicate.py --marks-report # the same for phenomenon_browser.html marks

Two stores, same machinery: `api/adjudications` for the lie browser's claim judgements and
`api/marks` for the phenomenon browser's per-message marks (agree / disagree / missed / unsure
against the sabotage, escalation, refusal, disclosure and eval-awareness judges).

The browser page saves each judgement to `outputs/adjudications.json` (one entry per claim,
keyed by run|agent|turn|category|subject|object, which is stable across page regeneration) and
appends every write to `outputs/adjudications.log.jsonl`, so a bad overwrite is always recoverable.
Opened as a plain file:// page instead, the browser falls back to localStorage — the same data,
exportable as JSON and importable here.
"""
from __future__ import annotations

import argparse, json, os, threading, time, collections
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(__file__).resolve().parent/'outputs'
STORE = OUT/'adjudications.json'
LOG = OUT/'adjudications.log.jsonl'
#: name -> (store file, append-only log). Both endpoints share the merge/write machinery; only
#: the files differ, so a phenomenon mark can never overwrite a lie adjudication.
STORES = {
    'adjudications': (STORE, LOG),
    'marks': (OUT/'phenomenon_marks.json', OUT/'phenomenon_marks.log.jsonl'),
}
LOCK = threading.Lock()
LABELS = ('real', 'not-real', 'wrong-category', 'unsure')
#: phenomenon_browser: agree = the judge was right, disagree = it was not this phenomenon,
#: missed = it IS this phenomenon and the judge did not flag it (marked from an unflagged post).
MARK_LABELS = ('agree', 'disagree', 'missed', 'unsure')


def read_store(name: str = 'adjudications') -> dict:
    store, log = STORES[name]
    try:
        return json.loads(store.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:            # never lose work to a half-written file
        return json.loads(log.read_text(encoding='utf-8').splitlines()[-1]) if log.exists() else {}


def write_store(store: dict, name: str = 'adjudications') -> None:
    path, _ = STORES[name]
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    os.replace(tmp, path)                   # atomic: a crash mid-write cannot truncate the store


def merge(entries: dict, name: str = 'adjudications') -> dict:
    with LOCK:
        store = read_store(name)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S')
        for k, v in entries.items():
            if not isinstance(v, dict): continue
            cur = store.get(k, {})
            cur.update({kk: vv for kk, vv in v.items() if kk in ('label', 'intent', 'note', 'meta')})
            cur['ts'] = stamp
            if not (cur.get('label') or cur.get('intent') or (cur.get('note') or '').strip()):
                store.pop(k, None)          # cleared judgement, cleared note: drop the entry
            else:
                store[k] = cur
        write_store(store, name)
        with STORES[name][1].open('a', encoding='utf-8') as fh:
            fh.write(json.dumps({'ts': stamp, 'entries': entries}, ensure_ascii=False) + '\n')
        return store


class Handler(SimpleHTTPRequestHandler):
    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _store_name(self):
        head = self.path.split('?')[0].strip('/')
        name = head.split('/')[-1] if head.startswith('api/') else None
        return name if name in STORES else None

    def do_GET(self):
        name = self._store_name()
        if name:
            return self._json(read_store(name))
        return super().do_GET()

    def do_POST(self):
        name = self._store_name()
        if not name:
            return self._json({'error': 'no such endpoint'}, 404)
        try:
            raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            payload = json.loads(raw or b'{}')
            entries = payload.get('entries') if isinstance(payload, dict) else None
            if entries is None:
                return self._json({'error': 'expected {"entries": {...}}'}, 400)
            store = merge(entries, name)
            return self._json({'ok': True, 'n': len(store)})
        except Exception as exc:                                    # noqa: BLE001
            return self._json({'error': f'{type(exc).__name__}: {exc}'}, 500)

    def log_message(self, fmt, *args):                              # one line per write, not per asset
        # log_error() passes an HTTPStatus as args[0], so coerce before matching — a bare
        # `in` on it raises, and the exception kills the handler thread mid-response.
        first = str(args[0]) if args else ''
        if 'api/' in first:
            print(f'  {time.strftime("%H:%M:%S")} {first}')


def report() -> str:
    store = read_store()
    if not store: return 'No adjudications yet.'
    rows = [dict(v.get('meta') or {}, label=v.get('label'), intent=v.get('intent'),
                 note=(v.get('note') or '').strip(), key=k) for k, v in store.items()]
    done = [r for r in rows if r['label']]
    o = [f'# Adjudications — {len(done)} claims judged ({len(rows)} entries in the store)\n']

    def tally(name, keyfn):
        per = collections.defaultdict(collections.Counter)
        for r in done: per[keyfn(r)][r['label']] += 1
        o.append(f'\n## By {name}\n')
        o.append('| ' + name + ' | ' + ' | '.join(LABELS) + ' | n | precision (real / judged) |')
        o.append('|---' * (len(LABELS) + 3) + '|')
        for k in sorted(per, key=str):
            c = per[k]; n = sum(c.values()); real = c['real']
            decided = n - c['unsure']
            prec = f'{100.0*real/decided:.0f}%' if decided else '—'
            o.append(f'| {k} | ' + ' | '.join(str(c[l]) for l in LABELS) + f' | {n} | {prec} |')

    tally('category', lambda r: r.get('cat') or '?')
    tally('agreement', lambda r: f"{r.get('votes','?')}/3 replicates at {r.get('lvl','?')}")
    tally('arm', lambda r: r.get('arm') or '?')
    tally('model', lambda r: r.get('model') or '?')
    flagged = [r for r in done if r['label'] != 'real']
    if flagged:
        o.append(f'\n## The {len(flagged)} claims not marked real\n')
        for r in sorted(flagged, key=lambda r: str(r.get('run'))):
            o.append(f"- **{r['label']}** — {r.get('arm')} {r.get('model')} {r.get('run')} "
                     f"{r.get('agent')} turn {r.get('turn')} ({r.get('cat')}, {r.get('lvl')})"
                     + (f" — {r['note']}" if r['note'] else ''))
    return '\n'.join(o)


def marks_report() -> str:
    store = read_store('marks')
    if not store:
        return 'No marks yet.'
    rows = [dict(v.get('meta') or {}, label=v.get('label'),
                 note=(v.get('note') or '').strip(), key=k) for k, v in store.items()]
    done = [r for r in rows if r['label']]
    o = [f'# Phenomenon marks — {len(done)} messages marked\n']
    per = collections.defaultdict(collections.Counter)
    for r in done:
        per[r.get('cat') or '?'][r['label']] += 1
    o.append('| phenomenon | ' + ' | '.join(MARK_LABELS) + ' | n | precision (agree / decided) |')
    o.append('|---' * (len(MARK_LABELS) + 3) + '|')
    for cat in sorted(per):
        c = per[cat]
        n = sum(c.values())
        decided = c['agree'] + c['disagree']
        prec = f'{100.0*c["agree"]/decided:.0f}%' if decided else '—'
        o.append(f'| {cat} | ' + ' | '.join(str(c[l]) for l in MARK_LABELS) + f' | {n} | {prec} |')
    bad = [r for r in done if r['label'] in ('disagree', 'missed')]
    if bad:
        o.append(f'\n## The {len(bad)} the judge got wrong\n')
        for r in sorted(bad, key=lambda r: (str(r.get('cat')), str(r.get('run')))):
            o.append(f"- **{r['label']}** — {r.get('cat')} · {r.get('run')} · {r.get('agent')} "
                     f"turn {r.get('turn')} ({r.get('model')} {r.get('arm')})"
                     + (f" — {r['note']}" if r['note'] else ''))
    return '\n'.join(o)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--marks-report', action='store_true',
                    help='markdown summary of the phenomenon browser marks')
    ap.add_argument('--report', action='store_true', help='print a summary and exit')
    ap.add_argument('--import-json', default=None,
                    help='merge an exported adjudications json (from the file:// fallback)')
    args = ap.parse_args()
    if args.import_json:
        # An exported file carries its own store name when it came from the phenomenon
        # browser; anything older is a lie-browser export and keeps the historical default.
        payload = json.loads(Path(args.import_json).read_text(encoding='utf-8'))
        name = 'adjudications'
        if isinstance(payload, dict) and payload.get('store') in STORES:
            name, payload = payload['store'], payload.get('entries') or {}
        n = len(merge(payload, name))
        print(f'merged into {name}; store now holds {n} entries'); return
    if args.report:
        print(report()); return
    if args.marks_report:
        print(marks_report()); return
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(OUT)))
    print(f'serving {OUT} → http://localhost:{args.port}/   (ctrl-c to stop)')
    print(f'  lie_browser.html         · store {STORE.name} ({len(read_store())} entries)')
    print(f'  phenomenon_browser.html  · store {STORES["marks"][0].name} '
          f'({len(read_store("marks"))} entries)')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')


if __name__ == '__main__':
    main()
