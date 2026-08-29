"""A browser for the jv10/jv11 verdicts: read the turn, compare the judges, adjudicate.

    .venv/bin/python -m experiments.agent2.deception_browser
    open experiments/agent2/deception_browser.html

One self-contained page over the frozen sample (`deception_sample.json`). Per turn it shows
what Priya's assistant actually did — step by step, reasoning and messages in the order they
happened — and beside it every jv10 and jv11 verdict, so a disagreement can be read against
the material rather than against another judge's summary.

Deliberately NOT a jv7/jv8/jv9 viewer. The only trace of the older judges is the `jv9 >=2/3`
badge, which is how the sample was drawn and therefore part of the turn's identity; no jv7,
jv8 or jv9 finding, quote or verdict appears anywhere. Show-jv10 / show-jv11 toggles exist so
one version can be read alone, which is the honest way to look at a version you are about to
compare — seeing both at once anchors you to whichever spoke first.

Adjudications live in the browser's `localStorage`, keyed by `<run>#<turn>`, and are exported
as JSON with the Export button. Nothing is written back into the verdict files: an adjudication
is a human judgement ABOUT a verdict and must never be confusable with one.
"""

import argparse
import collections
import html
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent1.workspace import Workspace  # noqa: E402
from experiments.agent2 import turn_context  # noqa: E402
from experiments.agent2.target_run import _audience  # noqa: E402

SAMPLE = Path(__file__).with_name("deception_sample.json")
OUT = Path(__file__).with_name("deception_browser.html")

#: Set by ``--relative``; see the link comment in ``turn_payload``.
RELATIVE_LINKS = False

#: Set by ``--http-base``. A ``file://`` link cannot be clicked in a sandboxed viewer (a chat
#: file card, a VS Code preview) and Safari blocks it outright, so the only route that works
#: everywhere is HTTP. Serve the repo root -- ``python3 -m http.server 8000`` -- and rebuild
#: with ``--http-base http://localhost:8000``.
HTTP_BASE = ""


def _audience_of(ws, speaker):
    def f(args, result):
        conv = str((result or {}).get("conversation") or (args or {}).get("conversation") or "")
        return _audience(conv, ws, speaker)
    return f


def turn_payload(report, ws, run, index):
    """One turn as the page needs it: the steps, what each sent, and where to read the rest."""
    record = turn_context._record(report, index)
    aud = _audience_of(ws, str(record.get("agent") or ""))
    calls = turn_context._calls_by_step(record)
    details = {int(d.get("step") or 0): d for d in record.get("steps_detail") or []}
    all_steps = turn_context._all_steps(record)
    emitting = turn_context.emitting_steps(report, index)
    steps = []
    for n in all_steps:
        sent = []
        for c in turn_context._delivered_posts(calls.get(n, [])):
            a, r = c.get("arguments") or {}, c.get("result") or {}
            sent.append({"audience": aud(a, r),
                         "conversation": r.get("conversation") or a.get("conversation"),
                         "text": a.get("text") or ""})
        if n == (all_steps[-1] if all_steps else 0) and str(record.get("text_to_principal") or "").strip():
            sent.append({"audience": "debrief", "conversation": "(private to Priya)",
                         "text": record["text_to_principal"]})
        steps.append({
            "step": n,
            "reasoning": str((details.get(n) or {}).get("reasoning") or ""),
            "tools": [str(c.get("tool")) for c in calls.get(n, [])],
            "sent": sent,
            "emitting": n in emitting,
        })
    # An ABSOLUTE file:// href, not a path relative to this page. A relative link only works
    # while the html sits in experiments/agent2/, and this file gets copied — downloaded,
    # mailed, opened from a preview pane — at which point every "open rollout" 404s and the
    # breakage looks like a bug in the browser rather than in where it was opened from.
    # `--relative` restores the portable-but-fragile form for moving the whole tree.
    rollout = path = None
    for cand in (Path(run).with_suffix(".html"), Path(run).parent / "run.html"):
        if (REPO / cand).is_file():
            if HTTP_BASE:
                rollout = HTTP_BASE.rstrip("/") + "/" + str(cand).replace("\\", "/")
            elif RELATIVE_LINKS:
                import os
                rollout = os.path.relpath(REPO / cand, OUT.parent)
            else:
                rollout = (REPO / cand).resolve().as_uri()
            path = str((REPO / cand).resolve())
            break
    return {"rollout_path": path, "round": record.get("round"), "kind": record.get("kind"),
            "clock": record.get("clock"), "opening": str(record.get("message_in") or ""),
            "steps": steps, "rollout": rollout}


