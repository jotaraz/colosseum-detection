from __future__ import annotations

"""Read a workspace fixture the way the employees would — one self-contained HTML page.

The run viewer answers "what did the assistants do"; this one answers "what was already
there". No turns, no tool calls, no model: just the world the fixture ships, with every
conversation readable and every word searchable.

    python -m experiments.agent1.workspace_viewer experiments/agent1/fixtures/aug2026_v9_renamed.json
    python -m experiments.agent1.workspace_viewer fixtures/aug2026_v9.json -o world.html --open

Two panes: conversations on the left, the thread on the right. Typing in the search box
filters the list to conversations that contain the term, counts the hits, and switches the
right pane to every match across the whole workspace with its context; clicking a result
opens that thread scrolled to the line. `/` focuses the box, Escape clears it.

The page renders from an embedded copy of the fixture rather than from pre-baked HTML, which
is what makes search and the "to Alice" name swap cheap: both are transformations of the data
followed by a re-render, so neither can corrupt the other (the run viewer rewrites text nodes
in place, which would fight with search highlighting).

Reading aids that are properties of the fixture, not of any run:

* the ground-truth tag on a message (``operative``, ``supporting``, …), so the planted signal
  is visible where it sits rather than only in a list of timestamps;
* who has not read a thread — ``read_state`` holds a last-read marker per principal, and the
  messages after it are the unread tail an assistant wakes up to;
* the sprint channel's pinned brief, and the calendars and board, on their own pages.
"""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from experiments.agent1.viewer import ALIAS_BACK, ALIAS_CORE_JS
except ImportError:  # run as a plain script rather than `python -m`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from experiments.agent1.viewer import ALIAS_BACK, ALIAS_CORE_JS

#: Realistic names, for deciding whether the swap button has anything to do.
_RENAMED_RE = re.compile(r"\b(" + "|".join(ALIAS_BACK) + r")\b", re.I)

CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#e2e2e2;
        --card:#fafafa; --accent:#2563eb; --warn:#b45309; --good:#15803d; --code:#f3f4f6;
        --hit:#fde68a; --hitfg:#1a1a1a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --fg:#e6e6e6; --muted:#9aa0a6; --line:#2a2e35; --card:#161a20;
          --accent:#7aa2f7; --warn:#e0af68; --good:#9ece6a; --code:#1b1f27;
          --hit:#7c5e10; --hitfg:#fff; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); height:100vh; display:flex;
       flex-direction:column; overflow:hidden;
       font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }
header { padding:.7rem 1rem .6rem; border-bottom:1px solid var(--line); flex:none; }
h1 { font-size:1.05rem; margin:0 0 .15rem; }
h1 .v { color:var(--accent); }
.note { color:var(--muted); font-size:.82rem; margin:0; }
.bar { display:flex; gap:.5rem; align-items:center; margin-top:.55rem; flex-wrap:wrap; }
input[type=search], select, button { font:inherit; padding:.32rem .6rem; border-radius:6px;
       border:1px solid var(--line); background:var(--card); color:var(--fg); }
input[type=search] { flex:1; min-width:14rem; }
input[type=search]:focus { outline:2px solid var(--accent); outline-offset:-1px; }
button { cursor:pointer; } button:hover { border-color:var(--accent); }
button.on { border-color:var(--accent); color:var(--accent); }
main { flex:1; display:flex; min-height:0; }
#list { width:20rem; flex:none; overflow-y:auto; border-right:1px solid var(--line);
        padding:.4rem; }
#pane { flex:1; overflow-y:auto; padding:.8rem 1.1rem 4rem; }
.conv { padding:.4rem .55rem; border-radius:7px; cursor:pointer; border:1px solid transparent; }
.conv:hover { background:var(--card); }
.conv.sel { background:var(--card); border-color:var(--accent); }
.conv .n { font-weight:600; }
.conv .m { color:var(--muted); font-size:.78rem; display:flex; gap:.4rem; justify-content:space-between; }
.sect { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
        margin:.7rem .55rem .25rem; }
.msg { padding:.3rem .5rem; border-radius:6px; margin:.1rem 0; scroll-margin-top:2rem; }
.msg:hover { background:var(--card); }
.msg .t { color:var(--muted); font-size:.8rem; font-variant-numeric:tabular-nums; margin-right:.45rem; }
.msg .who { font-weight:600; color:var(--accent); }
.msg .body { white-space:pre-wrap; }
.msg.unread { border-left:3px solid var(--warn); padding-left:.5rem; }
.msg.here { background:color-mix(in srgb, var(--accent) 12%, transparent); }
mark { background:var(--hit); color:var(--hitfg); border-radius:3px; padding:0 .1em; }
.tag { font-size:.7rem; padding:.03rem .38rem; border-radius:99px; border:1px solid var(--line);
       color:var(--muted); margin-left:.35rem; white-space:nowrap; }
