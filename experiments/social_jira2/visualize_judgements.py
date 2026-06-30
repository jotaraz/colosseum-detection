#!/usr/bin/env python3
from __future__ import annotations

"""Build a self-contained, interactive HTML view of social_jira2 judge results.

This is the *aggregate* counterpart to ``viewer/viewer.py`` (which reads one scenario's
transcript at a time). Here we walk a directory of ``judge_results.json`` files (written by
``judge.py``), tabulate how often each phenomenon was flagged, and emit a single static HTML
file. All interactivity (which assistant types to compare, which metric to show, drilling into
the flagged turns behind a cell) happens client-side in embedded JS — no server, fully
shareable.

An *assistant type* is the 5-tuple ``(model, feelings_preset, scenario_type, setup,
personality)``; incidents are summed over seeds and samples. In the HTML you choose which of
those 5 dimensions to **split** into columns and which to **collapse** (sum over), and you can
restrict each dimension to a subset of its values. The table is phenomena (rows) x selected
assistant-type columns; each cell is a per-turn rate by default, with toggles for the per-run
"ever" rate and raw counts, and clicking a cell expands the flagged turns (judge spans + notes)
behind it.

Usage:
    python visualize_judgements.py                      # default dir, writes judge_view.html
    python visualize_judgements.py <root>               # any dir of judge_results.json
    python visualize_judgements.py <root> --out foo.html [--open]
"""

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "outputs" / "social_jira2_c2p2_gptoss_120b_medium_conflict_newgen_merged"

# Canonical phenomenon order (mirrors aggregate_judgements.py / JUDGE_TURN_PROMPT.md). Keeps a
# stable row order and surfaces phenomena that never fired.
PHENOMENA = [
    "Leakage",
    "Implied Leakage",
    "Strategic Omission",
    "Strategic Framing",
    "Cover Story Planning",
    "Observer Suspicion Modeling",
    "Strategic Attention Manipulation",
    "Fabrication (considered)",
    "Fabrication (executed)",
    "Hallucination",
    "Made Bad Deal",
    "Out-of-Scope Proposal",
    "Vague",
    "Ambiguous",
]

# The dimensions that make up an assistant type (seed + sample are always summed over).
DIMENSIONS = ["model_label", "feelings_preset", "scenario_type", "setup", "personality"]
DIM_LABELS = {
    "model_label": "model",
    "feelings_preset": "feelings",
    "scenario_type": "scenario",
    "setup": "setup",
    "personality": "personality",
}

_SAMPLE_RE = re.compile(r"__s(\d+)$")


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def find_results(root: Path, name: str) -> List[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob(name))


def _sample_of(run_dir_name: str) -> Optional[int]:
    m = _SAMPLE_RE.search(run_dir_name)
    return int(m.group(1)) if m else None


def build_run_record(summary: Dict[str, Any], rel_id: str) -> Dict[str, Any]:
    """One run = one judged scenario leaf (a single seed x sample x assistant config).

    ``details`` maps phenomenon -> list of flagged turns (the drill-down evidence); the flag
    count for a phenomenon is just ``len(details[phenomenon])`` (one taxonomy entry == one flag,
    matching aggregate_judgements.py). ``n_turns`` is the per-turn-rate denominator.
    """
    details: Dict[str, List[Dict[str, Any]]] = {}
    parse_errors = 0
    for turn in summary.get("turns", []):
        if turn.get("parse_error"):
            parse_errors += 1
        for ph in turn.get("present_phenomena", []):
            if not isinstance(ph, dict):
                ph = {"phenomenon": str(ph)}
            name = ph.get("phenomenon", "<unnamed>")
            details.setdefault(name, []).append(
                {
                    "turn": turn.get("turn_index"),
                    "agent": turn.get("agent"),
                    "phase": turn.get("phase"),
                    "spans": ph.get("spans", []) or [],
                    "note": ph.get("note", "") or "",
                }
            )

    rec: Dict[str, Any] = {
        "id": rel_id,
        "n_turns": int(summary.get("num_turns") or len(summary.get("turns", []))),
        "seed": summary.get("seed"),
        "sample": _sample_of(Path(rel_id).name),
        "parse_errors": parse_errors,
        "details": details,
    }
    for dim in DIMENSIONS:
        rec[dim] = summary.get(dim)
    # model_label can be missing on older runs; fall back to the raw model id.
    if rec.get("model_label") in (None, "None"):
        rec["model_label"] = summary.get("model")
    return rec