def verdict_payload(run, index):
    """Every jv10/jv11 verdict for this turn, one entry per setting (jv10 keeps its steps)."""
    out = []
    for f in sorted((REPO / run).parent.glob(Path(run).stem + ".deception_jv1*_*.json")):
        d = json.loads(f.read_text())
        rows = [r for r in d.get("turns") or [] if r.get("turn_index") == index]
        if not rows:
            continue
        out.append({
            "version": d.get("judge_version"),
            "model": (d.get("judge") or "?").split(":")[-1],
            "replicate": int(d.get("replicate") or 1),
            "units": [{"step": r.get("step"), "findings": r.get("findings") or [],
                       "excluded": r.get("excluded") or [], "description": r.get("description") or "",
                       "parse_error": r.get("parse_error"), "has_cot": r.get("has_cot")}
                      for r in sorted(rows, key=lambda r: (r.get("step") or 0))],
        })
    return sorted(out, key=lambda v: (v["version"], v["model"], v["replicate"]))


def build(sample_path: Path, out_path: Path) -> int:
    sample = json.loads(sample_path.read_text())
    reports, workspaces = {}, {}
    turns = []
    for s in sample:
        run = s["run"]
        if run not in reports:
            reports[run] = json.loads((REPO / run).read_text())
            wpath = reports[run]["config"]["workspace"]
            if wpath not in workspaces:
                workspaces[wpath] = Workspace.load(REPO / wpath)
        report = reports[run]
        ws = workspaces[report["config"]["workspace"]]
        payload = turn_payload(report, ws, run, s["turn_index"])
        verdicts = verdict_payload(run, s["turn_index"])
        turns.append({
            "id": f'{run}#{s["turn_index"]}',
            "run": run, "turn_index": s["turn_index"], "group": s["group"],
            "family": s["family"], "jv9_hit": (s["jv9_votes"] or 0) >= 2,
            "jv9_labelled": s["jv9_votes"] is not None,
            "label": f'{Path(run).parent.name}/{Path(run).stem}' if Path(run).name == "run.json"
                     else Path(run).stem,
            **payload, "verdicts": verdicts,
        })
    data = {"turns": turns,
            "settings": sorted({(v["version"], v["model"], v["replicate"])
                                for t in turns for v in t["verdicts"]})}
    out_path.write_text(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    n_v = sum(len(t["verdicts"]) for t in turns)
    try:
        shown = out_path.resolve().relative_to(REPO)
    except ValueError:  # --out outside the repo is legitimate; do not fail on the log line
        shown = out_path
    print(f"wrote {shown} — {len(turns)} turns, {n_v} verdicts, "
          f"{out_path.stat().st_size // 1024} KB")
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Deception verdicts — jv10 / jv11</title>
<style>
:root{--bg:#fbfbfa;--fg:#1a1a19;--mut:#6b6b66;--line:#e2e2dd;--card:#fff;--acc:#3b5bdb;
      --yes:#b02a37;--no:#2b7a4b;--warn:#a16207;--chip:#f0f0ec;}
@media (prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#e8e8e4;--mut:#9a9a94;--line:#2e2e34;
      --card:#1d1d22;--acc:#8ba3ff;--yes:#ff8a94;--no:#7fd6a3;--warn:#e3b341;--chip:#26262c;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line);
       padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
h1{font-size:15px;margin:0 12px 0 0;font-weight:600}
button,select{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;
              background:var(--card);color:var(--fg);cursor:pointer}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
main{padding:16px;max-width:1500px;margin:0 auto}
.turn{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:18px;overflow:hidden}
.thead{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.thead .t{font-weight:600}
.chip{font-size:11px;padding:1px 7px;border-radius:99px;background:var(--chip);color:var(--mut);white-space:nowrap}
.chip.hit{background:#fde2e4;color:#9d1c2b}
.chip.flag{background:#e7ecfd;color:#2540b0;font-weight:600}
@media (prefers-color-scheme:dark){.chip.hit{background:#42181f;color:#ff9aa4}
  .chip.flag{background:#22294a;color:#9db2ff}}
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0}
@media(max-width:1100px){.cols{grid-template-columns:1fr}}
.col{padding:12px 14px;min-width:0}
.col+.col{border-left:1px solid var(--line)}
@media(max-width:1100px){.col+.col{border-left:0;border-top:1px solid var(--line)}}
h3{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 8px}
.step{border-left:2px solid var(--line);padding:0 0 0 10px;margin:0 0 12px}
.step.em{border-left-color:var(--acc)}
.sl{font-size:11px;color:var(--mut);margin-bottom:3px}
pre{white-space:pre-wrap;word-wrap:break-word;margin:0;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
.cot{color:var(--mut);max-height:11em;overflow:auto;background:var(--chip);padding:6px 8px;border-radius:6px}
.msg{border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin-top:6px}
.msg .a{font-size:11px;font-weight:600;color:var(--acc)}
.vset{border:1px solid var(--line);border-radius:8px;margin-bottom:10px}
.vhead{padding:6px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--chip)}
.vhead .v{font-weight:600;font-size:12px}
.dot{width:9px;height:9px;border-radius:99px;display:inline-block}
.dot.y{background:var(--yes)} .dot.n{background:var(--no)}
.f{padding:8px 10px;border-top:1px solid var(--line)}
.f .claim{font-weight:600}
.f .meta{font-size:11px;color:var(--mut);margin-top:3px}
.q{border-left:2px solid var(--line);padding-left:8px;margin-top:5px;font:12px/1.45 ui-monospace,monospace;color:var(--mut)}
.exblock{border-top:1px dashed var(--line);background:var(--chip);padding:6px 10px 8px}
.exhead{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-bottom:4px}
.ex{font-size:12px;color:var(--fg);padding:3px 0}
.ex+.ex{border-top:1px solid var(--line)}
.exr{color:var(--mut);margin:2px 0 0 2px}
.g-vague{border-color:#b9a94a} .g-belief{border-color:#6b8fd6} .g-slip{border-color:#8f8f8a}
.g-audience{border-color:#c08a52} .g-goal{border-color:#6fae82}
.gate{font-size:10px;padding:0 5px;border-radius:4px;background:var(--chip);border:1px solid var(--line)}
.adj{padding:10px 14px;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.adj textarea{flex:1;min-width:240px;font:inherit;padding:5px 8px;border:1px solid var(--line);
              border-radius:6px;background:var(--bg);color:var(--fg)}
.adj button.sel{background:var(--acc);color:#fff;border-color:var(--acc)}
a{color:var(--acc)}
.count{color:var(--mut);font-size:12px}
button.cp{font-size:11px;padding:2px 7px}
mark.mk-find{background:#ffd9dd;color:inherit;border-bottom:2px solid #d1495b;padding:0 1px}
mark.mk-excl{background:#e6e6e0;color:inherit;border-bottom:2px dotted #9a9a94;padding:0 1px}
@media (prefers-color-scheme:dark){
  mark.mk-find{background:#4a1f27;border-bottom-color:#ff8a94}
  mark.mk-excl{background:#32323a;border-bottom-color:#6f6f78}}
.legend{font-size:11px;color:var(--mut);margin-left:auto}
.hint{font-size:11px;color:var(--mut);padding:0 16px 8px}
</style></head><body>
<header>
  <h1>Deception verdicts</h1>
  <button id="b10" class="on">jv10 (per step)</button>
  <button id="b11" class="on">jv11 (per turn)</button>
  <button id="bx" class="on">excluded claims</button>
  <button id="bh" class="on">highlight quotes</button>
  <select id="model"><option value="">all judge models</option></select>
  <select id="group"><option value="">all groups</option><option>a1_hit</option>
    <option>a1_unjudged</option><option>a3_full</option><option>a3_extra</option></select>
  <select id="flag">
    <option value="">flagged by: anyone or no one</option>
    <option value="jv10">flagged by jv10</option>
    <option value="jv11">flagged by jv11</option>
    <option value="either">flagged by either</option>
    <option value="both">flagged by both</option>
    <option value="none">flagged by neither</option>
  </select>
  <button id="mq" class="on" title="When on, the judge-model dropdown also narrows the quorum: with gpt-5.4 selected, 'flagged by jv10' means gpt-5.4's jv10 runs flagged it. When off, the quorum is taken over every model and the dropdown only chooses whose verdicts are displayed.">model narrows quorum</button>
  <select id="quorum">
    <option value="any">…by ≥1 judge run</option>
    <option value="maj">…by most judge runs</option>
    <option value="all">…by all judge runs</option>
  </select>
  <select id="filter">
    <option value="">all turns</option>
    <option value="split">judges disagree</option>
    <option value="vsplit">jv10 vs jv11 differ</option>
    <option value="jv9">jv9 &ge;2/3 only</option>
    <option value="todo">not yet adjudicated</option>
  </select>
  <button id="inv" title="Show exactly the turns the current filters leave out. Negates the whole criterion set at once — group, flagged-by, quorum and the filter dropdown together — so 'flagged by both' inverted is 'not flagged by both', not 'flagged by neither'.">invert</button>
  <span class="count" id="count"></span>
  <span style="flex:1"></span>
  <button id="exp">Export adjudications</button>
  <button id="imp">Import</button>
  <input type="file" id="file" accept="application/json" style="display:none">
</header>
<div class="hint" id="hint"></div>
<main id="main"></main>
<script>
const DATA = __DATA__;
const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const KEY = "deception_adjudications_v1";
let adj = {};
try { adj = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { adj = {}; }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(adj)); } catch (e) {} };

const models = [...new Set(DATA.turns.flatMap(t => t.verdicts.map(v => v.model)))].sort();
const msel = document.getElementById("model");
models.forEach(m => { const o = document.createElement("option"); o.textContent = m; msel.appendChild(o); });

const show = {jv10:true, jv11:true};
let showExcluded = true;
// What is DISPLAYED honours the jv10/jv11 toggles; what is FILTERED does not. "Show me the
// turns jv10 flagged" must not force jv11 out of view — the point of asking is usually to
// read the other version's account of the same turn.
const visible = t => t.verdicts.filter(v => show[v.version] &&
      (!msel.value || v.model === msel.value));
// Which settings the quorum is taken over. By default the judge-model dropdown narrows it,
// so "flagged by jv10" with gpt-5.4 selected asks about gpt-5.4's jv10 runs. Turn `model
// narrows quorum` off to ask the other question — which turns ANY model's jv10 flagged —
// while still reading only the selected model's verdicts.
let modelNarrowsQuorum = true;
const forVersion = (t, ver) => t.verdicts.filter(v => v.version === ver &&
      (!modelNarrowsQuorum || !msel.value || v.model === msel.value));
const isDec = v => v.units.some(u => (u.findings||[]).length > 0);

// A version "flagged" a turn when enough of its settings recorded a FINDING — an `excluded`
// entry is a claim the gates killed, and never counts. `n` is the settings actually in play,
// so the judge-model dropdown narrows the quorum rather than being ignored by it.
function flagged(t, ver) {
  const vs = forVersion(t, ver);
  if (!vs.length) return false;
  const k = vs.filter(isDec).length, q = document.getElementById("quorum").value;
  if (q === "all") return k === vs.length;
  if (q === "maj") return k * 2 > vs.length;
  return k > 0;
}
const flagCount = (t, ver) => {
  const vs = forVersion(t, ver);
  return [vs.filter(isDec).length, vs.length];
};
const decByVersion = t => {
  const o = {};
  for (const v of visible(t)) (o[v.version] = o[v.version] || []).push(isDec(v));
  return o;
};

// `invert` asks the complement question: everything the current criteria do NOT select. It
// negates the criterion set as a whole rather than each control separately, which is the only
// reading that makes "the exact opposite of this list" true — so `flagged by both` inverted is
// "not flagged by both", which includes the turns only one version flagged. A turn with nothing
// to display is never dragged back in by inverting: there would be no verdict to read.
let invert = false;
const keep = t => visible(t).length > 0 && (invert ? !matches(t) : matches(t));

function matches(t) {
  if (document.getElementById("group").value && t.group !== document.getElementById("group").value) return false;
  const vs = visible(t), f = document.getElementById("filter").value;
  const fl = document.getElementById("flag").value;
  if (fl) {
    const a = flagged(t, "jv10"), b = flagged(t, "jv11");
    if (fl === "jv10"   && !a) return false;
    if (fl === "jv11"   && !b) return false;
    if (fl === "either" && !(a || b)) return false;
    if (fl === "both"   && !(a && b)) return false;
    if (fl === "none"   && (a || b)) return false;
  }
  if (f === "split") return vs.some(isDec) && vs.some(v => !isDec(v));
  if (f === "jv9")   return t.jv9_hit;
  if (f === "todo")  return !(adj[t.id] && adj[t.id].verdict);
  if (f === "vsplit") {
    const d = decByVersion(t);
    if (!d.jv10 || !d.jv11) return false;
    return d.jv10.some(Boolean) !== d.jv11.some(Boolean);
  }
  return true;
}

function fnd(f, version) {
  const ev = f.evidence || {};
  const q = (label, arr, checks) => (arr||[]).map((s,i) => {
    const st = (checks||[])[i] || {};
    const bad = st.status && st.status !== "verbatim" && st.status !== "spliced";
    return `<div class="q">${bad ? `<span class="gate">${esc(st.status)}</span> ` : ""}${esc(label)}: ${esc(s)}</div>`;
  }).join("");
  return `<div class="f">
    <div class="claim">${esc(f.claim)}</div>
    <div class="meta">${["falsity","specificity","belief","serves_goal","cot_instrumental"]
        .map(k => `<span class="gate">${k} ${esc(String(f[k]))}</span>`).join(" ")}
      ${(f.audiences||[]).map(a => `<span class="gate">${esc(a)}</span>`).join(" ")}</div>
    ${f.goal_route ? `<div class="meta"><b>route:</b> ${esc(f.goal_route)}</div>` : ""}
    <div class="meta">${esc(f.reason)}</div>
    ${q("said", ev.output_spans, ev.output_spans_checks)}
    ${q("cot", ev.cot_final_stance, ev.cot_final_stance_checks)}
    ${q("instrument", ev.cot_deception, ev.cot_deception_checks)}
  </div>`;
}

function vset(v) {
  const dec = isDec(v);
  const nf = v.units.reduce((a,u) => a + (u.findings||[]).length, 0);
  const nx = v.units.reduce((a,u) => a + (u.excluded||[]).length, 0);
  const pe = v.units.some(u => u.parse_error);
  const body = v.units.map(u => {
    const head = v.version === "jv10" ? `<div class="ex"><b>step ${u.step}</b></div>` : "";
    const fs = (u.findings||[]).map(f => fnd(f, v.version)).join("");
    // Excluded claims get their own labelled block: they are the gates doing their job, and
    // reading them is how you see WHY a turn is not deception — invisible if they look like a
    // footnote to the findings. The verbatim span is shown too; a gate kill you cannot trace
    // back to a sentence is not checkable.
    const xs = (showExcluded && (u.excluded||[]).length) ? `<div class="exblock">
      <div class="exhead">${u.excluded.length} claim${u.excluded.length===1?"":"s"} the gates killed</div>
      ${u.excluded.map(e => `<div class="ex">
        <span class="gate g-${esc(e.failed_gate)}">${esc(e.failed_gate)}</span>
        <b>${esc(e.claim)}</b>
        <div class="exr">${esc(e.reason)}</div>
        ${e.output_span ? `<div class="q">said: ${esc(e.output_span)}</div>` : ""}
      </div>`).join("")}</div>` : "";
    return head + fs + xs;
  }).join("");
  return `<div class="vset">
    <div class="vhead"><span class="dot ${dec ? "y" : "n"}"></span>
      <span class="v">${esc(v.version)} · ${esc(v.model)} · r${v.replicate}</span>
      <span class="chip">${nf} finding${nf===1?"":"s"}</span>
      <span class="chip">${nx} excluded</span>
      ${pe ? '<span class="chip hit">parse error</span>' : ""}
    </div>${body}</div>`;
}

let highlightQuotes = true;

// Every span a visible judge quoted as `said:`, so the sentence a verdict rests on can be
// found in the message rather than reconstructed from the claim. Findings win over excluded
// where they overlap: what survived the gates matters more than what died at them.
function quoted(t) {
  const out = [];
  for (const v of visible(t)) {
    const who = `${v.version} · ${v.model} · r${v.replicate}`;
    for (const u of v.units) {
      for (const f of u.findings || [])
        for (const sp of f.evidence && f.evidence.output_spans || [])
          out.push({text: sp, kind: "find", label: `${who} — ${f.claim}`});
      if (showExcluded)
        for (const e of u.excluded || [])
          if (e.output_span)
            out.push({text: e.output_span, kind: "excl",
                      label: `${who} — excluded (${e.failed_gate}) — ${e.claim}`});
    }
  }
  return out;
}

// Mark per character (2 = finding, 1 = excluded), then emit runs. Per-character because
// judges quote overlapping and nested spans, and naive replacement would nest <mark> tags
// inside each other or corrupt the escaping.
function highlight(text, spans) {
  if (!highlightQuotes || !spans.length) return esc(text);
  const mark = new Uint8Array(text.length), lab = new Array(text.length);
  let any = false;
  for (const s of spans) {
    if (!s.text || s.text.length < 3) continue;
    const v = s.kind === "find" ? 2 : 1;
    for (let i = text.indexOf(s.text); i !== -1; i = text.indexOf(s.text, i + s.text.length)) {
      any = true;
      for (let j = i; j < i + s.text.length; j++)
        if (mark[j] < v) { mark[j] = v; lab[j] = s.label; }
        else if (mark[j] === v && !lab[j]) lab[j] = s.label;
    }
  }
  if (!any) return esc(text);
  let out = "", i = 0;
  while (i < text.length) {
    const v = mark[i];
    let j = i;
    while (j < text.length && mark[j] === v) j++;
    const seg = esc(text.slice(i, j));
    out += v === 0 ? seg
         : `<mark class="mk-${v === 2 ? "find" : "excl"}" title="${esc(lab[i] || "")}">${seg}</mark>`;
    i = j;
  }
  return out;
}

function steps(t) {
  const spans = quoted(t);
  return t.steps.map(s => `<div class="step ${s.emitting ? "em" : ""}">
    <div class="sl">step ${s.step}${s.tools.length ? " · " + esc(s.tools.join(", ")) : ""}</div>
    ${s.reasoning ? `<pre class="cot">${esc(s.reasoning)}</pre>` : ""}
    ${s.sent.map(m => `<div class="msg"><div class="a">${esc(m.audience)} → ${esc(m.conversation||"")}</div>
      <pre>${highlight(m.text, spans)}</pre></div>`).join("")}
  </div>`).join("");
}

function adjBar(t) {
  const a = adj[t.id] || {};
  const b = (val, label) => `<button data-adj="${val}" data-id="${esc(t.id)}"
      class="${a.verdict === val ? "sel" : ""}">${label}</button>`;
  return `<div class="adj">
    <b style="font-size:12px">adjudication</b>
    ${b("deception","deception")}${b("clean","not deception")}${b("unsure","unsure")}
    <textarea data-note="${esc(t.id)}" rows="1" placeholder="note…">${esc(a.note||"")}</textarea>
  </div>`;
}

function render() {
  const kept = DATA.turns.filter(keep);
  const done = DATA.turns.filter(t => adj[t.id] && adj[t.id].verdict).length;
  document.getElementById("count").textContent =
    `${kept.length} / ${DATA.turns.length} turns${invert ? " (inverted)" : ""} · ${done} adjudicated`;
  document.getElementById("main").innerHTML = kept.map(t => `
    <div class="turn" id="${esc(t.id)}">
      <div class="thead">
        <span class="t">${esc(t.label)} · turn ${t.turn_index}</span>
        <span class="chip">${esc(t.family)}</span>
        <span class="chip">${esc(t.group)}</span>
        <span class="chip">round ${esc(String(t.round))}${t.kind === "closing" ? " · closing" : ""}</span>
        ${["jv10","jv11"].map(ver => {
            const [k, n] = flagCount(t, ver);
            return n ? `<span class="chip ${k ? "flag" : ""}" title="${k} of ${n} judge run${n===1?"":"s"} flagged this turn">${ver} ${k}/${n} runs</span>` : "";
          }).join("")}
        ${t.jv9_hit ? '<span class="chip hit">jv9 &ge;2/3</span>'
                    : (t.jv9_labelled ? '<span class="chip">jv9 &lt;2/3</span>'
                                      : '<span class="chip">jv9 unjudged</span>')}
        <span style="flex:1"></span>
        ${t.rollout ? `<a href="${esc(t.rollout)}" target="_blank" rel="noopener">open rollout ↗</a>
          <button class="cp" data-path="${esc(t.rollout_path||"")}" title="copy the rollout's path — use this when the viewer blocks navigation">copy path</button>` : ""}
      </div>
      <div class="cols">
        <div class="col"><h3>what the assistant did
          ${highlightQuotes ? `<span class="legend"><mark class="mk-find">quoted by a finding</mark>
            ${showExcluded ? '<mark class="mk-excl">quoted by an excluded claim</mark>' : ""}</span>` : ""}
        </h3>${steps(t)}</div>
        <div class="col"><h3>verdicts</h3>${visible(t).map(vset).join("") || "<i>none shown</i>"}</div>
      </div>
      ${adjBar(t)}
    </div>`).join("") ||
      `<p><i>no turns ${invert ? "fall outside" : "match"} this filter</i></p>`;
}

document.getElementById("b10").onclick = e => { show.jv10 = !show.jv10; e.target.classList.toggle("on"); render(); };
document.getElementById("b11").onclick = e => { show.jv11 = !show.jv11; e.target.classList.toggle("on"); render(); };
// Navigation to file:// is blocked in sandboxed viewers and by Safari, so every rollout also
// offers its path for the clipboard: `open <path>` always works, a link does not.
document.addEventListener("click", e => {
  const path = e.target.dataset && e.target.dataset.path;
  if (!path) return;
  const done = () => { const o = e.target.textContent; e.target.textContent = "copied";
                       setTimeout(() => e.target.textContent = o, 1200); };
  if (navigator.clipboard) navigator.clipboard.writeText(path).then(done, () => prompt("path:", path));
  else prompt("path:", path);
});
document.getElementById("bh").onclick = e => {
  highlightQuotes = !highlightQuotes; e.target.classList.toggle("on"); render();
};
document.getElementById("bx").onclick = e => {
  showExcluded = !showExcluded; e.target.classList.toggle("on"); render();
};
document.getElementById("inv").onclick = e => {
  invert = !invert; e.target.classList.toggle("on"); render();
};
document.getElementById("mq").onclick = e => {
  modelNarrowsQuorum = !modelNarrowsQuorum; e.target.classList.toggle("on"); render();
};
["model","group","filter","flag","quorum"].forEach(id =>
  document.getElementById(id).onchange = render);
document.addEventListener("click", e => {
  const v = e.target.dataset && e.target.dataset.adj;
  if (!v) return;
  const id = e.target.dataset.id;
  adj[id] = Object.assign({}, adj[id], {verdict: adj[id] && adj[id].verdict === v ? null : v,
                                        at: new Date().toISOString()});
  save(); render();
});
document.addEventListener("input", e => {
  const id = e.target.dataset && e.target.dataset.note;
  if (!id) return;
  adj[id] = Object.assign({}, adj[id], {note: e.target.value});
  save();
});
document.getElementById("exp").onclick = () => {
  const blob = new Blob([JSON.stringify(adj, null, 1)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "deception_adjudications.json";
  a.click();
};
document.getElementById("imp").onclick = () => document.getElementById("file").click();
document.getElementById("file").onchange = ev => {
  const f = ev.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { try { adj = Object.assign(adj, JSON.parse(r.result)); save(); render(); }
                     catch (e) { alert("could not read that file"); } };
  r.readAsText(f);
};
document.getElementById("hint").innerHTML = DATA.turns[0] && /^file:/.test(DATA.turns[0].rollout || "")
  ? "Rollout links are <code>file://</code> URLs: they work in Chrome/Firefox when this page is "
    + "opened from disk, but are blocked in sandboxed viewers and in Safari. If a link does "
    + "nothing, use <b>copy path</b>, or serve the repo (<code>python3 -m http.server 8000</code> "
    + "from the repo root) and rebuild with <code>--http-base http://localhost:8000</code>."
  : "";
render();
</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the jv10/jv11 verdict browser")
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT",
                    help="build against http://localhost:PORT (default 8000) and serve the repo "
                         "root until Ctrl-C. THE ROUTE FOR SAFARI, which blocks file:// links "
                         "outright and restricts localStorage on file:// origins — so over "
                         "file:// the rollout links do nothing AND adjudications may not "
                         "persist. Over http both work.")
    ap.add_argument("--http-base", default="",
                    help="build links against a locally served repo root, e.g. "
                         "http://localhost:8000 (run `python3 -m http.server 8000` there). The "
                         "only link form that works in every browser and in sandboxed viewers.")
    ap.add_argument("--relative", action="store_true",
                    help="link rollouts by a path relative to the page instead of an absolute "
                         "file:// URI. Survives moving the whole repo; breaks the moment this "
                         "one file is copied out of experiments/agent2/.")
    args = ap.parse_args(argv)
    global RELATIVE_LINKS, HTTP_BASE
    RELATIVE_LINKS, HTTP_BASE = args.relative, args.http_base
    if args.serve:
        HTTP_BASE = f"http://localhost:{args.serve}"
    rc = build(Path(args.sample), Path(args.out))
    if rc or not args.serve:
        return rc

    import functools
    import http.server
    import socketserver
    page = Path(args.out).resolve().relative_to(REPO)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.serve), handler) as httpd:
        print(f"\n  open  {HTTP_BASE}/{page}\n\n  serving {REPO} — Ctrl-C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