.tag.operative { color:var(--warn); border-color:var(--warn); }
.tag.unread { color:var(--warn); border-color:var(--warn); }
.pin { background:var(--code); border-left:3px solid var(--muted); border-radius:0 6px 6px 0;
       padding:.5rem .7rem; white-space:pre-wrap; font-size:.86rem; margin:.3rem 0 .9rem; }
.head { border-bottom:1px solid var(--line); padding-bottom:.5rem; margin-bottom:.6rem; }
.head h2 { font-size:1.05rem; margin:0 0 .15rem; }
.head .m { color:var(--muted); font-size:.82rem; }
.hit { border-bottom:1px solid var(--line); padding:.45rem 0; cursor:pointer; }
.hit:hover { background:var(--card); }
.hit .where { color:var(--accent); font-size:.8rem; font-weight:600; }
.empty { color:var(--muted); padding:2rem 0; }
table { border-collapse:collapse; font-size:.85rem; margin:.3rem 0 1.2rem; }
th, td { border:1px solid var(--line); padding:.28rem .5rem; text-align:left; }
th { color:var(--muted); font-weight:500; }
"""

JS = ALIAS_CORE_JS + r"""
/* ------------------------------------------------------------------ state */
let DATA = RAW;                 /* the fixture, possibly name-swapped */
let aliasOn = false;
let selected = null;            /* conversation id, or a reference page id */
let query = "";
let author = "";

const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

/* Deep-map every string in the fixture through the name swap. Same substitution the builder
   applies, so a renamed fixture reads as the placeholder one and back again. */
function aliasDeep(v) {
  if (typeof v === "string") return aliasSub(v);
  if (Array.isArray(v)) return v.map(aliasDeep);
  if (v && typeof v === "object") {
    const out = {};
    for (const [k, val] of Object.entries(v)) out[aliasSub(k)] = aliasDeep(val);
    return out;
  }
  return v;
}

function toAlice() {
  aliasOn = !aliasOn;
  DATA = aliasOn ? aliasDeep(RAW) : RAW;
  const btn = document.getElementById("alias-btn");
  btn.textContent = aliasOn ? "back to original names" : "to Alice";
  btn.classList.toggle("on", aliasOn);
  renderAll();
}

/* ------------------------------------------------------------- derivations */
const label = c => c.type === "channel" ? "#" + c.name : "dm:" + c.members.join("+");
const tagOf = m => (DATA.ground_truth?.message_types || {})[m.ts] || "";

/* A message is unread by X when X is in the conversation and their last-read marker is older
   than it. Own messages never count, which is how the fixture's own read_state is defined. */
function unreadBy(conv, m) {
  const marks = DATA.read_state || {};
  const who = [];
  for (const person of conv.members) {
    if (person === m.user) continue;
    const mark = (marks[person] || {})[conv.id];
    if (mark !== undefined && parseFloat(m.ts) > parseFloat(mark)) who.push(person);
  }
  return who;
}

function matches(m, conv) {
  if (author && m.user !== author) return false;
  if (!query) return true;
  return (m.text + " " + m.user + " " + m.time).toLowerCase().includes(query);
}

function fmt(ts) {
  const d = new Date(parseFloat(ts) * 1000);
  return d.toLocaleString(undefined, {weekday: "short", day: "2-digit", month: "short",
                                      hour: "2-digit", minute: "2-digit"});
}

function highlight(text) {
  if (!query) return esc(text);
  const i = text.toLowerCase().indexOf(query);
  if (i < 0) return esc(text);
  let out = "", rest = text, at = i;
  while (at >= 0) {
    out += esc(rest.slice(0, at)) + "<mark>" + esc(rest.slice(at, at + query.length)) + "</mark>";
    rest = rest.slice(at + query.length);
    at = rest.toLowerCase().indexOf(query);
  }
  return out + esc(rest);
}

/* ---------------------------------------------------------------- rendering */
function renderList() {
  const chans = DATA.conversations.filter(c => c.type === "channel");
  const dms = DATA.conversations.filter(c => c.type !== "channel");
  const rows = [];
  const section = (title, convs) => {
    const inner = convs.map(c => {
      const hits = c.messages.filter(m => matches(m, c)).length;
      if ((query || author) && !hits) return "";
      const unread = c.messages.filter(m => unreadBy(c, m).length).length;
      const sub = c.type === "channel" ? c.members.length + " members"
                                       : c.members.join(", ");
      return `<div class="conv ${selected === c.id ? "sel" : ""}" onclick="open_('${c.id}')">
        <div class="n">${esc(label(c))}</div>
        <div class="m"><span>${esc(sub)}</span><span>${
          (query || author) ? hits + " / " + c.messages.length : c.messages.length}${
          unread ? " · " + unread + " unread" : ""}</span></div></div>`;
    }).join("");
    if (inner.trim()) rows.push(`<div class="sect">${title}</div>` + inner);
  };
  section("Channels", chans);
  section("Direct messages", dms);
  if (!query && !author) {
    rows.push('<div class="sect">Reference</div>');
    for (const [id, name] of [["_people", "People & board"], ["_cal", "Calendars"]])
      rows.push(`<div class="conv ${selected === id ? "sel" : ""}" onclick="open_('${id}')">
                   <div class="n">${name}</div></div>`);
  }
  document.getElementById("list").innerHTML =
    rows.join("") || '<div class="empty">No conversation matches.</div>';
}

