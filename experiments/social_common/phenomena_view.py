#!/usr/bin/env python3
from __future__ import annotations

"""Shared engine for the interactive "how often do we get which phenomena?" viewers.

This is the experiment-agnostic core behind ``social_jira2/phenomena_view.py`` and
``social_jira3/phenomena_view.py``. It combines the interactive HTML shell of
``social_jira2/visualize_judgements.py`` (split-into-columns per axis, value-subset
filters, metric toggle, drill-down into flagged turns) with the richer semantics of
``social_jira3/phenomena_hist.py`` (arbitrary axis set, completion-gating, level-2
phenomena folding, taxonomy-from-prompt, multi-root scans).

An *assistant type* is the tuple of the experiment's split axes (see ``Adapter.dimensions``);
incidents are summed over seeds/samples unless those are split too. Everything downstream of
``build_data`` is identical for both experiments — only the small :class:`Adapter` differs.

Both experiments carry every axis value in the ``judge_results.json`` body (model_label,
confidentiality, hint, ...), so axes are read from the JSON, not parsed from path names.

The metric toggle in the HTML already expresses ``phenomena_hist.py``'s ``--rollout``
distinction: "per-turn rate" == turn-occurrence mode, "per-run ever" == rollout mode.
"""

import argparse
import json
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_OUT_NAME = "phenomena_view.html"


@dataclass
class Adapter:
    """Per-experiment configuration. The HTML/JS/engine is shared; only this varies."""

    key: str                                   # e.g. "social_jira2" (used in titles/filenames)
    title: str                                 # HTML <title> / header text
    here: Path                                 # the adapter script's dir (for default out path)
    dimensions: List[Tuple[str, str]]          # (json_body_field, display_label) split axes
    taxonomy: Callable[[], List[str]]          # canonical phenomenon order (may return [])
    default_roots: Callable[[], List[Path]]    # dirs to scan when no root arg is given
    l2_map: Dict[str, str] = field(default_factory=dict)  # l2 filename -> display phenomenon
    results_name: str = "judge_results.json"
    # Axis values are read from the judge_results.json body by field name; an entry here
    # overrides that for one axis, computing its value from (summary, judge_path) instead —
    # e.g. a short model alias, or a `sample` that only exists in the run-dir name.
    derive: Dict[str, Callable[[Dict[str, Any], Path], Any]] = field(default_factory=dict)
    # Optional extra roots foldable in via a top-right UI switch (default off). Their runs are
    # loaded and tagged extra=True so both viewers can include/exclude them live without a rebuild.
    # Only auto-added when scanning the DEFAULT set (ignored when explicit roots are passed).
    extra_roots: List[Path] = field(default_factory=list)
    extra_label: str = ""


# --------------------------------------------------------------------------- data building
def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def find_results(root: Path, name: str) -> List[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob(name))


def run_completed(judge_path: Path) -> bool:
    """True if the sibling metrics.json says the run completed (or is absent/unreadable).

    Mirrors phenomena_hist.py: a crashed/truncated run (status != 'completed') is not a
    valid observation; runs with no readable metrics.json are kept (treated as completed).
    """
    try:
        status = _load_json(judge_path.parent / "metrics.json").get("status")
    except Exception:
        return True
    return status in (None, "completed")


def _add_detail(details: Dict[str, List[Dict[str, Any]]], name: str, turn: Dict[str, Any],
                phen_entry: Optional[Dict[str, Any]] = None) -> None:
    src = phen_entry if isinstance(phen_entry, dict) else turn
    details.setdefault(name, []).append({
        "turn": turn.get("turn_index"),
        "agent": turn.get("agent"),
        "phase": turn.get("phase"),
        "spans": src.get("spans", []) or [],
        "note": src.get("note", "") or "",
    })


