#!/usr/bin/env python3
from __future__ import annotations

"""Shared engine for the interactive per-turn *evidence reader* (the inverse of
``phenomena_view.py``).

Where ``phenomena_view`` counts occurrences, this reads them: pick a filter subset (all quit /
quit=quit2 / all models / ...) and one or more phenomena, and each phenomenon becomes a column
of the actual flagged turns. Each turn card shows the full setting tuple, who said it, compact
badges for the *other* phenomena flagged on that turn, the flagged spans, a **CoT** button, and
a **show more** button that lazy-loads and renders the whole rollout transcript beside it.

Delivery is a small Flask app (like ``viewer/viewer.py``): the flagged-turn *index* (cheap, from
``judge_results.json`` + L2 files) is built once at startup and sent to the client, which does all
filtering/column-building; only the heavy per-run **transcripts** are fetched lazily on demand.

This shares the :class:`Adapter` and helpers of ``phenomena_view`` verbatim — an experiment is
described once, both tools consume it.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments.social_common.phenomena_view import (
    Adapter, _load_json, _ordered_phenomena, find_results, run_completed,
)

# Run under whatever interpreter has Flask (re-exec into the repo .venv if needed) — same shim
# as viewer/viewer.py so `python -m ...` works without the caller pre-activating the venv.
try:
    from flask import Flask, jsonify, request, Response
except ModuleNotFoundError:  # pragma: no cover - environment shim
    _here = Path(__file__).resolve()
    _venv_py = next((p / ".venv" / "bin" / "python" for p in _here.parents
                     if (p / ".venv" / "bin" / "python").exists()), None)
    if _venv_py is None or Path(sys.executable).resolve() == _venv_py.resolve():
        raise SystemExit(
            "Flask is not installed for this interpreter and no repo .venv with Flask was "
            "found.\nInstall it (`pip install flask`) or run with the repo venv.")
    os.execv(str(_venv_py), [str(_venv_py), *sys.argv])


# ------------------------------------------------------------------ short phenomenon codes
def short_codes(names: List[str]) -> Dict[str, str]:
    """1-3 char badge code per phenomenon, auto-derived from its name (initials of words, or
    the first two letters of a single word), de-duplicated. Full name shown on hover."""
    import re
    out: Dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        words = re.findall(r"[A-Za-z0-9]+", name)
        base = ("".join(w[0] for w in words[:3]).upper() if len(words) >= 2
                else (name[:2].capitalize() if name else "?"))
        code, extra = base, 0
        pool = "".join(w[1:] for w in words) + "0123456789"
        while code in used and extra < len(pool):
            code = (base + pool[extra]).upper()[:3]
            extra += 1
        used.add(code)
        out[name] = code
    return out


# --------------------------------------------------------------------------- flagged-turn index
def _settings(adapter: Adapter, summary: Dict[str, Any], judge_path: Path) -> Dict[str, Any]:
    """The run's axis values as {field: value} — same extraction (body + model fallback +
    derive) that phenomena_view uses for its columns, reused here for the card header/filters."""
    s = {f: summary.get(f) for f, _ in adapter.dimensions}
    if "model_label" in s and s.get("model_label") in (None, "None"):
        s["model_label"] = summary.get("model")
    for f, fn in adapter.derive.items():
        if f in s:
            s[f] = fn(summary, judge_path)
    return {f: (str(v) if v is not None else "None") for f, v in s.items()}


def build_index(adapter: Adapter, roots: List[Path],
                extra_roots: List[Path] = ()) -> Dict[str, Any]:
    """Walk the roots and build the flat flagged-turn index (no transcripts loaded). Runs from
    extra_roots are tagged extra=True so the client can fold them in/out via a top-right switch."""
    turns: List[Dict[str, Any]] = []
    seen_phen: set[str] = set()
    n_runs = n_incomplete = 0
    all_roots = list(roots) + list(extra_roots)
    extra_set = {r.resolve() for r in extra_roots}
    multi_root = len(all_roots) > 1

    for root in all_roots:
        is_extra = root.resolve() in extra_set
        for jpath in find_results(root, adapter.results_name):
            try:
                summary = _load_json(jpath)
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] {jpath}: {exc}", file=sys.stderr)
                continue
            if "turns" not in summary:
                continue
            n_runs += 1
            completed = run_completed(jpath)
            if not completed:
                n_incomplete += 1
            run_dir = str(jpath.parent.resolve())
            try:
                run_id = str(jpath.parent.relative_to(root))
                run_id = f"{root.name}/{run_id}" if multi_root else run_id
            except ValueError:
                run_id = jpath.parent.name
            settings = _settings(adapter, summary, jpath)

            # One record per (run, turn_index): all phenomena flagged on that turn + their spans.
            by_turn: Dict[int, Dict[str, Any]] = {}

            def _rec(turn: Dict[str, Any]) -> Dict[str, Any]:
                ti = turn.get("turn_index")
                if ti not in by_turn:
                    by_turn[ti] = {
                        "id": f"{run_id}#t{ti}", "run_id": run_id, "run_dir": run_dir,
                        "settings": settings, "completed": completed, "extra": is_extra,
                        "turn_index": ti, "agent": turn.get("agent"),
                        "phase": turn.get("phase"), "round": turn.get("round"),
                        "phenomena": [],
                    }
                return by_turn[ti]

            for turn in summary.get("turns") or []:
                for ph in turn.get("present_phenomena") or []:
                    if not isinstance(ph, dict):
                        ph = {"phenomenon": str(ph)}
                    name = ph.get("phenomenon", "<unnamed>")
                    seen_phen.add(name)
                    _rec(turn)["phenomena"].append(
                        {"name": name, "spans": ph.get("spans") or [], "note": ph.get("note") or ""})

            # Fold level-2 confirmations (sibling files) as independent phenomena on their turn.
            for l2_file, l2_name in adapter.l2_map.items():
                l2p = jpath.parent / l2_file
                if not l2p.is_file():
                    continue
                try:
                    l2d = _load_json(l2p)
                except Exception:
                    continue
                for turn in l2d.get("turns") or []:
                    if turn.get("present"):
                        seen_phen.add(l2_name)
                        _rec(turn)["phenomena"].append({
                            "name": l2_name, "spans": turn.get("spans") or [],
                            "note": turn.get("note") or ""})

            # Attach each flagged turn's full public message (response + tool actions) so the card
            # can show the whole assistant message, not just the excerpted spans, plus tx_index —
            # the agent_turns.json position (for the transcript highlight / CoT), which differs
            # from the judge's turn_index. Both come from (agent, phase, round) identity, not the
            # judge counter. Reads only agent_turns.json + tool_events.json; CoT stays lazy.
            if by_turn:
                msg_by_key, idx_by_key = _turn_lookup(jpath.parent)
                for k in sorted(by_turn):
                    rec = by_turn[k]
                    key = (rec["agent"], rec["phase"], _rnorm(rec["round"]))
                    rec["msg"] = msg_by_key.get(key, "")
                    rec["tx_index"] = idx_by_key.get(key)
                    turns.append(rec)

    phenomena = _ordered_phenomena(adapter, seen_phen)
    dim_values: Dict[str, List[str]] = {}
    for f, _ in adapter.dimensions:
        dim_values[f] = sorted({t["settings"].get(f, "None") for t in turns},
                               key=lambda s: (s == "None", s))
    return {
        "title": adapter.title,
        "roots": [str(r) for r in roots],
        "n_runs": n_runs,
        "n_incomplete": n_incomplete,
        "n_flagged_turns": len(turns),
        "dimensions": [f for f, _ in adapter.dimensions],
        "dim_labels": {f: lbl for f, lbl in adapter.dimensions},
        "dim_values": dim_values,
        "phenomena": phenomena,
        "phen_codes": short_codes(phenomena),
        "has_extra": any(t["extra"] for t in turns),
        "extra_label": adapter.extra_label,
        "turns": turns,
    }


# --------------------------------------------------------------------------- transcript assembly
def _round_num(key: Any) -> int:
    import re
    m = re.search(r"(\d+)$", str(key or ""))
    return int(m.group(1)) if m else 0


def _cot_map(run_dir: Path) -> Dict[tuple, str]:
    """(agent, phase, round_number) -> concatenated CoT, from agent_reasoning.json.

    Structure: {agent: {iteration_k: {phase: {round_j: {step_i: {reasoning_content}}}}}}.
    round_number is parsed from the round key (round_0/round_1/...); a turn's planning_round
    (1-based; None/execution -> 0) indexes straight into it."""
    p = run_dir / "agent_reasoning.json"
    if not p.exists():
        return {}
    try:
        rj = json.loads(p.read_text())
    except Exception:
        return {}
    out: Dict[tuple, str] = {}
    for agent, iters in (rj or {}).items():
        for _itk, phases in (iters or {}).items():
            for phase, rounds in (phases or {}).items():
                for rk, steps in (rounds or {}).items():
                    parts = [((steps[sk] or {}).get("reasoning_content") or "").strip()
                             for sk in sorted((steps or {}).keys(), key=_round_num)]
                    text = "\n\n".join(p for p in parts if p).strip()
                    if text:
                        out[(agent, phase, _round_num(rk))] = text
    return out


def _summarize_tool(ev: Dict[str, Any]) -> Dict[str, str]:
    """Compact public rendering of one tool event (the visible conversation action)."""
    name = ev.get("tool_name") or "tool"
    args = ev.get("arguments") or {}
    if isinstance(args, dict) and args.get("message"):
        text = str(args["message"])
    elif isinstance(args, dict):
        text = ", ".join(f"{k}={v}" for k, v in args.items())
    else:
        text = str(args)
    res = ev.get("result")
    res = "" if res in (None, "") else str(res)
    return {"name": name, "text": text, "result": res[:400]}


def _tool_matches(ev: Dict[str, Any], agent: Any, phase: Any, pr: Any) -> bool:
    """A tool event belongs to a turn if the actor/phase/planning-round line up (execution &
    summary turns have no planning_round, so treat falsy == falsy)."""
    return (ev.get("agent_name") == agent and ev.get("phase") == phase
            and (ev.get("planning_round") == pr or (not ev.get("planning_round") and not pr)))


def _rnorm(r: Any) -> Optional[int]:
    """Normalize a round to a hashable key: a 1-based planning round, else None (execution/
    preliminary_vote/summary carry no meaningful round)."""
    return r if isinstance(r, int) and r > 0 else None


def _turn_lookup(run_dir: Path):
    """Map a judge turn's identity (agent, phase, round) -> (agent_turns 1-based index, full
    public message).

    CRITICAL: the judge's ``turn_index`` is its OWN counter over judged (planning) turns and does
    NOT line up with agent_turns.json, which interleaves planning / preliminary_vote / execution /
    summary. So we key by identity, which is unique per turn (one planning turn per agent+round,
    one execution/summary per agent). The public message is the turn's response plus its tool
    actions (post_message text verbatim; other tools as ``[tool] args``). No CoT."""
    try:
        agent_turns = json.loads((run_dir / "agent_turns.json").read_text())
    except Exception:
        return {}, {}
    try:
        tool_events = json.loads((run_dir / "tool_events.json").read_text())
    except Exception:
        tool_events = []
    msg_by_key: Dict[tuple, str] = {}
    idx_by_key: Dict[tuple, int] = {}
    for i, t in enumerate(agent_turns or [], start=1):
        agent, phase, pr = t.get("agent"), t.get("phase"), t.get("planning_round")
        key = (agent, phase, _rnorm(pr))
        if key in idx_by_key:  # unique per turn; first wins defensively
            continue
        parts = []
        resp = (t.get("response") or "").strip()
        if resp:
            parts.append(resp)
        for ev in tool_events or []:
            if not _tool_matches(ev, agent, phase, pr):
                continue
            s = _summarize_tool(ev)
            txt = s["text"] if s["name"] == "post_message" else f"[{s['name']}] {s['text']}"
            if txt.strip():
                parts.append(txt.strip())
        idx_by_key[key] = i
        msg_by_key[key] = "\n\n".join(parts).strip()
    return msg_by_key, idx_by_key


def assemble_transcript(run_dir: Path) -> List[Dict[str, Any]]:
    """Light ordered rollout: per turn -> agent, phase, round, public response, tool actions, CoT.
    Turn index is 1-based, matching the judge's turn_index (index into agent_turns.json)."""
    try:
        agent_turns = json.loads((run_dir / "agent_turns.json").read_text())
    except Exception:
        return []
    try:
        tool_events = json.loads((run_dir / "tool_events.json").read_text())
    except Exception:
        tool_events = []
    cot = _cot_map(run_dir)

    out: List[Dict[str, Any]] = []
    for i, t in enumerate(agent_turns or [], start=1):
        agent, phase = t.get("agent"), t.get("phase")
        pr = t.get("planning_round")
        rnum = pr if isinstance(pr, int) and pr > 0 else 0
        tools = [_summarize_tool(ev) for ev in (tool_events or [])
                 if _tool_matches(ev, agent, phase, pr)]
        out.append({
            "index": i, "agent": agent, "phase": phase, "round": pr,
            "response": (t.get("response") or "").strip(),
            "tools": tools,
            "cot": cot.get((agent, phase, rnum), ""),
        })
    return out


