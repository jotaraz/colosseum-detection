from __future__ import annotations

"""One browsable file per judged item: the trace as the judges saw it, every verdict on it, meta.

``preference_judge.py`` writes its verdicts (``items.jsonl``) and its inputs (``traces.jsonl``)
separately, and a replicate lands in its own directory — so checking whether a verdict is fair to
the trace means joining three files by hand. This joins them: one card per (run, principal),
carrying the metadata, every judge×replicate verdict side by side, and the full prefix trace in a
collapsible block, as a single self-contained HTML file with no external assets.

Items the judge skipped (no chain of thought at the anchor turn) are kept as cards with an empty
verdict row, because "we could not look" is a finding about the run, not an absence to hide.

  python -m experiments.agent5.preference_bundle
  python -m experiments.agent5.preference_bundle --format jsonl --out /tmp/bundle.jsonl
  python -m experiments.agent5.preference_bundle --run r1=experiments/agent5/outputs/preference_v1
"""

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_RUNS = [f"r1={HERE / 'outputs' / 'preference_v1'}",
                f"r2={HERE / 'outputs' / 'preference_v1_rep2'}"]

#: Judge spec -> the short name used in the verdict columns.
SHORT = {"bifrost:azure/gpt-5.5": "gpt-5.5",
         "openrouter:deepseek/deepseek-v4-flash-0731": "ds-flash"}


def short(judge: str) -> str:
    return SHORT.get(judge, judge.split(":")[-1])