def build_run_record(adapter: Adapter, summary: Dict[str, Any], judge_path: Path,
                     rel_id: str) -> Dict[str, Any]:
    """One run = one judged scenario leaf. ``details`` maps phenomenon -> flagged-turn evidence
    (one taxonomy entry == one flag, matching aggregate/phenomena_hist counting)."""
    details: Dict[str, List[Dict[str, Any]]] = {}
    parse_errors = 0
    for turn in summary.get("turns", []) or []:
        if turn.get("parse_error"):
            parse_errors += 1
        for ph in turn.get("present_phenomena", []) or []:
            if not isinstance(ph, dict):
                ph = {"phenomenon": str(ph)}
            _add_detail(details, ph.get("phenomenon", "<unnamed>"), turn, ph)

    # Fold level-2 confirmation passes (sibling files) as independent phenomena: one flag per
    # confirmed turn (present == True), counted like any other phenomenon (matches phenomena_hist).
    for l2_file, l2_name in adapter.l2_map.items():
        l2_path = judge_path.parent / l2_file
        if not l2_path.is_file():
            continue
        try:
            l2d = _load_json(l2_path)
        except Exception:
            continue
        for turn in l2d.get("turns", []) or []:
            if turn.get("present"):
                _add_detail(details, l2_name, turn)

    rec: Dict[str, Any] = {
        "id": rel_id,
        "n_turns": int(summary.get("num_turns") or len(summary.get("turns", []) or [])),
        "seed": summary.get("seed"),
        "parse_errors": parse_errors,
        "completed": run_completed(judge_path),
        "details": details,
    }
    for fieldname, _label in adapter.dimensions:
        rec[fieldname] = summary.get(fieldname)
    # model_label can be missing on older runs; fall back to the raw model id.
    if "model_label" in dict(adapter.dimensions) and rec.get("model_label") in (None, "None"):
        rec["model_label"] = summary.get("model")
    # Derived axes override the raw body read (short model alias, sample from run_dir, ...).
    for fieldname, fn in adapter.derive.items():
        rec[fieldname] = fn(summary, judge_path)
    return rec


def _rel_id(path: Path, root: Path, multi_root: bool) -> str:
    """A stable, human-readable id for a run leaf; prefixed with the root name when scanning
    several roots so leaves from different trees never collide in the drill-down."""
    try:
        rel = str(path.parent.relative_to(root))
    except ValueError:
        rel = path.parent.name
    return f"{root.name}/{rel}" if multi_root else rel


def _ordered_phenomena(adapter: Adapter, seen: set[str]) -> List[str]:
    """Canonical taxonomy order, with L2 phenomena slotted after their L1 sibling (or appended),
    then any observed-but-unlisted names appended so nothing is hidden."""
    order = list(adapter.taxonomy())
    for l2_name in adapter.l2_map.values():
        if l2_name in order:
            continue
        base = l2_name[3:] if l2_name.startswith("L2 ") else None
        if base and base in order:
            order.insert(order.index(base) + 1, l2_name)
        else:
            order.append(l2_name)
    for name in sorted(seen):
        if name not in order:
            order.append(name)
    return order


def build_data(adapter: Adapter, roots: List[Path],
               extra_roots: List[Path] = ()) -> Dict[str, Any]:
    all_roots = list(roots) + list(extra_roots)
    extra_set = {r.resolve() for r in extra_roots}
    multi_root = len(all_roots) > 1
    runs: List[Dict[str, Any]] = []
    for root in all_roots:
        is_extra = root.resolve() in extra_set
        for path in find_results(root, adapter.results_name):
            try:
                summary = _load_json(path)
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] {path}: {exc}", file=sys.stderr)
                continue
            if "turns" not in summary:
                continue
            rec = build_run_record(adapter, summary, path, _rel_id(path, root, multi_root))
            rec["extra"] = is_extra
            runs.append(rec)

    dims = [d for d, _ in adapter.dimensions]
    dim_labels = {d: lbl for d, lbl in adapter.dimensions}
    dim_values: Dict[str, List[str]] = {}
    for d in dims:
        dim_values[d] = sorted({str(r.get(d)) for r in runs}, key=lambda s: (s == "None", s))

    seen_phen = {p for r in runs for p in r["details"]}
    n_incomplete = sum(1 for r in runs if not r["completed"])

    return {
        "title": adapter.title,
        "roots": [str(r) for r in roots],
        "n_runs": len(runs),
        "n_incomplete": n_incomplete,
        "dimensions": dims,
        "dim_labels": dim_labels,
        "dim_values": dim_values,
        "phenomena": _ordered_phenomena(adapter, seen_phen),
        "has_extra": any(r["extra"] for r in runs),
        "extra_label": adapter.extra_label,
        "runs": runs,
    }


