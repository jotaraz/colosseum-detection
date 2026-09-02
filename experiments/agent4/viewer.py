"""Render an agent4 run.json into HTML using agent1's turn/step visualization.

A thin wrapper over ``experiments.agent1.viewer``: the timeline, the collapsible turns,
and the step interior (reasoning → tool calls with args/results, in the order the model
ran) are agent1's own renderers, so the two experiments read identically. Run
``reasoning_extract.py`` first (the runner does both automatically) — it builds the
``steps_detail`` / ``tool_calls[].step`` structure those renderers expect.

agent4 extras: the outcome/board header, and the notify_user ledger panel at the bottom.

Run:  python experiments/agent4/viewer.py <run.json>   (writes run.html next to it)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent1.viewer import (
    CSS,
    block,
    esc,
    reads_by_conversation,
    render_timeline,
    render_transcript,
    written_during_run,
)
from experiments.agent1.workspace import human_time


def _label_order(label: str, sprint_channel: str) -> tuple:
    """Sprint channel first, then the other channels, then DMs."""
    if label.lstrip("#") == sprint_channel:
        return (0, label)
    return (1 if label.startswith("#") else 2, label)


def _build_transcript(r: Dict[str, Any], sprint_channel: str) -> Dict[str, list]:
    """conversation label → messages in arrival order, agent1's transcript shape.

    Preferred source: ``messages`` (the world's final feed, saved by the runner). Older
    records lack it; those are reconstructed from the fixture plus the run's own posts —
    complete except calendar-bot DMs sent during the run.
    """
    rows: list = []
    if r.get("messages"):
        rows = [dict(m) for m in r["messages"]]
    else:
        cfg = r.get("config") or {}
        fixture_path = REPO / str(cfg.get("fixture") or "")
        if fixture_path.is_file():
            fx = json.loads(fixture_path.read_text())
            for conv in fx.get("conversations") or []:
                label = (f"#{conv['name']}" if conv.get("type", "channel") == "channel"
                         else "dm:" + "+".join(conv.get("members") or []))
                for m in conv.get("messages") or []:
                    rows.append({"label": label, "ts": m["ts"], "user": m["user"], "text": m["text"]})
        script_path = REPO / str(cfg.get("script") or "")
        if script_path.is_file():
            by_label = {w["ts"]: w["label"] for w in r.get("wake_log") or []}
            for e in json.loads(script_path.read_text()):
                from datetime import datetime as _dt
                ts = f"{_dt.fromisoformat(e['at']).timestamp():.6f}"
                rows.append({"label": by_label.get(ts) or e["conversation"],
                             "ts": ts, "user": e["user"], "text": e["text"]})
        for w in r.get("wake_log") or []:
            if w.get("source") == "reminder" and not any(x["ts"] == w["ts"] for x in rows):
                rows.append({"label": w["label"], "ts": w["ts"], "user": w["from"],
                             "text": "REMINDER: task sign-up for this sprint is still open "
                                     "and the board is not complete. Please claim your tasks."})
        for t in r.get("turns") or []:
            for c in t.get("tool_calls") or []:
                res = c.get("result")
                if (c.get("tool") == "slack_post_message" and isinstance(res, dict)
                        and res.get("ok") and res.get("ts")):
                    rows.append({"label": res.get("conversation"), "ts": res["ts"],
                                 "user": res.get("posted_as"), "text": (c.get("args") or {}).get("text")})

    import datetime as _dtm
    transcript: Dict[str, list] = {}
    seen = set()
    for m in sorted(rows, key=lambda x: float(x["ts"])):
        key = (m.get("label"), m["ts"], m.get("user"), m.get("text"))
        if key in seen:
            continue
        seen.add(key)
        transcript.setdefault(str(m.get("label")), []).append({
            "ts": str(m["ts"]),
            "time": human_time(_dtm.datetime.fromtimestamp(float(m["ts"]))),
            "from": m.get("user"), "text": m.get("text"),
        })
    return dict(sorted(transcript.items(), key=lambda kv: _label_order(kv[0], sprint_channel)))


def _conv_labels(r: Dict[str, Any]) -> Dict[str, str]:
    """Slack conversation id -> display label, from the run's feed plus the fixture."""
    label_by_cid: Dict[str, str] = {}
    for m in r.get("messages") or []:
        if m.get("conv_id"):
            label_by_cid[m["conv_id"]] = str(m.get("label"))
    cfg = r.get("config") or {}
    fixture_path = REPO / str(cfg.get("fixture") or "")
    if fixture_path.is_file():
        fx = json.loads(fixture_path.read_text())
        names = {u["id"]: u["name"] for u in fx.get("users") or []}
        for c in fx.get("conversations") or []:
            label_by_cid.setdefault(c["id"], (
                f"#{c['name']}" if c.get("is_channel")
                else "dm:" + "+".join(sorted(names.get(u, u) for u in c.get("members") or []))))
    return label_by_cid


def _read_calls(call: Dict[str, Any], label_by_cid: Dict[str, str]) -> list:
    """(label, ts) pairs a single agent5 read call handed to the model."""
    res = call.get("result") if isinstance(call.get("result"), dict) else {}
    if not res.get("ok"):
        return []
    tool = call.get("tool")
    if tool in ("conversations_history", "conversations_replies"):
        cid = str((call.get("args") or {}).get("channel"))
        return [(label_by_cid.get(cid, cid), str(m.get("ts")))
                for m in res.get("messages") or [] if isinstance(m, dict)]
    if tool == "search_messages":
        return [(label_by_cid.get(str((h.get("channel") or {}).get("id")), ""), str(h.get("ts")))
                for h in (res.get("messages") or {}).get("matches") or [] if isinstance(h, dict)]
    if tool == "pins_list":
        cid = str((call.get("args") or {}).get("channel"))
        return [(label_by_cid.get(cid, cid), str((it.get("message") or {}).get("ts")))
                for it in res.get("items") or [] if isinstance(it, dict)]
    return []


def _reads_detail(r: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, tuple]]]:
    """label -> reader -> ts -> (turn index, when) for the FIRST read of each message.

    ``when`` is the exact sim time of the read call (every world call is clock-stamped
    by the server); when a call somehow lacks it, the turn's clock interval stands in.
    """
    label_by_cid = _conv_labels(r)
    detail: Dict[str, Dict[str, Dict[str, tuple]]] = {}
    for i, t in enumerate(r.get("turns") or []):
        agent = str(t.get("agent") or "")
        span = f'~{(t.get("clock") or "")[11:16]}–{(t.get("clock_end") or "")[11:16]}'
        for c in t.get("tool_calls") or []:
            handed = _read_calls(c, label_by_cid)
            if not handed:
                continue
            when = str(c.get("clock") or "")[11:19] or span
            for lab, ts in handed:
                slot = detail.setdefault(lab, {}).setdefault(agent, {})
                slot.setdefault(ts, (i, when))
    return detail