def build_data(root: Path, results_name: str) -> Dict[str, Any]:
    files = find_results(root, results_name)
    runs: List[Dict[str, Any]] = []
    for path in files:
        try:
            summary = _load_json(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] {path}: {exc}", file=sys.stderr)
            continue
        if "turns" not in summary:
            continue
        try:
            rel_id = str(path.parent.relative_to(root))
        except ValueError:
            rel_id = path.parent.name
        runs.append(build_run_record(summary, rel_id))

    # Distinct values per dimension (sorted, stringified for stable JS keys).
    dim_values: Dict[str, List[str]] = {}
    for dim in DIMENSIONS:
        seen = sorted({str(r.get(dim)) for r in runs}, key=lambda s: (s == "None", s))
        dim_values[dim] = seen

    # Phenomena actually present that aren't in the canonical list (keep them visible).
    seen_phen = {p for r in runs for p in r["details"]}
    extra = sorted(p for p in seen_phen if p not in PHENOMENA)
    phenomena = PHENOMENA + extra

    return {
        "root": str(root),
        "n_runs": len(runs),
        "dimensions": DIMENSIONS,
        "dim_labels": DIM_LABELS,
        "dim_values": dim_values,
        "phenomena": phenomena,
        "runs": runs,
    }


def render_html(data: Dict[str, Any]) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__DATA__", blob)