# --------------------------------------------------------------------------- server
def run_server(adapter: Adapter, default_port: int, roots: Optional[List[Path]] = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description=f"Evidence reader for {adapter.key} flagged turns.")
    ap.add_argument("root", type=Path, nargs="*", help="dir(s) to scan (default: standard set).")
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if args.root:
        resolved = [r.resolve() for r in args.root]
        extra_roots: List[Path] = []
    else:
        resolved = [r.resolve() for r in (roots or adapter.default_roots())]
        extra_roots = [r.resolve() for r in adapter.extra_roots]
    missing = [r for r in resolved if not r.exists()]
    for r in missing:
        print(f"no such path: {r}", file=sys.stderr)
    if missing:
        raise SystemExit(2)
    absent_extra = [r for r in extra_roots if not r.exists()]
    for r in absent_extra:
        print(f"# extra root absent, skipping: {r}", file=sys.stderr)
    extra_roots = [r for r in extra_roots if r.exists()]
    served = resolved + extra_roots  # transcript path-guard must allow the extra dir too

    print(f"# indexing {len(resolved)} dir(s)"
          + (f" (+{len(extra_roots)} optional)" if extra_roots else "") + "...", file=sys.stderr)
    index = build_index(adapter, resolved, extra_roots)
    print(f"# {index['n_flagged_turns']} flagged turns across {index['n_runs']} runs "
          f"({index['n_incomplete']} incomplete). serving on "
          f"http://{args.host}:{args.port}", file=sys.stderr)

    app = Flask(adapter.key + "_turns")

    @app.get("/")
    def home() -> Response:
        return Response(_PAGE.replace("__TITLE__", adapter.title), mimetype="text/html")

    @app.get("/index")
    def index_ep() -> Response:
        return jsonify(index)

    @app.get("/transcript")
    def transcript_ep() -> Response:
        raw = request.args.get("dir", "")
        target = Path(raw).resolve()
        if not any(target == r or r in target.parents for r in served):
            return jsonify({"error": "path not under a served root"}), 403
        if not target.is_dir():
            return jsonify({"error": "no such run dir"}), 404
        return jsonify({"turns": assemble_transcript(target)})

    app.run(host=args.host, port=args.port, debug=False)