def _normalize_slack_calls(r: Dict[str, Any], turns: list) -> list:
    """agent5 posts/reads through Slack-Web-API-shaped tools; agent1's helpers
    (written_during_run, reads_by_conversation) know only the tanager tool names and
    shapes. Rewrite the agent5 calls into those shapes so the channels tab marks writes
    and reads; agent4 records pass through untouched (their tools already match)."""
    label_by_cid = _conv_labels(r)

    out = []
    for t in turns:
        calls = []
        for c in t.get("tool_calls") or []:
            res = c.get("result") if isinstance(c.get("result"), dict) else {}
            tool = c.get("tool")
            if tool == "chat_postMessage" and res.get("ok") and res.get("ts"):
                calls.append({**c, "tool": "slack_post_message",
                              "result": {"ok": True, "ts": str(res["ts"]),
                                         "conversation": label_by_cid.get(str(res.get("channel")), str(res.get("channel"))),
                                         "posted_as": t.get("agent")}})
            elif tool in ("conversations_history", "conversations_replies") and res.get("ok"):
                cid = str((c.get("args") or {}).get("channel"))
                calls.append({**c, "tool": "slack_get_messages",
                              "result": {"conversation": label_by_cid.get(cid, cid),
                                         "messages": [m for m in res.get("messages") or []
                                                      if isinstance(m, dict)]}})
            elif tool == "search_messages" and res.get("ok"):
                hits = (res.get("messages") or {}).get("matches") or []
                calls.append({**c, "tool": "slack_search",
                              "result": {"matches": [
                                  {"conversation": label_by_cid.get(str((h.get("channel") or {}).get("id")), ""),
                                   "ts": str(h.get("ts"))} for h in hits if isinstance(h, dict)]}})
            elif tool == "pins_list" and res.get("ok"):
                cid = str((c.get("args") or {}).get("channel"))
                calls.append({**c, "tool": "slack_get_messages",
                              "result": {"conversation": label_by_cid.get(cid, cid),
                                         "messages": [it["message"] for it in res.get("items") or []
                                                      if isinstance(it, dict) and it.get("message")]}})
            else:
                calls.append(c)
        out.append({**t, "tool_calls": calls})
    return out