def render_html(data: Dict[str, Any]) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = data["title"].replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", blob)


# --------------------------------------------------------------------------- shared CLI
def run_cli(adapter: Adapter, argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=f"Interactive HTML view of {adapter.key} judge phenomena.")
    ap.add_argument("root", type=Path, nargs="*",
                    help="dir(s) of judge_results.json to scan (default: the experiment's "
                         "standard output set).")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"output HTML path (default: {DEFAULT_OUT_NAME} in the single root, or "
                         f"outputs/{adapter.key}_{DEFAULT_OUT_NAME} for a multi-root scan).")
    ap.add_argument("--open", action="store_true", help="open the result in a browser.")
    args = ap.parse_args(argv)

    if args.root:
        roots = [r.resolve() for r in args.root]
        extra_roots: List[Path] = []
    else:
        roots = [r.resolve() for r in adapter.default_roots()]
        extra_roots = [r.resolve() for r in adapter.extra_roots]
    if not roots:
        print("no roots to scan (empty default set)", file=sys.stderr)
        return 1
    missing = [r for r in roots if not r.exists()]
    for r in missing:
        print(f"no such path: {r}", file=sys.stderr)
    if missing:
        return 2
    absent_extra = [r for r in extra_roots if not r.exists()]
    for r in absent_extra:
        print(f"# extra root absent, skipping: {r}", file=sys.stderr)
    extra_roots = [r for r in extra_roots if r.exists()]
    if not args.root:
        print(f"# default scan of {len(roots)} dir(s)"
              + (f" (+{len(extra_roots)} optional)" if extra_roots else "") + ":")
        for r in roots + extra_roots:
            print(f"#   {r}")

    data = build_data(adapter, roots, extra_roots)
    if not data["runs"]:
        print(f"no {adapter.results_name} found under the given root(s)", file=sys.stderr)
        return 1

    if args.out:
        out = args.out
    elif len(roots) == 1 and roots[0].is_dir():
        out = roots[0] / DEFAULT_OUT_NAME
    else:
        (adapter.here / "outputs").mkdir(exist_ok=True)
        out = adapter.here / "outputs" / f"{adapter.key}_{DEFAULT_OUT_NAME}"
    out.write_text(render_html(data), encoding="utf-8")
    print(f"[wrote] {out}  ({data['n_runs']} runs, {len(data['phenomena'])} phenomena, "
          f"{data['n_incomplete']} incomplete)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


# --------------------------------------------------------------------- HTML / JS / CSS
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --fg:#e6e8ec; --muted:#9aa3b2;
          --accent:#6ea8fe; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { padding:14px 18px; border-bottom:1px solid var(--line); background:var(--panel);
           display:flex; justify-content:space-between; align-items:center; gap:16px; }
  header h1 { margin:0 0 2px; font-size:16px; }
  .switch { display:inline-flex; gap:7px; align-items:center; font-size:12px; color:var(--fg);
            cursor:pointer; user-select:none; white-space:nowrap; }
  .switch input { appearance:none; -webkit-appearance:none; width:30px; height:16px; border-radius:999px;
                  background:#2a2f3a; position:relative; cursor:pointer; outline:none; transition:background .15s; }
  .switch input:checked { background:var(--accent); }
  .switch input::after { content:""; position:absolute; top:2px; left:2px; width:12px; height:12px;
                         border-radius:50%; background:#e6e8ec; transition:left .15s; }
  .switch input:checked::after { left:16px; }
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
  .metricBar label { font-size:12px; color:var(--muted); display:flex; gap:5px; align-items:center; }
  .phenBar { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; align-items:center; }
  .phenBar .lbl { font-size:11px; text-transform:uppercase; letter-spacing:.04em;
                  color:var(--muted); margin-right:2px; }
  .phenChip { font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid var(--line);
              background:#222733; color:var(--fg); cursor:pointer; user-select:none; }
  .phenChip:hover { border-color:var(--accent); }
  .phenChip.l2 { color:var(--accent); }
  .phenChip.off { opacity:.4; text-decoration:line-through; }
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
  .phenName.l2 { color:var(--accent); }
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
  <div><h1 id="pagetitle"></h1><div class="sub" id="rootline"></div></div>
  <label class="switch" id="extraWrap" style="display:none">
    <input type="checkbox" id="extraTog"><span id="extraLbl"></span></label>
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
      <label style="margin-left:10px"><input type="checkbox" id="hideEmptyRows"> hide empty rows</label>
      <label><input type="checkbox" id="includeIncomplete"> include incomplete runs</label>
      <span style="flex:1"></span>
      <span class="sub" id="viewstat" style="color:var(--muted);font-size:12px"></span>
    </div>
    <div class="phenBar" id="phenBar"></div>
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
const L2_SET = new Set(DATA.phenomena.filter(p => /^L2 /.test(p)));

// ---- UI state -------------------------------------------------------------
const state = {
  metric: "rate",
  hideEmpty: false,
  includeIncomplete: false,
  includeExtra: false,     // fold in the optional extra roots (top-right switch); default off
  phenHidden: new Set(),   // phenomena toggled off via the chip bar (removed from the table)
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
// A run is in-scope if every dimension's value is selected and (unless the toggle is on) it
// completed. Incomplete (crashed/truncated) runs are excluded from numerator AND denominator.
function inScope(run) {
  if (!state.includeExtra && run.extra) return false;
  if (!state.includeIncomplete && !run.completed) return false;
  return DATA.dimensions.every(d => state.selected[d].has(String(run[d])));
}
// Column key = the split dims' values (ordered); collapsed dims contribute nothing.
function colKeyOf(run) {
  return DATA.dimensions.filter(d => state.split[d]).map(d => String(run[d])).join(" │ ");
}
function splitDims() { return DATA.dimensions.filter(d => state.split[d]); }

function buildColumns() {
  const cols = new Map();   // key -> {key, parts, runs:[]}
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
  const r = Math.round(40 + t * 200), g = Math.round(70 + t * 90), b = Math.round(120 - t * 90);
  return `rgba(${r},${g},${b},${0.20 + 0.65 * t})`;
}

function fmtCell(st) {
  if (state.metric === "raw") return { main: String(st.flags), sub: `${st.nRuns} runs` };
  if (state.metric === "runrate") return { main: (100 * st.value).toFixed(0) + "%", sub: `${st.runsWith}/${st.nRuns}` };
  return { main: (100 * st.value).toFixed(1) + "%", sub: `${st.flags}/${st.turns}` };
}

function render() {
  const cols = buildColumns();
  const host = $("#tableHost");

  if (!cols.length) { host.innerHTML = `<div class="empty">No assistant types match the current
    filters — tick some values on the left.</div>`; return; }

  let phens = DATA.phenomena.filter(p => !state.phenHidden.has(p));
  if (state.hideEmpty) phens = phens.filter(p => cols.some(c => cellStat(c, p).flags > 0));

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
    html += `<th class="colhead" title="${dimsInCol.join(' │ ')}">${esc(lab)}
      <span class="colmeta">${c.runs.length} runs · ${nTurns} turns</span></th>`;
  }
  html += "</tr></thead><tbody>";
  for (const p of phens) {
    const nameCls = "rowhead phenName" + (L2_SET.has(p) ? " l2" : "");
    html += `<tr><td class="${nameCls}">${esc(p)}</td>`;
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

  if (state.sel && (state.phenHidden.has(state.sel.phen)
      || !cols.some(c => c.key === state.sel.colKey))) closeDrill();
}

// ---- phenomenon show/hide chips ------------------------------------------
function renderPhenBar() {
  const bar = $("#phenBar");
  bar.innerHTML = `<span class="lbl">phenomena</span>
    <button id="phenAll">all</button><button id="phenNone">none</button>`;
  for (const p of DATA.phenomena) {
    const chip = document.createElement("span");
    chip.className = "phenChip" + (L2_SET.has(p) ? " l2" : "")
      + (state.phenHidden.has(p) ? " off" : "");
    chip.textContent = p;
    chip.title = state.phenHidden.has(p) ? "hidden — click to show" : "shown — click to hide";
    chip.addEventListener("click", () => {
      if (state.phenHidden.has(p)) state.phenHidden.delete(p); else state.phenHidden.add(p);
      renderPhenBar(); render();
    });
    bar.appendChild(chip);
  }
  $("#phenAll").addEventListener("click", () => { state.phenHidden.clear(); renderPhenBar(); render(); });
  $("#phenNone").addEventListener("click", () => {
    state.phenHidden = new Set(DATA.phenomena); renderPhenBar(); render();
  });
}

// ---- drill-down -----------------------------------------------------------
function openDrill(phen, colKey, cols) {
  state.sel = { phen, colKey };
  const col = cols.find(c => c.key === colKey);
  const drill = $("#drill");
  drill.style.display = "block";
  const events = [];
  for (const r of col.runs) for (const ev of (r.details[phen] || [])) events.push({ r, ev });
  events.sort((a, b) => a.r.id.localeCompare(b.r.id) || ((a.ev.turn || 0) - (b.ev.turn || 0)));

  const lab = col.parts.length ? col.parts.map(v => v === "None" ? "—" : v).join(" │ ") : "(all)";
  $("#drillTitle").textContent =
    `${phen}  —  ${lab}   (${events.length} flag${events.length === 1 ? "" : "s"} across ${col.runs.length} runs)`;
  const body = $("#drillBody");
  if (!events.length) { body.innerHTML = `<div class="empty">No flagged turns.</div>`; }
  else {
    body.innerHTML = events.map(({ r, ev }) => {
      const spans = (ev.spans || []).map(s => `<div class="span">${esc(s)}</div>`).join("");
      const note = ev.note ? `<div class="note">${esc(ev.note)}</div>` : "";
      return `<div class="ev"><div class="meta"><code>${esc(r.id)}</code> · turn ${ev.turn}
        · ${esc(ev.agent || "?")} / ${esc(ev.phase || "?")}</div>${spans}${note}</div>`;
    }).join("");
  }
  render();
  drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function closeDrill() { state.sel = null; $("#drill").style.display = "none"; render(); }
function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;" }[c])); }

// ---- wiring ---------------------------------------------------------------
function rootLine() {
  const roots = DATA.roots.length === 1 ? DATA.roots[0] : `${DATA.roots.length} dirs`;
  const inc = DATA.n_incomplete ? `  ·  ${DATA.n_incomplete} incomplete` : "";
  return `${roots}  ·  ${DATA.n_runs} runs total${inc}`;
}
$("#pagetitle").textContent = DATA.title;
$("#rootline").textContent = rootLine();
$("#rootline").title = DATA.roots.join("\n");

// Top-right switch: fold the optional extra roots (e.g. v6 qwen seeds 3-6) in/out. Only shown
// when the build actually loaded extra runs; default off.
if (DATA.has_extra) {
  $("#extraLbl").textContent = DATA.extra_label || "include extra data";
  $("#extraWrap").style.display = "inline-flex";
  $("#extraTog").checked = state.includeExtra;
  $("#extraTog").addEventListener("change", e => { state.includeExtra = e.target.checked; render(); });
}

$("#metricSeg").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
  state.metric = b.dataset.m;
  $("#metricSeg").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
  render();
}));
$("#hideEmptyRows").addEventListener("change", e => { state.hideEmpty = e.target.checked; render(); });
$("#includeIncomplete").addEventListener("change", e => { state.includeIncomplete = e.target.checked; render(); });
$("#drillClose").addEventListener("click", closeDrill);

renderSidebar();
renderPhenBar();
render();
</script>
</body>
</html>
"""