function messageHTML(conv, m, focus) {
  const tag = tagOf(m);
  const unread = unreadBy(conv, m);
  const tags = (tag ? `<span class="tag ${tag === "operative" ? "operative" : ""}">${esc(tag)}</span>` : "")
    + (unread.length ? `<span class="tag unread">unread by ${esc(unread.join(", "))}</span>` : "");
  return `<div class="msg ${unread.length ? "unread" : ""} ${focus === m.ts ? "here" : ""}" id="m-${m.ts}">
    <span class="t">${esc(fmt(m.ts))}</span><span class="who">${esc(m.user)}</span>${tags}
    <div class="body">${highlight(m.text)}</div></div>`;
}

function renderResults() {
  const hits = [];
  for (const c of DATA.conversations)
    for (const m of c.messages)
      if (matches(m, c)) hits.push([c, m]);
  hits.sort((a, b) => parseFloat(a[1].ts) - parseFloat(b[1].ts));
  const head = `<div class="head"><h2>${hits.length} match${hits.length === 1 ? "" : "es"}</h2>
    <div class="m">${query ? 'for "' + esc(query) + '"' : "all messages"}${
      author ? " from " + esc(author) : ""} · across ${DATA.conversations.length} conversations</div></div>`;
  document.getElementById("pane").innerHTML = head + (hits.map(([c, m]) =>
    `<div class="hit" onclick="open_('${c.id}','${m.ts}')">
       <div class="where">${esc(label(c))} <span class="t">${esc(fmt(m.ts))}</span></div>
       <div><span class="who">${esc(m.user)}:</span> <span class="body">${highlight(m.text)}</span></div>
     </div>`).join("") || '<div class="empty">Nothing matches.</div>');
}

function renderConversation(focus) {
  const c = DATA.conversations.find(x => x.id === selected);
  if (!c) return renderResults();
  const unread = c.messages.filter(m => unreadBy(c, m).length).length;
  document.getElementById("pane").innerHTML =
    `<div class="head"><h2>${esc(label(c))}</h2><div class="m">${esc(c.id)} ·
       ${esc(c.members.join(", "))} · ${c.messages.length} messages${
       unread ? " · " + unread + " unread" : ""}</div></div>`
    + (c.pinned ? `<div class="pin">${highlight(c.pinned)}</div>` : "")
    + (c.messages.map(m => messageHTML(c, m, focus)).join("")
       || '<div class="empty">No messages — the brief is the whole channel.</div>');
  if (focus) {
    const el = document.getElementById("m-" + focus);
    /* guarded: jsdom (and anything else without layout) has no scrollIntoView, and an
       exception here would abort the click handler after the render has already happened. */
    if (el && el.scrollIntoView) el.scrollIntoView({block: "center"});
  } else {
    document.getElementById("pane").scrollTop = 0;
  }
}

function renderReference() {
  const pane = document.getElementById("pane");
  if (selected === "_people") {
    const rows = DATA.users.map(u => `<tr><td>${esc(u.name)}</td><td>${esc(u.title || "")}</td>
      <td>${esc(u.department || "")}</td><td>${u.is_bot ? "bot" : ""}</td></tr>`).join("");
    const tasks = (DATA.board?.tasks || []).map(t =>
      `<tr><td>${esc(t.id)}</td><td>${esc(t.title)}</td><td>${esc(t.needs || "")}</td></tr>`).join("");
    pane.innerHTML = `<div class="head"><h2>People &amp; board</h2><div class="m">
        principals ${esc((DATA.principals || []).join(", "))} · reporter ${esc(DATA.reporter || "-")}
        · reports to ${esc(DATA.report_to || "-")}</div></div>
      <table><tr><th>name</th><th>title</th><th>department</th><th></th></tr>${rows}</table>
      <h2>${esc(DATA.board?.name || "Board")}</h2>
      <table><tr><th>id</th><th>title</th><th>needs</th></tr>${tasks}</table>`;
  } else {
    const cal = DATA.calendars || {};
    const days = [...new Set(Object.values(cal).flat().map(e => e.start.slice(0, 10)))].sort();
    const head = days.map(d => `<th>${d.slice(8)}/${d.slice(5, 7)}</th>`).join("");
    const rows = Object.entries(cal).map(([who, events]) => {
      const cells = days.map(d => {
        const on = events.filter(e => e.start.startsWith(d));
        const mins = on.reduce((a, e) =>
          a + (new Date(e.end) - new Date(e.start)) / 60000, 0);
        return `<td title="${esc(on.map(e => e.title).join(", "))}">${mins ? Math.round(mins) + "m" : "—"}</td>`;
      }).join("");
      return `<tr><th>${esc(who)}</th>${cells}</tr>`;
    }).join("");
    pane.innerHTML = `<div class="head"><h2>Calendars</h2><div class="m">booked minutes per day;
      hover a cell for the titles</div></div><table><tr><th></th>${head}</tr>${rows}</table>`;
  }
  pane.scrollTop = 0;
}

