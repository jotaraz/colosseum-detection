"""agent5 board viewer: a time-aligned multi-column grid over run.json.

Columns are conversations (channels + DMs) and one turn lane per assistant,
all toggleable. Rows are world-clock moments (ordinal, not proportional), so
everything an agent did in one turn — a DM, a channel post, a silent think —
lands on the same row across columns. Assistant messages carry a CoT expander
showing the exact reasoning step that produced them; hovering cross-highlights
cause and effect (wake edges, reads, posts). View state lives in the URL hash.

Run: python -m experiments.agent5.viewer5 <run_dir_or_run.json> [...]
     (writes board.html next to each run.json)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MAX_DETAIL = 700          # chars of a tool call's args/result kept in the page
GAP_SPACER_S = 240        # world-clock gap that earns a "+N min" spacer row
ROW_MERGE_S = 1           # atoms within the same rounded second share a row
POST_GROUP_S = 15         # same-turn-and-step posts further apart than this split rows


def _naive_utc(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def _epoch_offset(r: Dict[str, Any]) -> float:
    """Seconds to add to a naive world-clock ISO to land on message-ts epoch."""
    for t in r["turns"]:
        for c in t.get("tool_calls") or []:
            res = c.get("result") or {}
            if c.get("tool") == "chat_postMessage" and res.get("ts") and c.get("clock"):
                return float(res["ts"]) - _naive_utc(c["clock"])
    for t in r["turns"]:
        w = t.get("wake") or {}
        if w.get("ts") and t.get("clock"):
            return float(w["ts"]) - _naive_utc(t["clock"])
    return 0.0


def _hms(epoch: float, off: float) -> str:
    return datetime.fromtimestamp(epoch - off, tz=timezone.utc).strftime("%H:%M:%S")


def _clip(obj: Any, n: int = MAX_DETAIL) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def _conv_label_map(r: Dict[str, Any]) -> Dict[str, str]:
    return {m["conv_id"]: m["label"] for m in r["messages"]}


def _call_view(c: Dict[str, Any], labels: Dict[str, str]) -> Dict[str, Any]:
    tool = c.get("tool", "?")
    args = c.get("args") or {}
    res = c.get("result") or {}
    label = labels.get(str(args.get("channel")), str(args.get("channel") or ""))
    v: Dict[str, Any] = {"tool": tool, "step": c.get("step") or 0,
                         "detail": f"args: {_clip(args)}\nresult: {_clip(res)}"}
    if tool in ("conversations_history", "conversations_replies"):
        msgs = res.get("messages") or []
        v["line"] = f"read {label} ({len(msgs)} msgs)"
        v["read_ts"] = [str(m.get("ts")) for m in msgs if m.get("ts")][:300]
    elif tool == "chat_postMessage":
        v["line"] = f"posted → {label}"
        if res.get("ts"):
            v["post_ts"] = str(res["ts"])
    elif tool == "chat_delete":
        v["line"] = f"deleted a message in {label}"
    elif tool == "notify_user":
        v["line"] = "notified principal"
    elif tool == "board_assign":
        v["line"] = f"board_assign {json.dumps(args, ensure_ascii=False)}"
    elif tool == "reactions_add":
        v["line"] = f"reacted :{args.get('name', '?')}: in {label}"
    else:
        v["line"] = f"{tool} {_clip(args, 80)}"
    return v


def _turn_views(r: Dict[str, Any], off: float) -> List[Dict[str, Any]]:
    labels = _conv_label_map(r)
    out = []
    for t in r["turns"]:
        calls_by_step: Dict[int, list] = defaultdict(list)
        reads, read_ts, posts = [], [], []
        for c in t.get("tool_calls") or []:
            cv = _call_view(c, labels)
            calls_by_step[cv["step"]].append(cv)
            if "read_ts" in cv:
                reads.append(cv["line"])
                read_ts += cv.pop("read_ts")
            if "post_ts" in cv:
                posts.append(cv["post_ts"])
        steps = [{"step": sd.get("step"), "reasoning": sd.get("reasoning") or "",
                  "text": sd.get("text") or "", "calls": calls_by_step.pop(sd.get("step"), [])}
                 for sd in (t.get("steps_detail") or [])]
        for step, calls in sorted(calls_by_step.items()):  # calls with no step record
            steps.append({"step": step, "reasoning": "", "text": "", "calls": calls})
        wake = t.get("wake") or {}
        if t["kind"] == "wake" and wake.get("label"):
            src = f"woke ← {wake['label']}" + (f" ({wake['from']})" if wake.get("from") else "")
        elif t["kind"] == "added":
            src = f"added to {wake.get('label', '?')}"
        else:
            src = t["kind"] + " from principal"
        end_iso = t.get("clock_end") or t["clock"]
        out.append({
            "i": t["i"], "agent": t["agent"], "kind": t["kind"],
            "time": _hms(_naive_utc(t["clock"]) + off, off),
            "end": _hms(_naive_utc(end_iso) + off, off),
            "t1": int(_naive_utc(end_iso) + off), "src": src,
            "ask": (t.get("message_in") or "") if t["kind"] in ("ask", "debrief") else "",
            "steps": steps, "reads": reads, "read_ts": sorted(set(read_ts)),
            "posts": posts, "report": t.get("text_to_principal") or "",
            "wake_ts": str(wake.get("ts")) if wake.get("ts") else "",
            "cost": (t.get("usage") or {}).get("cost"),
        })
    return out


def build_data(r: Dict[str, Any]) -> Dict[str, Any]:
    off = _epoch_offset(r)
    start = _naive_utc(r["clock_start"]) + off
    roster = list((r.get("system_prompts") or {}).keys()) or sorted({t["agent"] for t in r["turns"]})
    turns = _turn_views(r, off)

    post_map: Dict[str, Dict[str, int]] = {}    # msg ts -> producing turn/step
    for t, raw in zip(turns, r["turns"]):
        for c in raw.get("tool_calls") or []:
            res = c.get("result") or {}
            if c.get("tool") == "chat_postMessage" and res.get("ts"):
                post_map[str(res["ts"])] = {"turn": t["i"], "step": c.get("step") or 0}
    woken: Dict[str, List[int]] = defaultdict(list)  # msg ts -> turns it woke
    for t in turns:
        if t["wake_ts"]:
            woken[t["wake_ts"]].append(t["i"])

    convs: Dict[str, Dict[str, Any]] = {}
    msgs = []
    backlog: Dict[str, list] = defaultdict(list)
    for m in r["messages"]:
        cid, ts = m["conv_id"], float(m["ts"])
        c = convs.setdefault(cid, {"id": cid, "label": m["label"], "type": m["type"],
                                   "total": 0, "run": 0})
        c["total"] += 1
        src = post_map.get(m["ts"])
        mv = {"ts": m["ts"], "conv": cid, "user": m["user"], "text": m["text"],
              "time": _hms(ts, off), "src": src, "wakes": woken.get(m["ts"], [])}
        if ts < start - 30 and not src:
            backlog[cid].append(mv)
        else:
            c["run"] += 1
            msgs.append(mv)

    def conv_key(c):
        is_sprint = c["type"] == "channel" and "sprint" in c["label"]
        return (0 if is_sprint else 1 if c["type"] == "channel" else 2, -c["run"], c["label"])
    conv_order = sorted(convs.values(), key=conv_key)
    # default columns (2026-09-03): the sprint channel, DMs between people (no bot member),
    # and the agents' turns. Social channels and calendar-bot DMs stay one chip-click away.
    for c in conv_order:
        lab = c["label"]
        is_sprint = c["type"] == "channel" and "sprint" in lab
        is_people_dm = c["type"] != "channel" and "bot" not in lab.lower()
        c["default_on"] = bool(c["run"] > 0 and (is_sprint or is_people_dm))

    # rows: one per rounded world-second. Turn cells sit at turn start; messages sit
    # at their own post time, except posts from the same (turn, step) share the row of
    # the group's earliest post — "sent together" stays same-height. A same-step group
    # is split on gaps > POST_GROUP_S: the step mapper leaves some calls at step 0, and
    # merging posts minutes apart would break channel chronology.
    grouped: Dict[tuple, list] = defaultdict(list)
    for i, mv in enumerate(msgs):
        if mv["src"]:
            grouped[(mv["src"]["turn"], mv["src"]["step"])].append(i)
    row_ts = {i: float(mv["ts"]) for i, mv in enumerate(msgs)}
    for members in grouped.values():
        members.sort(key=lambda i: float(msgs[i]["ts"]))
        anchor = float(msgs[members[0]]["ts"])
        for i in members:
            ts = float(msgs[i]["ts"])
            if ts - anchor > POST_GROUP_S:
                anchor = ts
            row_ts[i] = anchor
    atoms: Dict[int, Dict[str, Any]] = {}
    for t, raw in zip(turns, r["turns"]):
        key = int((_naive_utc(raw["clock"]) + off) // ROW_MERGE_S)
        t["t0"] = key
        atoms.setdefault(key, {"epoch": key, "turns": [], "msgs": []})["turns"].append(t["i"])
    for i in range(len(msgs)):
        key = int(row_ts[i] // ROW_MERGE_S)
        atoms.setdefault(key, {"epoch": key, "turns": [], "msgs": []})["msgs"].append(i)
    rows = [atoms[k] | {"time": _hms(k, off)} for k in sorted(atoms)]

    markers = []
    for name, iso in (("kickoff", r.get("kickoff")), ("deadline", r.get("deadline"))):
        if iso:
            ep = _naive_utc(iso) + off
            markers.append({"epoch": int(ep), "label": name, "time": _hms(ep, off)})

    cfg = r.get("config") or {}
    # ids → names overlay (as in agent4/viewer.py's run.html): users from the fixture the
    # run declares, conversation ids from the run's own message log.
    id_names: Dict[str, str] = {}
    try:
        fx_path = Path(__file__).resolve().parents[2] / str(cfg.get("fixture") or "")
        fx = json.loads(fx_path.read_text()) if cfg.get("fixture") else {}
        id_names.update({u["id"]: u["name"] for u in fx.get("users") or [] if u.get("id")})
    except Exception:
        pass
    for m in r.get("messages") or []:
        if m.get("conv_id") and m.get("label"):
            id_names.setdefault(str(m["conv_id"]), str(m["label"]))
    return {
        "id_names": id_names,
        "meta": {"run_id": cfg.get("run_id") or cfg.get("name", ""), "model": cfg.get("model", ""),
                 "outcome": r.get("outcome", ""), "score": r.get("score"),
                 "assignments": r.get("assignments"), "started": r.get("clock_start", ""),
                 "kickoff": r.get("kickoff"), "deadline": r.get("deadline")},
        "roster": roster, "convs": conv_order, "turns": turns, "msgs": msgs,
        "backlog": {k: v for k, v in backlog.items()}, "rows": rows, "markers": markers,
        "gap_s": GAP_SPACER_S,
    }


def render(run_path: str | Path, out_name: str = "board.html") -> Path:
    run_path = Path(run_path)
    if run_path.is_dir():
        run_path = run_path / "run.json"
    data = build_data(json.loads(run_path.read_text()))
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = _TEMPLATE.replace("__TITLE__", data["meta"]["run_id"] or run_path.parent.name)
    html = html.replace("__DATA__", blob)
    out = run_path.parent / out_name
    out.write_text(html)
    return out


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · board</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#1c2330; --dim:#68738a; --line:#dfe3ea;
  --stripe:rgba(15,40,80,.025); --hl:#ffd54d33; --pin:#ffb30055;
  --accent:#2563eb; --marker:#c2410c; --spacer:#94a3b8;
  --Nadia:#2563eb; --Marcus:#059669; --Matthieu:#059669; --Tomas:#d97706; --Priya:#db2777; --Helena:#7c3aed;
  --Rafael:#d97706;
  --human:#8a93a6;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#12161d; --panel:#1a2029; --ink:#dfe5ee; --dim:#8b96aa; --line:#2a3342;
  --stripe:rgba(160,190,255,.04); --hl:#ffd54d22; --pin:#ffb30044;
  --Nadia:#5c8dff; --Marcus:#2fbf8f; --Matthieu:#2fbf8f; --Tomas:#e8a13c; --Priya:#ef6aa8; --Helena:#a07af0;
  --Rafael:#e8a13c;
}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);overflow:hidden;
  display:flex;flex-direction:column;
  font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
#top{flex:none;background:var(--bg);border-bottom:1px solid var(--line);
  padding:8px 12px}
#top h1{font-size:14px;margin:0 0 2px;font-weight:650}
#top .meta{color:var(--dim);font-size:12px;margin-bottom:6px}
#chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
#chips .grp{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.06em;
  margin:0 2px 0 8px}
#chips .grp:first-child{margin-left:0}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--dim);border-radius:20px;
  padding:2px 9px;font-size:11.5px;cursor:pointer;user-select:none;white-space:nowrap}
.chip.on{color:var(--ink);border-color:currentColor}
.chip .n{opacity:.55;font-size:10px;margin-left:3px}
#wrap{flex:1;overflow:auto}
#grid{display:grid;align-items:start;min-width:min-content;padding-bottom:40vh}
.stripe{background:var(--stripe);align-self:stretch;height:100%}
.tbar{width:3px;height:100%;align-self:stretch;justify-self:start;margin-left:1px;
  border-radius:2px;opacity:.5;cursor:pointer}
.tbar:hover{opacity:1;width:5px}
.colhead{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line);
  padding:6px 8px;font-weight:650;font-size:12px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.rule{position:sticky;left:0;z-index:10;background:var(--bg);color:var(--dim);font-size:10.5px;
  padding:8px 6px 0 8px;white-space:nowrap;font-variant-numeric:tabular-nums}
.rule.colhead{z-index:25}
.cell{padding:4px 6px;min-width:0}
.spacer{grid-column:1/-1;color:var(--spacer);font-size:10.5px;text-align:center;
  border-top:1px dashed var(--line);padding:1px 0;margin-top:4px}
.marker{grid-column:1/-1;color:var(--marker);font-size:11px;font-weight:650;
  border-top:2px solid var(--marker);padding:2px 8px;margin-top:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:5px 8px;
  margin-bottom:4px;border-left:3px solid var(--human);overflow-wrap:break-word}
.card.hl{background:var(--hl)}
.card.pin{outline:2px solid var(--pin);background:var(--pin)}
.card .hd{display:flex;gap:6px;align-items:baseline;font-size:11px;color:var(--dim)}
.card .hd b{color:var(--ink);font-size:12px}
.card .hd .t{margin-left:auto;font-variant-numeric:tabular-nums}
.msg .body{white-space:pre-wrap;margin-top:2px}
.msg.scripted .body{color:var(--dim)}
.turnc{border-left-width:3px}
.turnc .src{font-size:11px;color:var(--dim);margin-top:1px}
.turnc .rline{font-size:11px;color:var(--dim)}
.turnc .peek{margin-top:2px;font-style:italic;color:var(--dim);display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.turnc.silent{opacity:.75}
.report{margin-top:3px;font-size:12px;border-top:1px dashed var(--line);padding-top:3px}
.report b{color:var(--accent);font-weight:600}
details.cot{margin-top:3px;font-size:12px}
details.cot>summary{cursor:pointer;color:var(--accent);font-size:11px;user-select:none;
  list-style:none;display:inline-block;border:1px solid var(--line);border-radius:10px;
  padding:0 8px}
details.cot[open]>summary{background:var(--accent);color:#fff;border-color:var(--accent)}
.cotbody{max-height:65vh;overflow:auto;overscroll-behavior:contain}
.step{border-left:2px solid var(--line);margin:5px 0 5px 2px;padding-left:7px}
.step .sh{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.step.post{border-left-color:var(--accent)}
.step.post .sh{color:var(--accent)}
.reason{white-space:pre-wrap;color:var(--ink);opacity:.9}
.stext{white-space:pre-wrap;margin-top:3px;padding:3px 6px;background:var(--stripe);
  border-radius:6px}
details.tc{margin:2px 0}
details.tc>summary{cursor:pointer;font-size:11px;color:var(--dim);list-style:none}
details.tc>summary:before{content:"⚙ "}
details.tc pre{white-space:pre-wrap;font-size:10.5px;margin:2px 0;color:var(--dim);
  max-height:220px;overflow:auto}
.askfull{white-space:pre-wrap;font-size:12px;color:var(--dim);margin-top:3px}
.jump{color:var(--accent);cursor:pointer;font-size:11px}
.bkcell{font-size:11.5px}
.bkcell>details>summary{cursor:pointer;color:var(--dim)}
.bkcell .card{opacity:.8}
.wline{font-size:10.5px;color:var(--dim);margin-top:2px}
.slackid.named{background:rgba(90,140,255,.14);border-bottom:1px dotted rgba(90,140,255,.8);border-radius:3px;padding:0 2px}
</style></head><body>
<div id="top">
  <h1 id="title"></h1><div class="meta" id="meta"></div>
  <div id="chips"></div>
  <div class="meta" style="margin-top:6px"><button id="btn-names" onclick="toggleNames()" title="Replace raw Slack ids with ⟨names⟩ — a viewer overlay, not what was written">ids → names</button></div>
</div>
<div id="wrap"><div id="grid"></div></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const AGENTS = new Set(D.roster);
const colDefs = [];             // {id,label,kind:'conv'|'agent',on}
for (const c of D.convs) colDefs.push({id:"c:"+c.id, label:c.label, kind:"conv",
  n:c.run, on:("default_on" in c ? c.default_on : c.run>0)});
for (const a of D.roster) colDefs.push({id:"a:"+a, label:a+" · turns", kind:"agent",
  n:D.turns.filter(t=>t.agent===a).length, on:true});
const colById = Object.fromEntries(colDefs.map(c=>[c.id,c]));

function agentColor(name){
  return getComputedStyle(document.documentElement).getPropertyValue("--"+name) ?
    `var(--${name}, var(--human))` : "var(--human)";
}
const esc = s => (s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* ---------- hash state ---------- */
function readHash(){
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.has("cols")){
    const on = new Set(p.get("cols").split("|").filter(Boolean));
    for (const c of colDefs) c.on = on.has(c.id);
  }
  return new Set((p.get("open")||"").split("|").filter(Boolean));
}
function writeHash(){
  const cols = colDefs.filter(c=>c.on).map(c=>c.id).join("|");
  const open = [...document.querySelectorAll("details.cot[open]")]
    .map(d=>d.dataset.oid).filter(Boolean).join("|");
  const p = new URLSearchParams();
  p.set("cols", cols); if (open) p.set("open", open);
  history.replaceState(null,"","#"+p.toString());
}
const openSet = readHash();

/* ---------- header ---------- */
document.getElementById("title").textContent = D.meta.run_id || "agent5 run";
document.title = (D.meta.run_id||"agent5") + " · board";
{
  const m = D.meta, bits=[];
  if (m.model) bits.push(m.model);
  if (m.outcome) bits.push("outcome: "+m.outcome);
  if (m.score) bits.push("assignments "+(m.score.complete?"complete":"incomplete")+
    (m.score.valid===false?" (invalid)":m.score.valid?" (valid)":""));
  if (m.assignments) bits.push(Object.entries(m.assignments)
    .map(([k,v])=>`${k}→${v??"—"}`).join("  "));
  document.getElementById("meta").textContent = bits.join("   ·   ");
}
function renderChips(){
  const el = document.getElementById("chips"); el.innerHTML="";
  const groups = [["Channels", c=>c.kind==="conv" && !c.label.startsWith("dm:")],
                  ["DMs",      c=>c.kind==="conv" && c.label.startsWith("dm:")],
                  ["Agent turns", c=>c.kind==="agent"]];
  const mkChip = c => {
    const b = document.createElement("span");
    b.className = "chip"+(c.on?" on":""); b.dataset.col=c.id;
    const lbl = c.kind==="conv" && c.label.startsWith("dm:")
      ? c.label.slice(3).replace("+"," ⇄ ") : c.label;
    b.innerHTML = esc(lbl)+`<span class="n">${c.n}</span>`;
    if (c.kind==="agent") b.style.color = c.on ? agentColor(c.label.split(" ")[0]) : "";
    b.onclick = ()=>{ c.on=!c.on; renderChips(); renderGrid(); writeHash(); };
    return b;
  };
  for (const [name, f] of groups){
    const g = document.createElement("span"); g.className="grp"; g.textContent=name;
    el.appendChild(g);
    const all = colDefs.filter(f);
    const quiet = all.filter(c=>c.kind==="conv" && !c.n && !c.on);
    for (const c of all.filter(c=>!quiet.includes(c))) el.appendChild(mkChip(c));
    if (quiet.length){
      if (showQuiet[name]) for (const c of quiet) el.appendChild(mkChip(c));
      else {
        const b = document.createElement("span"); b.className="chip";
        b.textContent = `+${quiet.length} quiet`;
        b.onclick = ()=>{ showQuiet[name]=true; renderChips(); };
        el.appendChild(b);
      }
    }
  }
}
const showQuiet = {};

/* ---------- cell builders ---------- */
function stepHtml(t, s, flagStep){
  const isPost = s.calls.some(c=>c.post_ts) || s.step===flagStep;
  let h = `<div class="step${isPost?" post":""}"><div class="sh">step ${s.step}` +
          `${s.calls.some(c=>c.post_ts)?" · posts":""}</div>`;
  if (s.reasoning) h += `<div class="reason">${esc(s.reasoning)}</div>`;
  for (const c of s.calls)
    h += `<details class="tc"><summary>${esc(c.line)}</summary><pre>${esc(c.detail)}</pre></details>`;
  if (s.text) h += `<div class="stext">${esc(s.text)}</div>`;
  return h+"</div>";
}
function cotHtml(t, oid, flagStep){
  let h = `<details class="cot" data-oid="${oid}" ${openSet.has(oid)?"open":""}>`+
          `<summary>CoT · ${t.steps.length} step${t.steps.length!==1?"s":""}</summary>`+
          `<div class="cotbody">`;
  if (t.ask) h += `<div class="askfull"><b>principal:</b> ${esc(t.ask)}</div>`;
  for (const s of t.steps) h += stepHtml(t, s, flagStep);
  if (t.report) h += `<div class="report"><b>→ principal:</b> ${esc(t.report)}</div>`;
  h += "</div></details>";
  return h;
}
function turnCard(t){
  const silent = !t.posts.length && !t.reads.length && t.steps.length<=1 &&
                 !["ask","debrief"].includes(t.kind);
  const relMsgs = [t.wake_ts, ...t.posts].filter(Boolean).join(" ");
  const peek = t.steps.length && t.steps[0].reasoning
    ? `<div class="peek">${esc(t.steps[0].reasoning.slice(0,220))}</div>` : "";
  const rline = t.reads.length ? `<div class="rline">${esc(t.reads.join(" · "))}</div>` : "";
  const rep = t.report && !silent
    ? `<div class="report"><b>→ principal:</b> ${esc(t.report.slice(0,200))}</div>` : "";
  return `<div class="card turnc${silent?" silent":""}" id="turn-${t.i}" data-tid="${t.i}"
    data-msgs="${relMsgs}" data-reads="${t.read_ts.join(" ")}"
    style="border-left-color:${agentColor(t.agent)}">
    <div class="hd"><b style="color:${agentColor(t.agent)}">${esc(t.agent)}</b>
      <span>${esc(t.src)}</span><span class="t">${t.time}</span></div>
    ${rline}${peek}${rep}${cotHtml(t, "t"+t.i, -1)}</div>`;
}
function msgCard(m, mi){
  const t = m.src ? D.turns[m.src.turn] : null;
  const cls = "card msg"+(t?"":" scripted");
  const color = t ? agentColor(t.agent) : (AGENTS.has(m.user)?agentColor(m.user):"var(--human)");
  const secs = s => s.split(":").reduce((a,x)=>a*60+ +x, 0);
  const wakes = m.wakes.length
    ? `<div class="wline">woke ${m.wakes.map(i=>{
        const w = D.turns[i], lag = secs(w.time)-secs(m.time);
        const lagTxt = lag>=90 ? ` +${Math.round(lag/60)}m` : lag>=5 ? ` +${lag}s` : "";
        return `<span class="jump" data-jump="${i}">${esc(w.agent)}${lagTxt}</span>`;
      }).join(", ")}</div>` : "";
  let cot = "";
  if (t){
    const step = t.steps.find(s=>s.calls.some(c=>c.post_ts===m.ts)) || t.steps[t.steps.length-1];
    cot = `<details class="cot" data-oid="m${mi}" ${openSet.has("m"+mi)?"open":""}>`+
      `<summary>CoT</summary><div class="cotbody">`+
      `<div class="sh" style="font-size:10.5px;color:var(--dim)">from ${esc(t.agent)}'s turn ` +
      `<span class="jump" data-jump="${t.i}">#${t.i} ↗</span></div>`+
      (step ? stepHtml(t, step, step.step) : "")+
      (t.report?`<div class="report"><b>→ principal:</b> ${esc(t.report)}</div>`:"")+
      `</div></details>`;
  }
  return `<div class="${cls}" data-mid="${m.ts}" data-src="${t?t.i:""}"
    data-wakes="${m.wakes.join(" ")}" style="border-left-color:${color}">
    <div class="hd"><b style="color:${color}">${esc(m.user)}</b>${t?"":"<span>scripted</span>"}
      <span class="t">${m.time}</span></div>
    <div class="body">${esc(m.text)}</div>${wakes}${cot}</div>`;
}

/* ---------- grid ---------- */
function renderGrid(){
  const grid = document.getElementById("grid");
  const cols = colDefs.filter(c=>c.on);
  const colIdx = Object.fromEntries(cols.map((c,i)=>[c.id, i+2]));

  // assemble logical rows: spacers + markers interleaved by epoch
  const items = [];
  let markers = [...D.markers].sort((a,b)=>a.epoch-b.epoch);
  let prev = null;
  for (const r of D.rows){
    const visible = r.turns.some(ti=>colIdx["a:"+D.turns[ti].agent]) ||
                    r.msgs.some(mi=>colIdx["c:"+D.msgs[mi].conv]);
    if (!visible) continue;   // row lives entirely in toggled-off columns
    while (markers.length && markers[0].epoch <= r.epoch)
      items.push({marker: markers.shift()});
    if (prev!==null && r.epoch - prev > D.gap_s)
      items.push({gap: Math.round((r.epoch-prev)/60)});
    items.push({row: r}); prev = r.epoch;
  }
  for (const m of markers) items.push({marker: m});

  const nrows = items.length + 2;   // header + backlog
  grid.style.gridTemplateColumns = `76px repeat(${cols.length}, minmax(250px, 340px))`;
  let h = "";
  for (const c of cols)
    h += `<div class="stripe" style="grid-column:${colIdx[c.id]};grid-row:1/${nrows+1}"></div>`;
  h += `<div class="colhead rule" style="grid-row:1;grid-column:1">clock</div>`;
  for (const c of cols){
    const lbl = c.kind==="conv" && c.label.startsWith("dm:")
      ? c.label.slice(3).replace("+"," ⇄ ") : c.label;
    const col = c.kind==="agent" ? `color:${agentColor(c.label.split(" ")[0])};` : "";
    h += `<div class="colhead" style="${col}grid-row:1;grid-column:${colIdx[c.id]}">${esc(lbl)}</div>`;
  }
  // backlog row
  h += `<div class="rule" style="grid-row:2;grid-column:1">history</div>`;
  for (const c of cols){
    if (c.kind!=="conv") continue;
    const bl = D.backlog[c.id.slice(2)] || [];
    if (!bl.length) continue;
    h += `<div class="cell bkcell" style="grid-row:2;grid-column:${colIdx[c.id]}">
      <details><summary>${bl.length} earlier message${bl.length!==1?"s":""}</summary>
      ${bl.map(m=>msgCard(m,"b"+m.ts)).join("")}</details></div>`;
  }
  // body rows
  const rowIdx = [];                       // rendered data rows: {gr, epoch}
  const grByTurn = {};                     // turn i -> grid row of its cell
  items.forEach((it, k)=>{
    const gr = k+3;
    if (it.gap){ h += `<div class="spacer" style="grid-row:${gr}">+${it.gap} min</div>`; return; }
    if (it.marker){
      h += `<div class="marker" style="grid-row:${gr}">${esc(it.marker.label)} · ${it.marker.time}</div>`;
      return;
    }
    const r = it.row;
    rowIdx.push({gr, epoch: r.epoch});
    for (const ti of r.turns) grByTurn[ti] = gr;
    h += `<div class="rule" style="grid-row:${gr};grid-column:1">${r.time}</div>`;
    const byCol = {};
    for (const ti of r.turns){
      const id = "a:"+D.turns[ti].agent;
      if (colIdx[id]) (byCol[id] ??= []).push(turnCard(D.turns[ti]));
    }
    for (const mi of r.msgs){
      const id = "c:"+D.msgs[mi].conv;
      if (colIdx[id]) (byCol[id] ??= []).push(msgCard(D.msgs[mi], mi));
    }
    for (const [id, cards] of Object.entries(byCol))
      h += `<div class="cell" style="grid-row:${gr};grid-column:${colIdx[id]}">${cards.join("")}</div>`;
  });
  // active-period bars: one per turn, spanning its lane from start row to the last
  // rendered row that falls inside [t0, t1]
  for (const t of D.turns){
    const col = colIdx["a:"+t.agent], gr0 = grByTurn[t.i];
    if (!col || !gr0) continue;
    let gr1 = gr0;
    for (const {gr, epoch} of rowIdx)
      if (epoch > t.t0 && epoch <= t.t1 && gr > gr1) gr1 = gr;
    h += `<div class="tbar" data-jump="${t.i}" style="grid-column:${col};`+
      `grid-row:${gr0}/${gr1+1};background:${agentColor(t.agent)}" `+
      `title="${esc(t.agent)} · ${t.time} → ${t.end}"></div>`;
  }
  grid.innerHTML = h;
}

/* ---------- cross-highlight + interactions ---------- */
function related(el){
  const ids = new Set([el]);
  const sel = [];
  if (el.dataset.tid !== undefined && el.dataset.tid !== ""){
    for (const ts of (el.dataset.msgs||"").split(" ").filter(Boolean))
      sel.push(`[data-mid="${ts}"]`);
    for (const ts of (el.dataset.reads||"").split(" ").filter(Boolean))
      sel.push(`[data-mid="${ts}"]`);
  }
  if (el.dataset.mid){
    if (el.dataset.src) sel.push(`#turn-${el.dataset.src}`);
    for (const t of (el.dataset.wakes||"").split(" ").filter(Boolean))
      sel.push(`#turn-${t}`);
  }
  for (const s of sel) document.querySelectorAll(s).forEach(x=>ids.add(x));
  return ids;
}
let hovered = [];
document.addEventListener("mouseover", e=>{
  const el = e.target.closest?.(".card"); if (!el) return;
  hovered.forEach(x=>x.classList.remove("hl")); hovered=[];
  for (const x of related(el)){ x.classList.add("hl"); hovered.push(x); }
});
document.addEventListener("mouseout", e=>{
  if (!e.target.closest?.(".card")) return;
  hovered.forEach(x=>x.classList.remove("hl")); hovered=[];
});
document.addEventListener("click", e=>{
  const j = e.target.closest?.("[data-jump]");
  if (j){
    const col = colById["a:"+D.turns[+j.dataset.jump].agent];
    if (!col.on){ col.on=true; renderChips(); renderGrid(); writeHash(); }
    const t = document.getElementById("turn-"+j.dataset.jump);
    if (t){ t.scrollIntoView({block:"center"});
      t.querySelector("details.cot")?.setAttribute("open","");
      t.classList.add("pin"); setTimeout(()=>t.classList.remove("pin"), 2500); }
    return;
  }
  if (e.target.closest?.("details")) return;   // let expanders behave
  const el = e.target.closest?.(".card"); if (!el) return;
  const on = !el.classList.contains("pin");
  document.querySelectorAll(".pin").forEach(x=>x.classList.remove("pin"));
  if (on) for (const x of related(el)) x.classList.add("pin");
});
document.addEventListener("toggle", e=>{
  if (e.target.matches?.("details.cot")) writeHash();
}, true);

// ids → names: wrap known Slack ids in text nodes (TreeWalker, so attributes and scripts
// are never touched), then swap id ⟷ ⟨name⟩. The grid is re-rendered by chip toggles and
// expanders, so the overlay is re-applied after every render while it is on.
const SLACK_IDS = D.id_names || {};
let namesOn = false;
function wrapIds(root){
  const keys = Object.keys(SLACK_IDS); if (!keys.length) return;
  const re = new RegExp('\\b(' + keys.join('|') + ')\\b', 'g');
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = []; let n;
  while (n = w.nextNode()) { re.lastIndex = 0; if (n.nodeValue && re.test(n.nodeValue)) nodes.push(n); }
  for (const node of nodes) {
    const p = node.parentElement; if (!p || p.closest('script,style') || p.classList.contains('slackid')) continue;
    const s = node.nodeValue, frag = document.createDocumentFragment(); let last = 0, m; re.lastIndex = 0;
    while (m = re.exec(s)) {
      frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      const sp = document.createElement('span'); sp.className = 'slackid';
      sp.dataset.id = m[0]; sp.dataset.name = SLACK_IDS[m[0]]; sp.title = m[0]; sp.textContent = m[0];
      frag.appendChild(sp); last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(s.slice(last))); node.parentNode.replaceChild(frag, node);
  }
}
function applyNames(){
  if (namesOn) wrapIds(document.body);
  document.querySelectorAll('.slackid').forEach(sp => {
    sp.textContent = namesOn ? '\u27e8' + sp.dataset.name + '\u27e9' : sp.dataset.id;
    sp.classList.toggle('named', namesOn); });
  document.getElementById('btn-names').textContent = namesOn ? 'names → ids' : 'ids → names';
}
function toggleNames(){ namesOn = !namesOn; applyNames(); }
const _renderGrid = renderGrid;
renderGrid = function(){ _renderGrid(); if (namesOn) applyNames(); };
document.addEventListener("toggle", () => { if (namesOn) applyNames(); }, true);

renderChips(); renderGrid();
</script></body></html>
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        out = render(arg)
        print(out)


if __name__ == "__main__":
    main()