def _exposures(r: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, tuple]]]:
    """label -> agent -> ts -> (turn, when, route): the EARLIEST moment each message's
    full text reached each assistant's context. Read is read — a wake delivery carries
    the complete raw event, so it counts exactly like a tool fetch; the route survives
    only as an initiative signal (did it get pushed, or did the assistant go look)."""
    uni: Dict[str, Dict[str, Dict[str, tuple]]] = {
        lab: {a: {ts: (i, when, "fetched") for ts, (i, when) in byts.items()}
              for a, byts in bya.items()}
        for lab, bya in _reads_detail(r).items()}
    for i, t in enumerate(r.get("turns") or []):
        w = t.get("wake") or {}
        ts, lab = w.get("ts"), w.get("label")
        if not ts or not lab:
            continue
        cur = uni.setdefault(str(lab), {}).setdefault(str(t.get("agent") or ""), {})
        if str(ts) not in cur or i < cur[str(ts)][0]:
            cur[str(ts)] = (i, (t.get("clock") or "")[11:19], "wake")
    return uni


def _render_transcript5(transcript, signals, written, exposures) -> str:
    """agent1's render_transcript with unified exposure marks for agent5: one tag per
    (message, assistant) at the earliest moment the text entered that assistant's
    context — wake delivery or tool fetch — with turn, exact sim time, and route."""
    out = []
    for label, messages in transcript.items():
        readers = exposures.get(label) or {}
        rows = []
        fresh = 0
        for m in messages:
            ts = str(m.get("ts"))
            source = written.get(ts)
            marks = ""
            if m.get("ts") in signals:
                marks += ' <span class="tag signal">signal</span>'
            if source:
                fresh += 1
                marks += f' <span class="tag post">{esc(source)}</span>'
            saw = False
            for who in sorted(readers):
                hit = readers[who].get(ts)
                if not hit:
                    continue
                saw = True
                marks += (f' <span class="tag read">read by {esc(who)} · '
                          f't{hit[0]} {esc(hit[1])} ({esc(hit[2])})</span>')
            rows.append(
                f'<div class="msg{" new" if source else ""}{" read" if saw else ""}">'
                f'<span class="t">{esc(m.get("time"))}</span>'
                f'<strong>{esc(m.get("from"))}:</strong> {esc(m.get("text"))}{marks}</div>')

        msg_ts = {str(m.get("ts")) for m in messages}
        tags = [f'<span class="tag">{len(messages)} messages</span>']
        tags.append(
            f'<span class="tag post">{fresh} written during the run</span>' if fresh
            else '<span class="tag">nothing written</span>')
        if readers:
            for who in sorted(readers):
                hits = {ts: h for ts, h in readers[who].items() if ts in msg_ts}
                if not hits:
                    continue
                whole = "" if len(hits) >= len(messages) else " partial"
                first = min(hits.values())
                fetched = sum(1 for h in hits.values() if h[2] == "fetched")
                tags.append(
                    f'<span class="tag read{whole}">{esc(who)} read {len(hits)}/{len(messages)}'
                    f' ({fetched} fetched) · from t{first[0]}</span>')
        else:
            tags.append('<span class="tag">nobody saw it</span>')
        out.append(block(f'{esc(label)} {"".join(tags)}', "".join(rows) or "<em>empty</em>"))
    return "".join(out)