function renderAll(focus) {
  renderList();
  if (selected === "_people" || selected === "_cal") renderReference();
  else if (query || author || !selected) renderResults();
  else renderConversation(focus);
}

/* ------------------------------------------------------------------ events */
function open_(id, focus) {
  selected = id;
  if (focus) { query = ""; document.getElementById("q").value = ""; }
  renderAll(focus);
}

function onSearch(v) {
  query = v.trim().toLowerCase();
  if (query) selected = null;
  renderAll();
}

function onAuthor(v) { author = v; renderAll(); }

document.addEventListener("keydown", e => {
  const box = document.getElementById("q");
  if (e.key === "/" && document.activeElement !== box) { e.preventDefault(); box.focus(); }
  if (e.key === "Escape") { box.value = ""; onSearch(""); box.blur(); }
});

document.addEventListener("DOMContentLoaded", () => {
  const people = [...new Set(DATA.conversations.flatMap(c => c.messages.map(m => m.user)))].sort();
  document.getElementById("from").innerHTML =
    '<option value="">anyone</option>' + people.map(p => `<option>${esc(p)}</option>`).join("");
  renderAll();
});
"""


def esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def render(workspace: Dict[str, Any], source: str) -> str:
    """The whole page: the fixture as embedded JSON plus the reader around it."""
    convs: List[Dict[str, Any]] = workspace.get("conversations") or []
    total = sum(len(c.get("messages") or []) for c in convs)
    when = workspace.get("now") or ""
    deadline = workspace.get("deadline")
    sub = (f"{len(convs)} conversations · {total} messages · clock {esc(when)}"
           + (f" · closes {esc(str(deadline)[11:16])}" if deadline else "")
           + f" · {esc(source)}")

    # `</` inside the JSON would end the script element early; the escape is invisible to
    # JSON.parse. Embedded rather than fetched so the page stays a single file.
    blob = json.dumps(workspace, ensure_ascii=False).replace("</", "<\\/")

    # The swap only has work to do on the realistic-name variant.
    renamed = bool(_RENAMED_RE.search(blob))
    alias_btn = (
        '<button id="alias-btn" onclick="toAlice()" title="Rewrite Priya/Marcus/Nadia… to '
        'Alice/Bob/Carol… everywhere on this page">to Alice</button>' if renamed else
        '<button id="alias-btn" disabled title="This fixture already uses the placeholder '
        'names">to Alice</button>'
    )

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{esc(workspace.get('version') or 'workspace')} · agent1 world</title>
<style>{CSS}</style></head><body>
<header>
  <h1>agent1 workspace <span class="v">{esc(workspace.get('version') or '')}</span></h1>
  <p class="note">{esc(workspace.get('note') or '')}</p>
  <p class="note">{sub}</p>
  <div class="bar">
    <input type="search" id="q" placeholder="Search every message —  /  to focus, Esc to clear"
           oninput="onSearch(this.value)" autocomplete="off">
    <label class="note">from <select id="from" onchange="onAuthor(this.value)"></select></label>
    {alias_btn}
  </div>
</header>
<main><div id="list"></div><div id="pane"></div></main>
<script>const RAW = {blob};
{JS}</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Browse and search an agent1 workspace fixture.")
    parser.add_argument("workspace", help="path to a fixture .json")
    parser.add_argument("-o", "--out", default=None, help="output .html (default: alongside)")
    parser.add_argument("--open", dest="open_it", action="store_true", help="open when written")
    args = parser.parse_args()

    path = Path(args.workspace)
    with open(path, "r", encoding="utf-8") as fh:
        workspace = json.load(fh)

    out = Path(args.out) if args.out else path.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(workspace, path.name), encoding="utf-8")
    print(f"Wrote {out}")
    if args.open_it:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(out)], check=False)


if __name__ == "__main__":
    main()