def collect(run_specs: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Join traces + every replicate's verdicts into one record per (run, principal)."""
    items: Dict[Tuple[str, str], Dict[str, Any]] = {}
    columns: List[str] = []
    for spec in run_specs:
        label, _, path = spec.partition("=")
        d = Path(path or label)
        if not path:
            label = d.name
        traces = d / "traces.jsonl"
        if traces.exists():
            for line in traces.open():
                rec = json.loads(line)
                key = (rec["run"], rec["agent"])
                if key not in items:
                    meta = {k: v for k, v in rec.items()}
                    meta["verdicts"] = {}
                    items[key] = meta
        for line in (d / "items.jsonl").open():
            row = json.loads(line)
            key = (row["run"], row["agent"])
            if key not in items:  # a verdict whose trace file is missing
                items[key] = {**{k: v for k, v in row.items() if k != "judge"},
                              "trace": "", "verdicts": {}}
            column = f"{short(row['judge'])} {label}"
            if column not in columns:
                columns.append(column)
            items[key]["verdicts"][column] = row
    return [items[k] for k in sorted(items)], columns


# ---- rendering ---------------------------------------------------------------------------------

_CSS = """
:root { --bg:#fbfbfa; --card:#fff; --line:#e4e2dd; --ink:#1f1f1e; --dim:#6b6a66;
        --clear:#0f7b52; --amb:#8a6d1f; --skip:#8a2f2f; --wav:#3b3f8f; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { padding:22px 26px 14px; border-bottom:1px solid var(--line); background:var(--card);
         position:sticky; top:0; z-index:5; }
h1 { margin:0 0 6px; font-size:19px; }
.sub { color:var(--dim); font-size:13px; }
.controls { margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
input[type=search] { padding:6px 10px; border:1px solid var(--line); border-radius:6px;
                     font-size:13px; min-width:260px; background:var(--bg); color:var(--ink); }
button { padding:6px 11px; border:1px solid var(--line); border-radius:6px; background:var(--bg);
         color:var(--ink); font-size:13px; cursor:pointer; }
button.on { background:var(--ink); color:var(--card); border-color:var(--ink); }
main { padding:18px 26px 60px; }
.overview { background:var(--card); border:1px solid var(--line); border-radius:9px;\n            padding:14px 16px 4px; margin:0 0 20px; }\n.overview h2 { margin:0 0 4px; font-size:14px; }\n.overview table { margin-top:10px; }\n.overview th { color:var(--dim); }\n.overview tbody th { color:var(--ink); font-size:13px; text-transform:none;\n                     letter-spacing:0; }\n.card { background:var(--card); border:1px solid var(--line); border-radius:9px;
        margin:0 0 16px; overflow:hidden; }
.card > h2 { margin:0; padding:12px 16px; font-size:14px; border-bottom:1px solid var(--line);
             display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.who { font-weight:700; }
.run { color:var(--dim); font-weight:400; font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size:12px; }
.meta { padding:10px 16px; color:var(--dim); font-size:12.5px; border-bottom:1px solid var(--line);
        display:flex; gap:16px; flex-wrap:wrap; }
.meta b { color:var(--ink); font-weight:600; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; vertical-align:top; padding:9px 12px; border-bottom:1px solid var(--line); }
th { font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim);
     font-weight:600; }
td.j { font-weight:600; white-space:nowrap; }
.badge { display:inline-block; padding:1px 7px; border-radius:20px; font-size:11.5px;
         font-weight:600; }
.b-clear { background:#e3f3ec; color:var(--clear); }
.b-amb { background:#f6efdc; color:var(--amb); }
.b-skip { background:#f7e6e6; color:var(--skip); }
.b-wav { background:#e8e9f4; color:var(--wav); }\n.pyr { overflow-x:auto; }\n.pyr svg { max-width:100%; height:auto; font-family:inherit; }\n.pt { font-size:13px; font-weight:600; fill:#222; }\n.pm { font-weight:400; fill:#777; }\n.pl { font-size:11px; fill:#666; paint-order:stroke; stroke:#fff; stroke-width:3px; }\n.pv { font-size:11px; fill:#333; }\n.pc { stroke:#c9ccd1; stroke-width:1; }\n.b-wld { background:#fbe7de; color:#9a4520; }\n.b-tkt { background:#f3ead9; color:#7a5a12; }\n.b-flr { background:#e7edf7; color:#33517d; }
.quote { color:var(--dim); font-style:italic; }
.note { color:var(--ink); }
details { border-top:1px solid var(--line); }
summary { padding:10px 16px; cursor:pointer; font-size:13px; color:var(--dim); }
pre { margin:0; padding:14px 18px 20px; background:#f6f5f2; white-space:pre-wrap;
      word-wrap:break-word; font:12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; }
.hidden { display:none; }
"""

_JS = """
const cards = [...document.querySelectorAll('.card')];
const q = document.getElementById('q');
let mode = 'all';
function apply() {
  const needle = q.value.toLowerCase();
  for (const c of cards) {
    const okMode = mode === 'all' || c.dataset.verdict === mode;
    const okText = !needle || c.textContent.toLowerCase().includes(needle);
    c.classList.toggle('hidden', !(okMode && okText));
  }
  document.getElementById('count').textContent =
    cards.filter(c => !c.classList.contains('hidden')).length + ' shown';
}
q.addEventListener('input', apply);
for (const b of document.querySelectorAll('button[data-mode]')) {
  b.addEventListener('click', () => {
    mode = b.dataset.mode;
    document.querySelectorAll('button[data-mode]').forEach(x => x.classList.toggle('on', x === b));
    apply();
  });
}
apply();
"""


#: Filter buckets, in the order the header buttons show them.
CLASSES = ("clear", "undecided", "split", "skipped")
#: Grounds by where the deciding fact lives (CRITIC_PREFERENCE_W1.md §3, W1_PLAN.md §7.4):
#: world content a scrub can remove, the ticket text only a rewrite can, and the floor that
#: survives any world. Kept in step with preference_judge.
WORLD_GROUNDS = ("task_fit", "personal", "colleague")
TICKET_GROUNDS = ("ticket_shape",)


def _verdict_class(item: Dict[str, Any]) -> str:
    """How the judges landed together: unanimous on what, or split. What the buttons key on.

    Unanimity is on the four-way label, and for a partner label on the *same* partner — two
    judges naming opposite partners is the sharpest disagreement there is, not a shared "clear".
    """
    if item.get("skipped"):
        return "skipped"
    calls = [_label(v) for v in item["verdicts"].values() if _label(v)]
    if not calls:
        return "skipped"
    if len(set(calls)) > 1:
        return "split"
    return "undecided" if calls[0] == "undecided" else "clear"


def _label(row: Dict[str, Any]) -> str:
    """The ``decision``, reconstructed for rows written under either previous schema."""
    if "error" in row or "parse_error" in row:
        return ""
    if row.get("decision"):
        return str(row["decision"])
    if row.get("preference"):  # the pre-`grounds` schema, where wavering was a label value
        pref = str(row["preference"])
        return "undecided" if pref in ("no_preference", "wavering") else pref
    if "has_clear_preference" not in row:
        return ""
    return str(row.get("preferred_partner") or row.get("preferred_ticket") or "clear") \
        if row.get("has_clear_preference") else "undecided"


#: The two partners' colours, and the neutral for "never landed". Red/blue pass the dataviz
#: validator's CVD and normal-vision checks against the light surface (ΔE 13.7 / 16.4); the
#: grey is a neutral by design, not a third series.
_T2_FILL, _T1_FILL, _UND_FILL = "#c0392b", "#2f6fb3", "#8c8f94"


def strength_pyramids(items: List[Dict[str, Any]]) -> str:
    """One diverging strength histogram per assistant model × principal.

    Rows are ``strength`` 3 (top) … 0 (bottom). Bars grow left for landings on the T2
    partner (Rafael, red) and right for landings on the T1 partner (Matthieu/Marcus,
    blue); a centred grey bar underneath counts the verdicts that never landed. Counts
    are judge×replicate verdicts pooled, so a 2-judge sweep counts each trace twice. One
    x-scale across all four panels, so bar lengths compare between panels.
    """
    e = html.escape
    rows = []
    for it in items:
        for v in it["verdicts"].values():
            lab = _label(v)
            if not lab:
                continue
            rows.append((it["model"], it["agent"], lab, int(v.get("strength") or 0)))
    if not rows:
        return ""
    labels = {r[2] for r in rows} - {"undecided"}
    t2 = "Rafael" if "Rafael" in labels else (sorted(labels)[-1] if labels else "T2")
    t1 = next((x for x in sorted(labels) if x != t2), "T1")
    panels = sorted({(r[0], r[1]) for r in rows})
    bins = {}
    for key in panels:
        b = {"t1": [0, 0, 0, 0], "t2": [0, 0, 0, 0], "und": 0}
        for m, a, lab, st in rows:
            if (m, a) != key:
                continue
            if lab == "undecided":
                b["und"] += 1
            elif lab == t2:
                b["t2"][min(max(st, 0), 3)] += 1
            else:
                b["t1"][min(max(st, 0), 3)] += 1
        bins[key] = b
    vmax = max([c for b in bins.values() for side in ("t1", "t2") for c in b[side]]
               + [b["und"] / 2 for b in bins.values()] + [1])

    # geometry: four panels in a 2×2 grid, shared centre line, one scale
    PW, PH, ROW, GAP = 360, 168, 26, 2
    MID, HALF = PW / 2, PW / 2 - 40
    px = HALF / vmax
    W, H = PW * 2 + 40, PH * 2 + 70
    svg = [f"<svg viewBox='0 0 {W} {H}' width='{W}' height='{H}' role='img' "
           f"aria-label='strength of each landing, per model and principal'>"]
    for i, key in enumerate(panels):
        model, agent = key
        b = bins[key]
        ox = 20 + (i % 2) * (PW + 20)
        oy = 40 + (i // 2) * (PH + 20)
        n = sum(b["t1"]) + sum(b["t2"]) + b["und"]
        svg.append(f"<text x='{ox + MID}' y='{oy - 8}' text-anchor='middle' class='pt'>"
                   f"{e(model)} · {e(agent)} <tspan class='pm'>({n} verdicts)</tspan></text>")
        for st in (3, 2, 1, 0):
            y = oy + (3 - st) * ROW
            svg.append(f"<text x='{ox + MID}' y='{y + ROW / 2 + 4}' text-anchor='middle' "
                       f"class='pl'>{st}</text>")
            for side, sign, fill, name in (("t2", -1, _T2_FILL, t2), ("t1", 1, _T1_FILL, t1)):
                c = b[side][st]
                if not c:
                    continue
                w = c * px
                x = ox + MID + (14 if sign > 0 else -14 - w)
                svg.append(f"<rect x='{x:.1f}' y='{y + GAP}' width='{w:.1f}' "
                           f"height='{ROW - 2 * GAP}' rx='3' fill='{fill}'>"
                           f"<title>{e(name)} · strength {st}: {c}</title></rect>")
                lx = x + w + 5 if sign > 0 else x - 5
                svg.append(f"<text x='{lx:.1f}' y='{y + ROW / 2 + 4}' class='pv' "
                           f"text-anchor='{'start' if sign > 0 else 'end'}'>{c}</text>")
        # undecided: one centred bar under the strength rows
        y = oy + 4 * ROW + 6
        u = b["und"]
        if u:
            w = u * px
            svg.append(f"<rect x='{ox + MID - w / 2:.1f}' y='{y + GAP}' width='{w:.1f}' "
                       f"height='{ROW - 2 * GAP}' rx='3' fill='{_UND_FILL}'>"
                       f"<title>undecided: {u}</title></rect>")
        svg.append(f"<text x='{ox + MID}' y='{y + ROW / 2 + 4}' text-anchor='middle' "
                   f"class='pv' style='fill:#fff'>{u or ''}</text>")
        svg.append(f"<text x='{ox + MID}' y='{y + ROW + 12}' text-anchor='middle' class='pl'>"
                   f"undecided</text>")
        # centre line
        svg.append(f"<line x1='{ox + MID}' y1='{oy}' x2='{ox + MID}' y2='{oy + 4 * ROW}' "
                   f"class='pc'/>")
    # legend, once
    ly = H - 14
    svg.append(f"<rect x='20' y='{ly - 10}' width='12' height='12' rx='2' fill='{_T2_FILL}'/>"
               f"<text x='36' y='{ly}' class='pl'>← {e(t2)} (T2), by strength</text>")
    svg.append(f"<rect x='{W / 2 - 60}' y='{ly - 10}' width='12' height='12' rx='2' "
               f"fill='{_UND_FILL}'/><text x='{W / 2 - 44}' y='{ly}' class='pl'>undecided</text>")
    svg.append(f"<rect x='{W - 230}' y='{ly - 10}' width='12' height='12' rx='2' "
               f"fill='{_T1_FILL}'/><text x='{W - 214}' y='{ly}' class='pl'>{e(t1)} (T1), "
               f"by strength →</text>")
    svg.append("</svg>")
    return ("<section class='overview'><h2>How hard each landing is held</h2>"
            "<div class='sub'>Per assistant model × principal: strength 3 (top) to 0 (bottom); "
            f"bars grow left for {e(t2)} and right for {e(t1)}; the grey bar is the verdicts "
            "that never landed. Judge×replicate verdicts pooled; one x-scale across panels.</div>"
            "<div class='pyr'>" + "".join(svg) + "</div></section>")


def render_html(items: List[Dict[str, Any]], columns: List[str], run_specs: List[str]) -> str:
    e = html.escape
    counts = {k: sum(1 for i in items if _verdict_class(i) == k) for k in CLASSES}
    out: List[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>PREFERENCE — traces and verdicts</title>",
        f"<style>{_CSS}</style></head><body>",
        "<header><h1>PREFERENCE — the trace each judge saw, and what it said</h1>",
        "<div class='sub'>Priya's and Nadia's assistants at the sprint hand-off. "
        f"{len(items)} items · {' · '.join(e(s) for s in run_specs)}</div>",
        "<div class='controls'>",
        "<input type='search' id='q' placeholder='filter — run, model, quote, trace text…'>",
        "<button data-mode='all' class='on'>all</button>",
        f"<button data-mode='clear'>unanimous partner ({counts['clear']})</button>",
        f"<button data-mode='undecided'>unanimous undecided ({counts['undecided']})</button>",
        f"<button data-mode='split'>split ({counts['split']})</button>",
        f"<button data-mode='skipped'>skipped ({counts['skipped']})</button>",
        "<span class='sub' id='count'></span>",
        "</div></header><main>",
    ]
    headers, prows, _ = pooled_rows(items, columns)
    out.append("<section class='overview'><h2>Per assistant model, all rollouts pooled</h2>"
               "<div class='sub'>Cell: partner named / judged (T1·T2), plus wN wavered. The "
               "pooled column counts every judge×replicate call, so its denominator is a "
               "multiple of items — a rate, not independent observations. `on world content` is "
               "the share of landings resting on task_fit/personal, the grounds an edit can "
               "remove. The last column is how much of the undecided bucket the judge rated "
               "confidence ≤ 1, i.e. could not tell."
               "</div><table><thead><tr>"
               + "".join(f"<th>{e(h)}</th>" for h in headers) + "</tr></thead><tbody>")
    for prow in prows:
        tag = "th" if prow[0] == "all" else "td"
        out.append("<tr>" + "".join(f"<{tag}>{e(c)}</{tag}>" for c in prow) + "</tr>")
    out.append("</tbody></table></section>")
    out.append(strength_pyramids(items))
    for item in items:
        klass = _verdict_class(item)
        out.append(f"<section class='card' data-verdict='{klass}'>")
        out.append(f"<h2><span class='who'>{e(item['agent'])}</span>"
                   f"<span class='run'>{e(item['run'])}</span></h2>")
        if item.get("skipped"):
            meta_bits = [f"<span class='badge b-skip'>skipped — {e(str(item['skipped']))}</span>"]
        else:
            meta_bits = []
        for label, key in (("model", "model"), ("seed", "seed"),
                           ("anchor turn", "anchor_turn"), ("kind", "anchor_kind"),
                           ("clock", "anchor_clock"), ("boundary", "boundary_source")):
            if item.get(key) is not None:
                meta_bits.append(f"{label} <b>{e(str(item[key]))}</b>")
        if item.get("steps_kept") is not None:
            meta_bits.append(f"steps <b>{item['steps_kept']}/{item.get('steps_in_window', item.get('steps_in_turn', '?'))}</b>")
        for label, key in (("chars", "trace_chars"), ("truncated steps", "truncated_steps"),
                           ("turns used", "turns_used"), ("steps dropped", "steps_dropped"),
                           ("window ended by", "cut_kind"), ("commit", "commit_act"),
                           ("signal", "signal_source")):
            if item.get(key) is not None:
                meta_bits.append(f"{label} <b>{e(str(item[key]))}</b>")
        out.append("<div class='meta'>" + "".join(f"<span>{b}</span>" for b in meta_bits) + "</div>")

        if item["verdicts"]:
            out.append("<table><thead><tr><th>judge</th><th>decision</th><th>ticket</th>"
                       "<th>grounds</th><th>str</th><th>conf</th><th>evidence quote</th>"
                       "<th>note</th></tr></thead><tbody>")
            for column in columns:
                row = item["verdicts"].get(column)
                if not row:
                    continue
                if "error" in row or "parse_error" in row:
                    body = e(str(row.get("error") or row.get("parse_error")))
                    out.append(f"<tr><td class='j'>{e(column)}</td>"
                               f"<td colspan='7'><span class='badge b-skip'>failed</span> "
                               f"{body}</td></tr>")
                    continue
                label = _label(row)
                badge = ("<span class='badge b-amb'>undecided</span>" if label == "undecided"
                         else f"<span class='badge b-clear'>{e(label or '—')}</span>")
                if row.get("wavered"):
                    badge += " <span class='badge b-wav'>wavered</span>"
                ticket = row.get("preferred_ticket") or "—"
                conf = row.get("confidence")
                # An undecided at confidence <= 1 is "could not tell", not "never landed".
                # It must not read the same as a confident one at a glance.
                if label == "undecided" and isinstance(conf, int) and conf <= 1:
                    badge += " <span class='badge b-skip'>could not tell</span>"
                feels = row.get("feelings") or []
                if feels:
                    badge += "".join(
                        f" <span class='badge {'b-skip' if f.startswith('dislikes') else 'b-wld'}'>"
                        f"{e(f)}</span>" for f in feels)
                ground = row.get("grounds")
                gcls = ("b-wld" if ground in WORLD_GROUNDS
                        else "b-tkt" if ground in TICKET_GROUNDS else "b-flr")
                gcell = (f"<span class='badge {gcls}'>{e(str(ground))}</span>" if ground
                         else "—")
                out.append(
                    f"<tr><td class='j'>{e(column)}</td><td>{badge}</td>"
                    f"<td>{e(str(ticket))}</td><td>{gcell}</td>"
                    f"<td>{e(str(row.get('strength')))}</td><td>{e(str(conf))}</td>"
                    f"<td class='quote'>{e(str(row.get('evidence_quote') or ''))}</td>"
                    f"<td class='note'>{e(str(row.get('note') or ''))}</td></tr>")
            out.append("</tbody></table>")

        trace = item.get("trace") or ""
        if trace.strip():
            out.append(f"<details><summary>trace as judged — {len(trace):,} characters</summary>"
                       f"<pre>{e(trace)}</pre></details>")
        else:
            out.append("<details><summary>no trace (no chain of thought at the anchor turn)"
                       "</summary><pre>—</pre></details>")
        out.append("</section>")
    out.append(f"</main><script>{_JS}</script></body></html>")
    return "\n".join(out)


def pooled_table(items: List[Dict[str, Any]], columns: List[str]) -> str:
    """One row per assistant model: every rollout of that model pooled.

    A cell is ``named/n (T1·T2) wW`` — how many of that model's items landed on a partner
    rather than ``undecided``, how those split between the tickets, and how many wavered on the
    way. ``on world content`` is the share of landings resting on ``task_fit`` / ``personal`` /
    ``colleague`` (``· ticket n`` counts the ``ticket_shape`` landings beside it),
    the grounds a workspace edit can remove; the rest is the structural floor. The last column
    is the share of ``undecided`` verdicts the judge itself rated at confidence ≤ 1: those are
    "I could not tell", not "never landed", and a high value means the rate is measuring
    legibility rather than the world.

    The pooled column counts all judge×replicate calls per item, so its denominator is
    ``4 × items``. It is a rate, not a count of independent observations — the four calls read
    the same trace.
    """
    headers, rows, skipped = pooled_rows(items, columns)
    judged = [i for i in items if not i.get("skipped")]
    out = ["# PREFERENCE — per assistant model, all rollouts pooled", "",
           f"{len(judged)} judged items over {len({i['run'] for i in judged})} runs "
           f"({len(skipped)} skipped for having no chain of thought at the anchor turn). "
           "Cell: named/n (T1·T2) wWavered.", "",
           "| " + " | ".join(headers) + " |",
           "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    if skipped:
        out += ["", "Skipped:", ""]
        out += [f"- `{i['run']}` / {i['agent']} — {i['skipped']}" for i in skipped]
    return "\n".join(out) + "\n"


def pooled_rows(items: List[Dict[str, Any]], columns: List[str]):
    """(headers, rows, skipped) for the pooled per-model table, shared by both renderers."""
    judged = [i for i in items if not i.get("skipped")]
    skipped = [i for i in items if i.get("skipped")]
    models = sorted({i.get("model", "") for i in judged})

    def cell(sel: List[Dict[str, Any]], column: str) -> str:
        rows = [i["verdicts"][column] for i in sel if column in i["verdicts"]]
        rows = [r for r in rows if "parse_error" not in r and "error" not in r]
        named = [r for r in rows if _label(r) not in ("", "undecided")]
        t1 = sum(1 for r in named if r.get("preferred_ticket") == "T1")
        t2 = sum(1 for r in named if r.get("preferred_ticket") == "T2")
        wav = sum(1 for r in rows if r.get("wavered"))
        return f"{len(named)}/{len(rows)} ({t1}·{t2})" + (f" w{wav}" if wav else "")

    def pooled(sel: List[Dict[str, Any]]) -> str:
        calls = [r for i in sel for r in i["verdicts"].values()
                 if "parse_error" not in r and "error" not in r]
        named = sum(1 for r in calls if _label(r) not in ("", "undecided"))
        pct = f" ({100 * named / len(calls):.0f}%)" if calls else ""
        return f"{named}/{len(calls)}{pct}"

    def world_share(sel: List[Dict[str, Any]]) -> str:
        """Of the landings on a partner, how many rest on a ground an edit can remove."""
        calls = [r for i in sel for r in i["verdicts"].values()
                 if "parse_error" not in r and "error" not in r]
        named = [r for r in calls if _label(r) not in ("", "undecided")]
        rated = [r for r in named if r.get("grounds")]
        if not rated:
            return f"n/a ({len(named)})" if named else "-"
        w = sum(1 for r in rated if r["grounds"] in WORLD_GROUNDS)
        t = sum(1 for r in rated if r["grounds"] in TICKET_GROUNDS)
        cell = f"{w}/{len(rated)} ({100 * w / len(rated):.0f}%)"
        return cell + (f" · ticket {t}" if t else "")

    def unsure(sel: List[Dict[str, Any]]) -> str:
        """How much of this model's no_preference is really "could not tell"."""
        calls = [r for i in sel for r in i["verdicts"].values()
                 if "parse_error" not in r and "error" not in r]
        npref = [r for r in calls if _label(r) == "undecided"]
        if not npref:
            return "-"
        rated = [r for r in npref if isinstance(r.get("confidence"), int)]
        if not rated:  # rows written before confidence existed — absent, not zero
            return f"n/a ({len(npref)})"
        low = sum(1 for r in rated if r["confidence"] <= 1)
        return f"{low}/{len(rated)}"

    headers = (["model", "runs", "items"] + list(columns)
               + ["pooled partner named", "on world content", "undecided at conf ≤ 1"])
    rows = []
    for model in models:
        sel = [i for i in judged if i.get("model") == model]
        rows.append([model, str(len({i["run"] for i in sel})), str(len(sel))]
                    + [cell(sel, c) for c in columns]
                    + [pooled(sel), world_share(sel), unsure(sel)])
    rows.append(["all", str(len({i["run"] for i in judged})), str(len(judged))]
                + [cell(judged, c) for c in columns]
                + [pooled(judged), world_share(judged), unsure(judged)])
    return headers, rows, skipped


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Join PREFERENCE traces + verdicts + metadata into one file")
    ap.add_argument("--run", action="append", default=None,
                    help="label=dir of a preference_judge output directory, repeatable")
    ap.add_argument("--format", choices=("html", "jsonl"), default="html")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    run_specs = args.run or list(DEFAULT_RUNS)
    items, columns = collect(run_specs)
    default_dir = Path((run_specs[0].partition("=")[2] or run_specs[0]))
    out = Path(args.out) if args.out else default_dir / f"bundle.{args.format}"
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "jsonl":
        with out.open("w") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    else:
        out.write_text(render_html(items, columns, run_specs))

    summary = out.parent / "summary.md"
    summary.write_text(pooled_table(items, columns))
    print(f"wrote {summary}")

    counts: Dict[str, int] = {}
    for item in items:
        counts[_verdict_class(item)] = counts.get(_verdict_class(item), 0) + 1
    print(f"wrote {out} — {len(items)} items, {len(columns)} verdict columns "
          f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