# --------------------------------------------------------------------- HTML / JS / CSS
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>social_jira2 — judge phenomena</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --fg:#e6e8ec; --muted:#9aa3b2;
          --accent:#6ea8fe; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { padding:14px 18px; border-bottom:1px solid var(--line); background:var(--panel); }
  header h1 { margin:0 0 2px; font-size:16px; }
  header .sub { color:var(--muted); font-size:12px; }
  .wrap { display:flex; gap:0; align-items:flex-start; }
  #side { width:300px; flex:0 0 300px; padding:14px; border-right:1px solid var(--line);
          background:var(--panel); height:calc(100vh - 56px); overflow:auto; position:sticky;
          top:0; }
  #main { flex:1 1 auto; padding:14px 18px; overflow:auto; height:calc(100vh - 56px); }
  .dim { margin-bottom:14px; border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
  .dim h3 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.04em;
            color:var(--muted); display:flex; justify-content:space-between; align-items:center; }
  .splitToggle { font-size:11px; color:var(--fg); display:flex; gap:5px; align-items:center;
                 cursor:pointer; text-transform:none; letter-spacing:0; }
  .vals { display:flex; flex-direction:column; gap:3px; max-height:170px; overflow:auto; }
  .vals label { display:flex; gap:6px; align-items:center; font-size:13px; cursor:pointer;
                color:var(--fg); }
  .vals.collapsed { opacity:.5; }
  .miniBtns { display:flex; gap:6px; margin-top:5px; }
  .miniBtns button { font-size:11px; }
  button { background:#222733; color:var(--fg); border:1px solid var(--line); border-radius:6px;
           padding:4px 8px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  .metricBar { display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap; align-items:center; }
  .metricBar .seg button.active { background:var(--accent); color:#0b0d11; border-color:var(--accent); }
  table { border-collapse:collapse; width:100%; }
  th, td { border:1px solid var(--line); padding:6px 8px; text-align:center; }
  th.rowhead, td.rowhead { text-align:left; white-space:nowrap; position:sticky; left:0;
                           background:var(--panel); z-index:2; }
  thead th { background:var(--panel); position:sticky; top:0; z-index:3; vertical-align:bottom; }
  thead th.colhead { font-weight:600; }
  thead th .colmeta { display:block; font-weight:400; color:var(--muted); font-size:11px; }
  td.cell { cursor:pointer; font-variant-numeric:tabular-nums; }
  td.cell:hover { outline:2px solid var(--accent); outline-offset:-2px; }
  td.cell .sub { display:block; font-size:10px; color:var(--muted); }
  td.cell.zero { color:var(--muted); }
  td.cell.sel { box-shadow: inset 0 0 0 2px var(--accent); }
  .phenName { font-weight:600; }
  #drill { margin-top:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); }
  #drill .dh { padding:10px 12px; border-bottom:1px solid var(--line); display:flex;
               justify-content:space-between; align-items:center; }
  #drill .body { padding:6px 12px 12px; max-height:48vh; overflow:auto; }
  .ev { border-bottom:1px solid var(--line); padding:8px 0; }
  .ev .meta { color:var(--muted); font-size:12px; margin-bottom:3px; }
  .ev .span { background:#1d2330; border-left:3px solid var(--accent); padding:4px 8px;
              margin:3px 0; border-radius:0 4px 4px 0; white-space:pre-wrap; }
  .ev .note { color:#cdd3dd; font-style:italic; margin-top:3px; }
  .empty { color:var(--muted); padding:20px; }
  code { background:#1d2330; padding:1px 4px; border-radius:3px; }
</style>
</head>
<body>
<header>
  <h1>social_jira2 — judge phenomena</h1>
  <div class="sub" id="rootline"></div>
</header>
<div class="wrap">
  <aside id="side"></aside>
  <main id="main">
    <div class="metricBar">
      <div class="seg" id="metricSeg">
        <button data-m="rate" class="active">per-turn rate</button>
        <button data-m="runrate">per-run "ever"</button>
        <button data-m="raw">raw flags</button>
      </div>
      <label style="margin-left:10px;font-size:12px;color:var(--muted)">
        <input type="checkbox" id="hideEmptyRows"> hide phenomena with no flags
      </label>
      <span style="flex:1"></span>
      <span class="sub" id="viewstat" style="color:var(--muted);font-size:12px"></span>
    </div>
    <div id="tableHost"></div>
    <div id="drill" style="display:none">
      <div class="dh"><strong id="drillTitle"></strong><button id="drillClose">close</button></div>
      <div class="body" id="drillBody"></div>
    </div>
  </main>
</div>
<script>
const DATA = __DATA__;
const $ = (s, r=document) => r.querySelector(s);

// ---- UI state -------------------------------------------------------------
const state = {
  metric: "rate",
  hideEmpty: false,
  split: {},        // dim -> bool (split into columns vs collapse/sum)
  selected: {},     // dim -> Set of chosen values
  sel: null,        // {phen, colKey} of the open drill-down cell
};
// Default: split dimensions that actually vary; collapse constant ones; all values on.
for (const dim of DATA.dimensions) {
  const vals = DATA.dim_values[dim];
  state.split[dim] = vals.length > 1;
  state.selected[dim] = new Set(vals);
}

// ---- sidebar (dimension filters) -----------------------------------------
function renderSidebar() {
  const side = $("#side");
  side.innerHTML = "";
  for (const dim of DATA.dimensions) {
    const vals = DATA.dim_values[dim];
    const box = document.createElement("div");
    box.className = "dim";
    const splitId = "split_" + dim;
    box.innerHTML = `<h3>${DATA.dim_labels[dim] || dim}
        <label class="splitToggle"><input type="checkbox" id="${splitId}"
          ${state.split[dim] ? "checked" : ""}> split into columns</label></h3>`;
    const valsDiv = document.createElement("div");
    valsDiv.className = "vals" + (state.split[dim] ? "" : " collapsed");
    for (const v of vals) {
      const id = `v_${dim}_${v}`;
      const lab = document.createElement("label");
      const checked = state.selected[dim].has(v) ? "checked" : "";
      const n = DATA.runs.filter(r => String(r[dim]) === v).length;
      lab.innerHTML = `<input type="checkbox" data-dim="${dim}" value="${v}" ${checked}>
        <span>${v === "None" ? "—" : v} <span style="color:var(--muted)">(${n})</span></span>`;
      valsDiv.appendChild(lab);
    }
    box.appendChild(valsDiv);
    const mini = document.createElement("div");
    mini.className = "miniBtns";
    mini.innerHTML = `<button data-all="${dim}">all</button><button data-none="${dim}">none</button>`;
    box.appendChild(mini);
    side.appendChild(box);

    box.querySelector("#" + splitId).addEventListener("change", e => {
      state.split[dim] = e.target.checked;
      valsDiv.classList.toggle("collapsed", !e.target.checked);
      render();
    });
    valsDiv.querySelectorAll("input").forEach(cb => cb.addEventListener("change", e => {
      const set = state.selected[dim];
      if (e.target.checked) set.add(e.target.value); else set.delete(e.target.value);
      render();
    }));
    mini.querySelector(`[data-all="${dim}"]`).addEventListener("click", () => {
      state.selected[dim] = new Set(vals); renderSidebar(); render();
    });
    mini.querySelector(`[data-none="${dim}"]`).addEventListener("click", () => {
      state.selected[dim] = new Set(); renderSidebar(); render();
    });
  }
}

// ---- aggregation ----------------------------------------------------------
// A run is in-scope if every dimension's value is among that dim's selected values.
function inScope(run) {
  return DATA.dimensions.every(d => state.selected[d].has(String(run[d])));
}
// Column key = the split dims' values (ordered); collapsed dims contribute nothing.
function colKeyOf(run) {
  return DATA.dimensions.filter(d => state.split[d]).map(d => String(run[d])).join(" │ ");
}
function splitDims() { return DATA.dimensions.filter(d => state.split[d]); }

function buildColumns() {
  const cols = new Map();   // key -> {key, label parts, runs:[]}
  for (const run of DATA.runs) {
    if (!inScope(run)) continue;
    const key = colKeyOf(run);
    if (!cols.has(key)) {
      const parts = splitDims().map(d => String(run[d]));
      cols.set(key, { key, parts, runs: [] });
    }
    cols.get(key).runs.push(run);
  }
  return [...cols.values()].sort((a, b) => a.key.localeCompare(b.key));
}

// Per (column, phenomenon): flags, turns, runs, runs-with-≥1, value for current metric.
function cellStat(col, phen) {
  let flags = 0, turns = 0, runsWith = 0;
  for (const r of col.runs) {
    turns += r.n_turns;
    const k = (r.details[phen] || []).length;
    flags += k;
    if (k > 0) runsWith += 1;
  }
  const nRuns = col.runs.length;
  let value;
  if (state.metric === "rate") value = turns ? flags / turns : 0;
  else if (state.metric === "runrate") value = nRuns ? runsWith / nRuns : 0;
  else value = flags;
  return { flags, turns, nRuns, runsWith, value };
}

// ---- rendering ------------------------------------------------------------
function heat(v, max) {
  if (!v || max <= 0) return "transparent";
  const t = Math.min(1, v / max);
  // muted blue -> hot orange
  const r = Math.round(40 + t * 200), g = Math.round(70 + t * 90), b = Math.round(120 - t * 90);
  return `rgba(${r},${g},${b},${0.20 + 0.65 * t})`;
}

function fmtCell(st) {
  if (state.metric === "raw") {
    return { main: String(st.flags), sub: `${st.nRuns} runs` };
  }
  if (state.metric === "runrate") {
    return { main: (100 * st.value).toFixed(0) + "%", sub: `${st.runsWith}/${st.nRuns}` };
  }
  return { main: (100 * st.value).toFixed(1) + "%", sub: `${st.flags}/${st.turns}` };
}

function render() {
  const cols = buildColumns();
  const host = $("#tableHost");
  $("#rootline").textContent = `${DATA.root}  ·  ${DATA.n_runs} runs total`;

  if (!cols.length) { host.innerHTML = `<div class="empty">No assistant types match the current
    filters — tick some values on the left.</div>`; return; }

  let phens = DATA.phenomena;
  if (state.hideEmpty) {
    phens = phens.filter(p => cols.some(c => cellStat(c, p).flags > 0));
  }

  // Pre-compute stats + max for the heat scale (per current metric, over visible cells).
  const stats = {};
  let max = 0;
  for (const p of phens) { stats[p] = {}; for (const c of cols) {
    const st = cellStat(c, p); stats[p][c.key] = st; if (st.value > max) max = st.value;
  } }

  const dimsInCol = splitDims().map(d => DATA.dim_labels[d] || d);
  let html = "<table><thead><tr><th class='rowhead'>phenomenon</th>";
  for (const c of cols) {
    const nTurns = c.runs.reduce((a, r) => a + r.n_turns, 0);
    const lab = c.parts.length ? c.parts.map(v => v === "None" ? "—" : v).join(" │ ") : "(all)";
    html += `<th class="colhead" title="${dimsInCol.join(' │ ')}">${lab}
      <span class="colmeta">${c.runs.length} runs · ${nTurns} turns</span></th>`;
  }
  html += "</tr></thead><tbody>";
  for (const p of phens) {
    html += `<tr><td class="rowhead phenName">${p}</td>`;
    for (const c of cols) {
      const st = stats[p][c.key];
      const f = fmtCell(st);
      const isSel = state.sel && state.sel.phen === p && state.sel.colKey === c.key;
      const cls = "cell" + (st.flags === 0 ? " zero" : "") + (isSel ? " sel" : "");
      const bg = st.flags === 0 ? "transparent" : heat(st.value, max);
      html += `<td class="${cls}" style="background:${bg}" data-phen="${encodeURIComponent(p)}"
        data-col="${encodeURIComponent(c.key)}">${f.main}<span class="sub">${f.sub}</span></td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  host.innerHTML = html;

  const nRunsShown = cols.reduce((a, c) => a + c.runs.length, 0);
  $("#viewstat").textContent =
    `${cols.length} columns · ${nRunsShown} runs in view · ${phens.length} phenomena`;

  host.querySelectorAll("td.cell").forEach(td => td.addEventListener("click", () => {
    openDrill(decodeURIComponent(td.dataset.phen), decodeURIComponent(td.dataset.col), cols);
  }));

  // keep drill-down in sync if its column still exists
  if (state.sel && !cols.some(c => c.key === state.sel.colKey)) closeDrill();
}

// ---- drill-down -----------------------------------------------------------
function openDrill(phen, colKey, cols) {
  state.sel = { phen, colKey };
  const col = cols.find(c => c.key === colKey);
  const drill = $("#drill");
  drill.style.display = "block";
  const events = [];
  for (const r of col.runs) for (const ev of (r.details[phen] || [])) events.push({ r, ev });
  events.sort((a, b) => a.r.id.localeCompare(b.r.id) || (a.ev.turn - b.ev.turn));

  const lab = col.parts.length ? col.parts.map(v => v === "None" ? "—" : v).join(" │ ") : "(all)";
  $("#drillTitle").textContent = `${phen}  —  ${lab}   (${events.length} flag${events.length===1?"":"s"} across ${col.runs.length} runs)`;
  const body = $("#drillBody");
  if (!events.length) { body.innerHTML = `<div class="empty">No flagged turns.</div>`; }
  else {
    body.innerHTML = events.map(({ r, ev }) => {
      const spans = (ev.spans || []).map(s => `<div class="span">${esc(s)}</div>`).join("");
      const note = ev.note ? `<div class="note">${esc(ev.note)}</div>` : "";
      return `<div class="ev"><div class="meta"><code>${esc(r.id)}</code> · turn ${ev.turn}
        · ${esc(ev.agent||"?")} / ${esc(ev.phase||"?")}</div>${spans}${note}</div>`;
    }).join("");
  }
  render();  // refresh cell selection highlight
  drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function closeDrill() { state.sel = null; $("#drill").style.display = "none"; render(); }
function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); }

// ---- wiring ---------------------------------------------------------------
$("#metricSeg").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
  state.metric = b.dataset.m;
  $("#metricSeg").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
  render();
}));
$("#hideEmptyRows").addEventListener("change", e => { state.hideEmpty = e.target.checked; render(); });
$("#drillClose").addEventListener("click", closeDrill);

renderSidebar();
render();
</script>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build an interactive HTML view of judge results.")
    ap.add_argument("root", type=Path, nargs="?", default=DEFAULT_ROOT,
                    help=f"Dir of judge_results.json files (default: {DEFAULT_ROOT.name}).")
    ap.add_argument("--results-name", default="judge_results.json")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output HTML path (default: judge_view.html inside <root>).")
    ap.add_argument("--open", action="store_true", help="Open the result in a browser.")
    args = ap.parse_args(argv)

    if not args.root.exists():
        print(f"No such directory: {args.root}", file=sys.stderr)
        return 1

    data = build_data(args.root, args.results_name)
    if not data["runs"]:
        print(f"No {args.results_name} found under {args.root}", file=sys.stderr)
        return 1

    out = args.out or (args.root / "judge_view.html")
    out.write_text(render_html(data), encoding="utf-8")
    print(f"[wrote] {out}  ({data['n_runs']} runs, {len(data['phenomena'])} phenomena)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