# --------------------------------------------------------------------- HTML / JS / CSS
_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --fg:#e6e8ec; --muted:#9aa3b2;
          --accent:#6ea8fe; --hot:#e0a24a; }
  * { box-sizing: border-box; }
  body { margin:0; font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { padding:12px 16px; border-bottom:1px solid var(--line); background:var(--panel); }
  header h1 { margin:0 0 2px; font-size:15px; }
  header .sub { color:var(--muted); font-size:12px; }
  .wrap { display:flex; align-items:flex-start; }
  #side { width:260px; flex:0 0 260px; padding:12px; border-right:1px solid var(--line);
          background:var(--panel); height:calc(100vh - 52px); overflow:auto; position:sticky; top:0; }
  #main { flex:1 1 auto; height:calc(100vh - 52px); display:flex; flex-direction:column; overflow:hidden; }
  .colbar { padding:10px 14px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap;
            gap:6px; align-items:center; }
  .colbar .lbl { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  .chip { font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid var(--line);
          background:#222733; color:var(--fg); cursor:pointer; user-select:none; }
  .chip:hover { border-color:var(--accent); }
  .chip.on { background:var(--accent); color:#0b0d11; border-color:var(--accent); }
  .chip.l2 { border-style:dashed; }
  .stage { flex:1 1 auto; display:flex; overflow:hidden; }
  #cols { flex:1 1 auto; display:flex; gap:12px; padding:12px 14px; overflow:auto; }
  .col { flex:0 0 380px; display:flex; flex-direction:column; min-width:0; border:1px solid var(--line);
         border-radius:8px; background:var(--panel); max-height:100%; }
  .col > h2 { margin:0; padding:8px 10px; font-size:13px; border-bottom:1px solid var(--line);
              position:sticky; top:0; background:var(--panel); }
  .col > h2 .cnt { color:var(--muted); font-weight:400; }
  .col .cards { overflow:auto; padding:8px; display:flex; flex-direction:column; gap:8px; }
  .card { border:1px solid var(--line); border-radius:6px; padding:8px; background:#12151c; }
  .card.active { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
  .card .hd { font-size:11px; color:var(--muted); margin-bottom:4px; }
  .card .hd b { color:var(--fg); }
  .card .sets { font-size:11px; color:var(--muted); margin-bottom:5px; font-variant-numeric:tabular-nums; }
  .badges { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:5px; }
  .badge { font-size:10px; padding:1px 5px; border-radius:4px; background:#232a36; color:var(--muted);
           border:1px solid var(--line); cursor:default; }
  .badge.l2 { color:var(--accent); }
  .msg { white-space:pre-wrap; background:#0c0f14; border:1px solid var(--line); border-radius:5px;
         padding:7px; max-height:360px; overflow:auto; }
  .spanlbl { font-size:10px; text-transform:uppercase; letter-spacing:.03em; color:var(--muted);
             margin:6px 0 2px; }
  mark { background:#5a4a1e; color:#ffe9b0; border-radius:2px; padding:0 1px; }
  .span { background:#1d2330; border-left:3px solid var(--hot); padding:3px 7px; margin:3px 0;
          border-radius:0 4px 4px 0; white-space:pre-wrap; }
  .note { color:#cdd3dd; font-style:italic; margin-top:3px; font-size:12px; }
  .btns { margin-top:6px; display:flex; gap:6px; }
  .btns button { font-size:11px; background:#222733; color:var(--fg); border:1px solid var(--line);
                 border-radius:5px; padding:3px 8px; cursor:pointer; }
  .btns button:hover { border-color:var(--accent); }
  .cot { margin-top:6px; white-space:pre-wrap; background:#0c0f14; border:1px solid var(--line);
         border-radius:5px; padding:7px; font-size:12px; color:#c8cfda; max-height:340px; overflow:auto; }
  .loadmore { margin:4px 2px 2px; }
  #tx { flex:0 0 46%; max-width:46%; border-left:1px solid var(--line); background:var(--panel);
        display:none; flex-direction:column; }
  #tx .txh { padding:8px 12px; border-bottom:1px solid var(--line); display:flex; gap:8px;
             justify-content:space-between; align-items:center; }
  #tx .txbody { overflow:auto; padding:8px 12px; }
  #tx .txctl { display:flex; gap:10px; align-items:center; }
  .switch { display:inline-flex; gap:6px; align-items:center; font-size:12px; color:var(--muted);
            cursor:pointer; user-select:none; }
  .switch input { appearance:none; -webkit-appearance:none; width:30px; height:16px; border-radius:999px;
                  background:#2a2f3a; position:relative; cursor:pointer; outline:none; transition:background .15s; }
  .switch input:checked { background:var(--accent); }
  .switch input::after { content:""; position:absolute; top:2px; left:2px; width:12px; height:12px;
                         border-radius:50%; background:#e6e8ec; transition:left .15s; }
  .switch input:checked::after { left:16px; }
  .tt { border-bottom:1px solid var(--line); padding:8px 0; }
  .tt.hl { background:rgba(110,168,254,.10); border-radius:6px; padding:8px; }
  .tt .tm { font-size:11px; color:var(--muted); margin-bottom:3px; }
  .tt .resp { white-space:pre-wrap; }
  .tt .tool { border-left:3px solid var(--accent); padding:2px 7px; margin:3px 0; background:#1d2330;
              border-radius:0 4px 4px 0; white-space:pre-wrap; }
  .tt .tool .tn { color:var(--accent); font-size:11px; }
  .tt .ttcot { white-space:pre-wrap; color:#9aa3b2; font-size:11px; margin-top:3px;
               border-top:1px dashed var(--line); padding-top:3px; }
  .empty { color:var(--muted); padding:16px; }
  .vals { display:flex; flex-direction:column; gap:2px; max-height:150px; overflow:auto; }
  .vals label { display:flex; gap:6px; align-items:center; cursor:pointer; }
  .dim { margin-bottom:12px; border:1px solid var(--line); border-radius:8px; padding:7px 9px; }
  .dim h3 { margin:0 0 5px; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  .miniBtns { display:flex; gap:6px; margin-top:5px; }
  .miniBtns button, #side .topctl button { font-size:11px; background:#222733; color:var(--fg);
       border:1px solid var(--line); border-radius:5px; padding:2px 7px; cursor:pointer; }
  code { background:#1d2330; padding:1px 4px; border-radius:3px; }
</style>
</head>
<body>
<header style="display:flex;justify-content:space-between;align-items:center;gap:16px">
  <div><h1 id="pt"></h1><div class="sub" id="rl"></div></div>
  <label class="switch" id="extraWrap" style="display:none;color:var(--fg)">
    <input type="checkbox" id="extraTog"><span id="extraLbl"></span></label></header>
<div class="wrap">
  <aside id="side"></aside>
  <main id="main">
    <div class="colbar" id="colbar"></div>
    <div class="stage">
      <div id="cols"><div class="empty">Loading…</div></div>
      <div id="tx"><div class="txh"><strong id="txt"></strong>
        <span class="txctl">
          <label class="switch"><input type="checkbox" id="txCotToggle"> show CoT</label>
          <button id="txClose">close</button></span></div>
        <div class="txbody" id="txb"></div></div>
    </div>
  </main>
</div>
<script>
const $ = (s, r=document) => r.querySelector(s);
let DATA = null;
const CAP0 = 50;                       // cards shown per column before "load more"
const txCache = new Map();             // run_dir -> Promise<[turns]>
const state = {
  selected: {},                        // dim -> Set of chosen values (filter only)
  includeIncomplete: false,
  includeExtra: false,                 // fold in the optional extra roots (top-right switch)
  cols: [],                            // ordered selected phenomena -> columns
  caps: {},                            // phenomenon -> current card cap
  open: null,                          // {dir, index, hiSpans} of the transcript pane
  cotOpen: new Set(),                  // card ids with CoT expanded
  txCot: false,                        // show CoT in the "show more" full-conversation pane
};

function esc(s){ return String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
// Escape `text`, then wrap every occurrence of each span in <mark>. Longest spans first so a
// span that contains a shorter one wins. Returns the html and any spans not found in `text`.
function highlight(text, spans){
  let html = esc(text||""); const missing=[];
  const uniq=[...new Set((spans||[]).filter(Boolean))].sort((a,b)=>b.length-a.length);
  for(const s of uniq){
    if((text||"").includes(s)){ const se=esc(s); html = html.split(se).join(""+se+""); }
    else missing.push(s);
  }
  html = html.split("").join("<mark>").split("").join("</mark>");
  return {html, missing};
}

// ---- load -----------------------------------------------------------------
fetch("index").then(r=>r.json()).then(d=>{
  DATA = d;
  for (const dim of DATA.dimensions) state.selected[dim] = new Set(DATA.dim_values[dim]);
  $("#pt").textContent = DATA.title;
  const roots = DATA.roots.length===1 ? DATA.roots[0] : DATA.roots.length+" dirs";
  $("#rl").textContent = `${roots}  ·  ${DATA.n_flagged_turns} flagged turns · ${DATA.n_runs} runs`
      + (DATA.n_incomplete?`  ·  ${DATA.n_incomplete} incomplete`:"");
  $("#rl").title = DATA.roots.join("\n");
  if(DATA.has_extra){
    $("#extraLbl").textContent = DATA.extra_label || "include extra data";
    $("#extraWrap").style.display = "inline-flex";
    $("#extraTog").checked = state.includeExtra;
    $("#extraTog").addEventListener("change", e=>{ state.includeExtra=e.target.checked; renderCols(); });
  }
  renderSidebar(); renderColbar(); renderCols();
});

// ---- sidebar (filters only) ----------------------------------------------
function renderSidebar(){
  const side = $("#side");
  side.innerHTML = `<div class="topctl" style="margin-bottom:10px">
     <label style="font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center">
       <input type="checkbox" id="inc"> include incomplete runs</label></div>`;
  for (const dim of DATA.dimensions){
    const vals = DATA.dim_values[dim];
    const box = document.createElement("div"); box.className="dim";
    box.innerHTML = `<h3>${esc(DATA.dim_labels[dim]||dim)}</h3>`;
    const vd = document.createElement("div"); vd.className="vals";
    for (const v of vals){
      const n = DATA.turns.filter(t=>String(t.settings[dim])===v).length;
      const lab = document.createElement("label");
      lab.innerHTML = `<input type="checkbox" data-dim="${dim}" value="${esc(v)}"
        ${state.selected[dim].has(v)?"checked":""}>
        <span>${v==="None"?"—":esc(v)} <span style="color:var(--muted)">(${n})</span></span>`;
      vd.appendChild(lab);
    }
    box.appendChild(vd);
    const mini=document.createElement("div"); mini.className="miniBtns";
    mini.innerHTML=`<button data-all="${dim}">all</button><button data-none="${dim}">none</button>`;
    box.appendChild(mini); side.appendChild(box);
    vd.querySelectorAll("input").forEach(cb=>cb.addEventListener("change",e=>{
      const set=state.selected[dim];
      if(e.target.checked) set.add(e.target.value); else set.delete(e.target.value);
      renderCols();
    }));
    mini.querySelector(`[data-all="${dim}"]`).addEventListener("click",()=>{
      state.selected[dim]=new Set(vals); renderSidebar(); renderCols(); });
    mini.querySelector(`[data-none="${dim}"]`).addEventListener("click",()=>{
      state.selected[dim]=new Set(); renderSidebar(); renderCols(); });
  }
  $("#inc").checked = state.includeIncomplete;
  $("#inc").addEventListener("change",e=>{ state.includeIncomplete=e.target.checked; renderCols(); });
}

// ---- column selector chips (phenomena) -----------------------------------
function renderColbar(){
  const bar = $("#colbar");
  bar.innerHTML = `<span class="lbl">columns:</span>`;
  for (const p of DATA.phenomena){
    const chip=document.createElement("span");
    const isL2 = /^L2 /.test(p);
    chip.className="chip"+(state.cols.includes(p)?" on":"")+(isL2?" l2":"");
    const total = DATA.turns.filter(t=>t.phenomena.some(x=>x.name===p)).length;
    chip.innerHTML = `${esc(DATA.phen_codes[p]||"?")} · ${esc(p)}
      <span style="opacity:.7">(${total})</span>`;
    chip.title = p;
    chip.addEventListener("click",()=>{
      const i=state.cols.indexOf(p);
      if(i>=0) state.cols.splice(i,1); else state.cols.push(p);
      renderColbar(); renderCols();
    });
    bar.appendChild(chip);
  }
}

// ---- filtering + columns --------------------------------------------------
function inScope(t){
  if(!state.includeExtra && t.extra) return false;
  if(!state.includeIncomplete && !t.completed) return false;
  return DATA.dimensions.every(d=>state.selected[d].has(String(t.settings[d])));
}
function turnsFor(phen, pool){
  return pool.filter(t=>t.phenomena.some(x=>x.name===phen))
             .sort((a,b)=> a.run_id<b.run_id?-1:a.run_id>b.run_id?1:(a.turn_index-b.turn_index));
}

function renderCols(){
  const host=$("#cols");
  if(!state.cols.length){ host.innerHTML=`<div class="empty">Pick one or more phenomena above to
    show their flagged turns as columns.</div>`; return; }
  const pool = DATA.turns.filter(inScope);
  host.innerHTML="";
  for(const phen of state.cols){
    const list=turnsFor(phen, pool);
    const cap=state.caps[phen]||CAP0;
    const col=document.createElement("div"); col.className="col";
    const shown=list.slice(0,cap);
    col.innerHTML=`<h2>${esc(DATA.phen_codes[phen]||"")} · ${esc(phen)}
       <span class="cnt">— ${list.length} turn${list.length===1?"":"s"}</span></h2>`;
    const cards=document.createElement("div"); cards.className="cards";
    if(!list.length) cards.innerHTML=`<div class="empty">none in the current filter</div>`;
    for(const t of shown) cards.appendChild(renderCard(t, phen));
    if(list.length>cap){
      const b=document.createElement("button"); b.className="loadmore";
      b.textContent=`load more (${list.length-cap} more)`;
      b.addEventListener("click",()=>{ state.caps[phen]=cap+CAP0; renderCols(); });
      cards.appendChild(b);
    }
    col.appendChild(cards); host.appendChild(col);
  }
  // refresh active-card highlight after a re-render
  markActive();
}

function renderCard(t, colPhen){
  const card=document.createElement("div"); card.className="card"; card.dataset.cardid=t.id+"@"+colPhen;
  card.dataset.dir=t.run_dir; card.dataset.index=t.turn_index;
  const sets = DATA.dimensions.map(d=>`${DATA.dim_labels[d]||d}=${esc(t.settings[d]==="None"?"—":t.settings[d])}`).join(" · ");
  const others = t.phenomena.filter(x=>x.name!==colPhen);
  const badges = others.map(x=>`<span class="badge${/^L2 /.test(x.name)?" l2":""}" title="${esc(x.name)}">${esc(DATA.phen_codes[x.name]||"?")}</span>`).join("");
  const mine = t.phenomena.find(x=>x.name===colPhen) || {spans:[],note:""};
  const note = mine.note?`<div class="note">${esc(mine.note)}</div>`:"";
  // Show the whole assistant message with the flagged spans highlighted in place; spans that
  // aren't in the public message came from the CoT — list them, revealed in full via "CoT".
  let bodyHtml;
  if(t.msg){
    const {html, missing} = highlight(t.msg, mine.spans);
    bodyHtml = `<div class="msg">${html}</div>`
      + (missing.length ? `<div class="spanlbl">also flagged in reasoning (open CoT):</div>`
          + missing.map(s=>`<div class="span">${esc(s)}</div>`).join("") : "");
  } else if((mine.spans||[]).length){
    bodyHtml = `<div class="spanlbl">flagged in reasoning (open CoT):</div>`
      + mine.spans.map(s=>`<div class="span">${esc(s)}</div>`).join("");
  } else {
    bodyHtml = `<div class="empty" style="padding:4px 0">no recorded content</div>`;
  }
  card.innerHTML =
    `<div class="hd">turn ${t.turn_index} · <b>${esc(t.agent||"?")}</b> · ${esc(t.phase||"?")}`
    + (t.round!=null?` · r${esc(t.round)}`:"") + (t.completed?"":" · <i>incomplete</i>") + `</div>`
    + `<div class="sets"><code>${esc(t.run_id)}</code></div>`
    + `<div class="sets">${sets}</div>`
    + (badges?`<div class="badges">${badges}</div>`:"")
    + bodyHtml + note
    + `<div class="btns"><button class="cotB">CoT</button><button class="moreB">show more ▸</button></div>`;
  card.querySelector(".cotB").addEventListener("click",()=>toggleCot(card, t));
  card.querySelector(".moreB").addEventListener("click",()=>openTx(t));
  if(state.cotOpen.has(card.dataset.cardid)) toggleCot(card, t, true);
  return card;
}

// ---- transcript (lazy) ----------------------------------------------------
function loadTx(dir){
  if(!txCache.has(dir))
    txCache.set(dir, fetch("transcript?dir="+encodeURIComponent(dir)).then(r=>r.json()).then(d=>d.turns||[]));
  return txCache.get(dir);
}
function toggleCot(card, t, force){
  const id=card.dataset.cardid;
  let box=card.querySelector(".cot");
  if(box && !force){ box.remove(); state.cotOpen.delete(id); return; }
  if(box) return;
  state.cotOpen.add(id);
  box=document.createElement("div"); box.className="cot"; box.textContent="loading CoT…";
  card.appendChild(box);
  const allSpans = t.phenomena.flatMap(p=>p.spans||[]);
  loadTx(card.dataset.dir).then(turns=>{
    const tt=turns.find(x=>x.index===t.tx_index);   // tx_index = agent_turns position, not judge #
    if(tt && tt.cot){ box.innerHTML = highlight(tt.cot, allSpans).html; }
    else box.textContent = "(no CoT recorded for this turn)";
  });
}
function openTx(t){
  // Highlight/scroll by tx_index (agent_turns position), but label with the judge's turn number.
  state.open={dir:t.run_dir, index:t.tx_index, run_id:t.run_id,
              hiSpans:t.phenomena.flatMap(p=>p.spans||[])};
  $("#tx").style.display="flex";
  $("#txt").textContent = `${t.run_id}  ·  turn ${t.turn_index}`;
  $("#txb").innerHTML=`<div class="empty">loading transcript…</div>`;
  markActive();
  renderTxBody(true);
}
// Render (or re-render) the full-conversation pane from the cached transcript — no refetch, so
// toggling "show CoT" is instant. `scroll` only on first open, not on toggle.
function renderTxBody(scroll){
  if(!state.open) return;
  const {dir, index, hiSpans} = state.open;
  const body=$("#txb");
  loadTx(dir).then(turns=>{
    if(!state.open || state.open.dir!==dir) return;
    body.innerHTML = turns.map(x=>renderTxTurn(x, index, hiSpans)).join("") ||
      `<div class="empty">no transcript found for this run</div>`;
    if(scroll){ const hl=body.querySelector(".tt.hl"); if(hl) hl.scrollIntoView({block:"center"}); }
  });
}
function renderTxTurn(x, hi, hiSpans){
  const isHi = x.index===hi;
  const mk = (s)=> isHi ? highlight(s, hiSpans).html : esc(s);
  // CoT first (above the sent message), and only when the pane's toggle is on.
  const cot = (state.txCot && x.cot)?`<div class="ttcot">${mk(x.cot)}</div>`:"";
  const resp = x.response?`<div class="resp">${mk(x.response)}</div>`:"";
  const tools=(x.tools||[]).map(tl=>`<div class="tool"><span class="tn">${esc(tl.name)}</span> ${mk(tl.text)}`
     + (tl.result?` <span style="color:var(--muted)">→ ${esc(tl.result)}</span>`:"") + `</div>`).join("");
  return `<div class="tt${isHi?" hl":""}"><div class="tm">turn ${x.index} · <b>${esc(x.agent||"?")}</b>`
    + ` · ${esc(x.phase||"?")}${x.round!=null?" · r"+esc(x.round):""}</div>${cot}${resp}${tools}</div>`;
}
function markActive(){
  document.querySelectorAll(".card").forEach(c=>{
    const on = state.open && c.dataset.dir===state.open.dir
               && String(c.dataset.index)===String(state.open.index);
    c.classList.toggle("active", !!on);
  });
}
$("#txCotToggle").addEventListener("change",e=>{ state.txCot=e.target.checked; renderTxBody(false); });
$("#txClose").addEventListener("click",()=>{ state.open=null; $("#tx").style.display="none"; markActive(); });
</script>
</body>
</html>
"""