def _agent1_shape(turn: Dict[str, Any]) -> Dict[str, Any]:
    t = dict(turn)
    t["tool_calls"] = [{**c, "arguments": c.get("args") or {}} for c in turn.get("tool_calls") or []]
    tokens = (turn.get("usage") or {}).get("tokens") or {}
    if tokens:
        t["usage"] = {"prompt_tokens": tokens.get("input", 0),
                      "completion_tokens": tokens.get("output", 0)}
    return t


SCHED_PALETTE = ["#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2"]
KIND_FILL_OPACITY = {"ask": 1.0, "closing": 0.55, "wake": 0.8, "ring": 0.8, "added": 0.9}


def _schedule_svg(r: Dict[str, Any], sprint_label: str) -> str:
    """Swimlane schedule: sprint-channel messages | one column per main character's turns |
    everything else (extra assistants' turns, scripted deliveries). Vertical axis is
    synthetic time; a block is one turn (clock → clock_end)."""
    import datetime as dtm

    turns = r.get("turns") or []
    if not turns:
        return "<p class='sub'>no turns</p>"
    principals = [a for a in dict.fromkeys(t["agent"] for t in turns)]
    ring_order = ((r.get("rings") or {}).get("C-sprint") or {}).get("order")
    fx_principals = [p for p in (ring_order or principals) if p in principals][:4]
    others = [a for a in principals if a not in fx_principals]
    color = {a: SCHED_PALETTE[i % len(SCHED_PALETTE)] for i, a in enumerate(fx_principals + others)}

    def ts_of(iso: str) -> float:
        return dtm.datetime.fromisoformat(iso).timestamp()

    t0 = ts_of(r["clock_start"]) - 60
    ends = [ts_of(t.get("clock_end") or t["clock"]) for t in turns]
    t1 = max(ends + ([ts_of(r["deadline"])] if r.get("deadline") else [])) + 120
    px_per_min = 11.0

    def y(ts: float) -> float:
        return 26 + (ts - t0) / 60.0 * px_per_min

    col_w, gutter, left = 150, 14, 56
    cols = ["group chat"] + fx_principals + ["others"]
    width = left + len(cols) * (col_w + gutter)
    height = int(y(t1)) + 20

    out = [f'<svg width="{width}" height="{height}" font-family="sans-serif" font-size="11">']
    # grid + headers
    m0 = int(t0 // 300) * 300
    while m0 < t1:
        if m0 >= t0:
            yy = y(m0)
            label = dtm.datetime.fromtimestamp(m0).strftime("%H:%M")
            out.append(f'<line x1="{left-6}" y1="{yy:.0f}" x2="{width}" y2="{yy:.0f}" '
                       'stroke="#94a3b8" stroke-opacity="0.25"/>')
            out.append(f'<text x="4" y="{yy+4:.0f}" fill="#64748b">{label}</text>')
        m0 += 300
    if r.get("deadline"):
        yy = y(ts_of(r["deadline"]))
        out.append(f'<line x1="{left-6}" y1="{yy:.0f}" x2="{width}" y2="{yy:.0f}" '
                   'stroke="#dc2626" stroke-dasharray="4 3"/>')
        out.append(f'<text x="4" y="{yy-4:.0f}" fill="#dc2626">deadline</text>')
    for i, name in enumerate(cols):
        x = left + i * (col_w + gutter)
        c = color.get(name, "#334155")
        out.append(f'<text x="{x}" y="14" font-weight="bold" fill="{c}">{esc(name)}</text>')
        out.append(f'<line x1="{x-7}" y1="20" x2="{x-7}" y2="{height-6}" '
                   'stroke="#e2e8f0"/>' if i else "")

    def col_x(name: str) -> int:
        if name in fx_principals:
            return left + (1 + fx_principals.index(name)) * (col_w + gutter)
        return left + (1 + len(fx_principals)) * (col_w + gutter)

    # turn blocks (main + others columns)
    for t in turns:
        a, x = t["agent"], col_x(t["agent"])
        y0 = y(ts_of(t["clock"]))
        y1 = max(y(ts_of(t.get("clock_end") or t["clock"])), y0 + 6)
        kind = t.get("kind", "wake")
        tip = (f'#{t["i"]} {a} {kind} {t["clock"][11:19]}–{str(t.get("clock_end") or "")[11:19]} · '
               f'{len(t.get("tool_calls") or [])} tool calls')
        out.append(
            f'<a href="#turn-{t["i"]}" onclick="showTab(\'timeline\')">'
            f'<rect x="{x}" y="{y0:.1f}" width="{col_w-24}" height="{(y1-y0):.1f}" rx="3" '
            f'fill="{color.get(a, "#334155")}" fill-opacity="{KIND_FILL_OPACITY.get(kind, 0.8)}">'
            f'<title>{esc(tip)}</title></rect>'
            + (f'<text x="{x+4}" y="{y0+11:.1f}" fill="#fff">#{t["i"]} {esc(kind)}</text>'
               if y1 - y0 >= 12 else "") + "</a>")

    # group-chat messages + scripted/non-roster events
    run_start = ts_of(r["clock_start"])
    roster = set(principals)
    for m in r.get("messages") or []:
        ts = float(m["ts"])
        if ts <= run_start - 1:
            continue
        sender = m.get("user")
        tip = f'[{dtm.datetime.fromtimestamp(ts).strftime("%H:%M:%S")}] {sender}: {m.get("text")}'
        if m.get("label") == sprint_label:
            x, yy = left, y(ts)
            c = color.get(sender, "#334155")
            out.append(f'<g><circle cx="{x+5}" cy="{yy:.1f}" r="4" fill="{c}"/>'
                       f'<text x="{x+13}" y="{yy+4:.1f}" fill="{c}">{esc(sender)}</text>'
                       f'<title>{esc(tip[:400])}</title></g>')
        elif sender not in roster:
            x, yy = col_x("__others__"), y(ts)
            out.append(f'<g><rect x="{x+col_w-38}" y="{yy-4:.1f}" width="8" height="8" fill="#64748b"/>'
                       f'<text x="{x+col_w-88}" y="{yy+4:.1f}" fill="#64748b" font-size="10">'
                       f'{esc(sender)}</text><title>{esc(tip[:400])}</title></g>')
    out.append("</svg>")
    return ('<p class="sub">blocks = turns (start → end, synthetic time); click one to jump '
            "to it. Dots = sprint-channel messages; gray squares = scripted/non-assistant "
            "events.</p>" + "".join(out))


def render(run_path: str | Path) -> Path:
    run_path = Path(run_path)
    r = json.loads(run_path.read_text())
    cfg = r.get("config") or {}
    score = r.get("score") or {}

    signals: set = set()
    sprint_channel = ""
    #: Slack id -> human-readable name, for the "ids → names" toggle (agent5 fixtures
    #: only; agent4 fixtures are name-keyed so the map stays empty and the button hides).
    id_names: Dict[str, str] = {}
    fixture_path = REPO / str(cfg.get("fixture") or "")
    if fixture_path.is_file():
        fx = json.loads(fixture_path.read_text())
        sprint_channel = str(fx.get("sprint_channel") or "")
        if not sprint_channel and fx.get("sprint_channel_id"):  # agent5 slack-native fixture
            conv = next((c for c in fx.get("conversations", [])
                         if c["id"] == fx["sprint_channel_id"]), None)
            sprint_channel = str((conv or {}).get("name") or "")
        signals = set((fx.get("ground_truth") or {}).get("signal_messages") or [])
        by_uid = {u["id"]: u["name"] for u in fx.get("users") or []
                  if str(u.get("id", "")).startswith("U")}
        id_names.update(by_uid)
        for c in fx.get("conversations") or []:
            cid = str(c.get("id") or "")
            if cid.startswith(("C", "D")) and len(cid) > 8:
                id_names[cid] = (f"#{c['name']}" if c.get("is_channel")
                                 else "dm:" + "+".join(sorted(by_uid.get(u, u)
                                                              for u in c.get("members") or [])))

    for m in r.get("messages") or []:  # runtime-opened DMs are not in the fixture
        if m.get("conv_id") and m.get("label"):
            id_names.setdefault(str(m["conv_id"]), str(m["label"]))

    turns = [_agent1_shape(t) for t in r.get("turns") or []]
    # Number the turns with their index in the record's turn list — the same index
    # analysis and judging use — so "turn 36" in a discussion is findable in the html.
    for i, t in enumerate(turns):
        t["kind"] = f"turn {i} · {t.get('kind', 'work')}"
    timeline = render_timeline(turns, signals, sprint_channel, r.get("system_prompts") or {})

    transcript = _build_transcript(r, sprint_channel)
    marker_turns = _normalize_slack_calls(r, turns)
    written = written_during_run(marker_turns)
    reads = reads_by_conversation(marker_turns, transcript)
    if r.get("experiment") == "agent5":
        channels_tab = _render_transcript5(transcript, signals, written, _exposures(r))
    else:
        channels_tab = render_transcript(transcript, signals, written, reads)
    if not r.get("messages"):
        channels_tab = ('<p class="sub">reconstructed from fixture + this run\'s posts '
                        "(pre-`messages` record); calendar-bot DMs sent during the run "
                        "are not shown</p>") + channels_tab

    schedule_tab = _schedule_svg(r, sprint_label="#" + sprint_channel if sprint_channel else "")

    notif_rows = "".join(
        f'<tr><td class="when">{esc(n.get("time"))}</td><td><b>{esc(n.get("agent"))}</b></td>'
        f"<td>{esc(n.get('text'))}</td></tr>"
        for n in (r.get("notifications") or []))
    notif_panel = block(
        f'notify_user ledger <span class="tag">{len(r.get("notifications") or [])} notes</span>',
        f'<table class="notif">{notif_rows}</table>', open_=True)

    wake_rows = "".join(
        f'<tr><td class="when">{esc(w.get("clock", ""))[11:16]}</td><td><b>{esc(w.get("agent"))}</b></td>'
        f'<td>{esc(w.get("label"))}</td><td>{esc(w.get("from"))}</td>'
        f'<td>{esc(w.get("source"))}</td><td>{esc(w.get("latency_s", ""))}</td></tr>'
        for w in (r.get("wake_log") or []))
    wake_panel = block(
        f'wake log <span class="tag">{len(r.get("wake_log") or [])} wakes</span>',
        '<table class="notif"><tr><th>clock</th><th>assistant</th><th>where</th>'
        f"<th>from</th><th>source</th><th>latency</th></tr>{wake_rows}</table>")

    head = (
        f'<h1>{esc(cfg.get("name") or "agent4 run")} — {esc(r.get("outcome"))}</h1>'
        f'<p class="sub">{esc(cfg.get("model"))} · fixture {esc((r.get("fixture") or {}).get("version"))} '
        f'({esc((r.get("fixture") or {}).get("sha"))}) · on duty {esc(r.get("clock_start", ""))[11:16]} · '
        f'asks {esc(r.get("kickoff", ""))[11:16]} · deadline {esc(r.get("deadline", ""))[11:16]} · '
        f'{len(turns)} turns · wall {esc(r.get("elapsed_seconds"))}s</p>'
        f'<p class="sub"><b>board:</b> {esc(json.dumps(r.get("assignments")))} · '
        f'<b>pairs:</b> {esc(json.dumps(score.get("pairs")))} · <b>valid:</b> {esc(score.get("valid"))}</p>'
        '<p class="sub"><button onclick="setAll(true)">expand all</button> '
        '<button onclick="setAll(false)">collapse all</button> '
        '<button id="tab-timeline" class="tabbtn on" onclick="showTab(\'timeline\')">timeline</button>'
        '<button id="tab-channels" class="tabbtn" onclick="showTab(\'channels\')">channels</button>'
        '<button id="tab-schedule" class="tabbtn" onclick="showTab(\'schedule\')">schedule</button>'
        + ('<button id="btn-names" onclick="toggleNames()" title="Replace raw Slack ids '
           'with ⟨names⟩ — a viewer overlay, not what was written">ids → names</button>'
           if id_names else "")
        + "</p>"
    )
    js = (
        "function setAll(v){document.querySelectorAll('details').forEach(d=>d.open=v)}"
        "function showTab(t){for(const n of ['timeline','channels','schedule']){"
        "document.getElementById('pane-'+n).style.display=(n===t?'':'none');"
        "document.getElementById('tab-'+n).classList.toggle('on',n===t);}}"
        # ids → names toggle: wrap known Slack ids in text nodes on first use (TreeWalker,
        # so attributes/scripts are never touched), then swap id ⟷ ⟨name⟩. The ⟨⟩ + tint
        # signal that the name is a viewer substitution, not what the agent wrote.
        f"const SLACK_IDS={json.dumps(id_names, ensure_ascii=False)};"
        "let namesOn=false,idsWrapped=false;"
        "function wrapIds(){const keys=Object.keys(SLACK_IDS);if(!keys.length)return;"
        "const re=new RegExp('\\\\b('+keys.join('|')+')\\\\b','g');"
        "const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,null);"
        "const nodes=[];let n;while(n=w.nextNode()){re.lastIndex=0;"
        "if(n.nodeValue&&re.test(n.nodeValue))nodes.push(n);}"
        "for(const node of nodes){const p=node.parentElement;"
        "if(!p||p.closest('script,style'))continue;"
        "const s=node.nodeValue,frag=document.createDocumentFragment();"
        "let last=0,m;re.lastIndex=0;"
        "while(m=re.exec(s)){frag.appendChild(document.createTextNode(s.slice(last,m.index)));"
        "const sp=document.createElement('span');sp.className='slackid';"
        "sp.dataset.id=m[0];sp.dataset.name=SLACK_IDS[m[0]];sp.title=m[0];"
        "sp.textContent=m[0];frag.appendChild(sp);last=m.index+m[0].length;}"
        "frag.appendChild(document.createTextNode(s.slice(last)));"
        "node.parentNode.replaceChild(frag,node);}idsWrapped=true;}"
        "function toggleNames(){if(!idsWrapped)wrapIds();namesOn=!namesOn;"
        "document.querySelectorAll('.slackid').forEach(sp=>{"
        "sp.textContent=namesOn?'\\u27e8'+sp.dataset.name+'\\u27e9':sp.dataset.id;"
        "sp.classList.toggle('named',namesOn);});"
        "document.getElementById('btn-names').textContent="
        "namesOn?'names → ids':'ids → names';}")
    extra_css = (".notif{border-collapse:collapse;width:100%;font-size:13px}"
                 ".notif td,.notif th{border-top:1px solid var(--line);padding:4px 8px;"
                 "vertical-align:top;text-align:left}"
                 ".tabbtn{margin-left:6px}.tabbtn.on{font-weight:700;text-decoration:underline}"
                 "#btn-names{margin-left:14px}"
                 ".slackid.named{background:rgba(90,140,255,.14);"
                 "border-bottom:1px dotted rgba(90,140,255,.8);border-radius:3px;padding:0 2px}")

    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>'
           f'{esc(cfg.get("name") or "agent4 run")}</title>'
           f"<style>{CSS}{extra_css}</style></head><body><main>"
           f"{head}"
           f'<div id="pane-timeline">{timeline}{wake_panel}{notif_panel}</div>'
           f'<div id="pane-channels" style="display:none">{channels_tab}</div>'
           f'<div id="pane-schedule" style="display:none; overflow-x:auto">{schedule_tab}</div>'
           f"</main><script>{js}</script></body></html>")

    out = run_path.parent / "run.html"
    out.write_text(doc)
    return out


if __name__ == "__main__":
    print(render(sys.argv[1]))
