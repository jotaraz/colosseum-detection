"""Inject each turn's start-of-turn model context into run.html.

For every turn, the context is the ``messages`` array of the FIRST upstream
/chat/completions request opencode made inside that turn's wall window — the exact
bytes the model saw (proxy_dump.jsonl records them post-rewrite). Because opencode
sessions are append-only, rendering every turn's full context would be O(n^2)
(25–100MB per run), so each block renders the messages that are NEW since the same
agent's previous turn and links the unchanged prefix back to that turn; a prefix
mismatch (a mid-turn continuation retry, or compaction) is flagged and rendered
from the divergence point.

Rebuilds run.html from run.json first (idempotent — safe to re-run), then injects
one collapsible "context at turn start" block at the top of every turn's body.

Run:  .venv/bin/python -m experiments.agent5.context_view <run_dir_or_run.json> [...]
      .venv/bin/python -m experiments.agent5.context_view --all   # every agent5 run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent1.viewer import esc
from experiments.agent4 import viewer as viewer4

RUNS = Path(__file__).resolve().parent / "runs"
WINDOW_SLACK = 0.2   # seconds of tolerance around the turn's wall window

CSS = """
details.ctxblock{border:1px dashed var(--line);border-radius:6px;margin:6px 0;padding:2px 6px}
details.ctxblock>summary{cursor:pointer;color:var(--muted,#68738a);font-size:12px}
.ctxnote{font-size:12px;color:var(--muted,#68738a);margin:4px 0}
.ctxnote.warn{color:#c2410c}
details.ctxmsg{margin:3px 0;border-left:3px solid var(--line);padding-left:6px}
details.ctxmsg>summary{cursor:pointer;font-size:12px}
details.ctxmsg>summary .r{font-weight:700;text-transform:uppercase;font-size:10.5px;
  letter-spacing:.05em;margin-right:6px}
details.ctxmsg.r-system{border-left-color:#7c3aed}
details.ctxmsg.r-user{border-left-color:#2563eb}
details.ctxmsg.r-assistant{border-left-color:#059669}
details.ctxmsg.r-tool{border-left-color:#d97706}
details.ctxmsg pre{white-space:pre-wrap;font-size:11.5px;max-height:320px;overflow:auto;
  margin:3px 0}
.ctxsub{font-size:10.5px;color:var(--muted,#68738a);text-transform:uppercase;
  letter-spacing:.05em;margin-top:4px}
"""


# --------------------------------------------------------------------- extraction
def load_requests(run_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """agent (lowercase) -> [{wall, messages}] for every chat/completions request,
    in wall order. response_text is dropped line by line to keep memory flat."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    dump = run_dir / "proxy_dump.jsonl"
    if not dump.is_file():
        return out
    with dump.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "chat/completions" not in d.get("path", ""):
                continue
            req = d.get("request") or {}
            msgs = req.get("messages")
            if not isinstance(msgs, list):
                continue
            out.setdefault(str(d.get("agent", "")).lower(), []).append(
                {"wall": d["wall"], "messages": msgs})
    for reqs in out.values():
        reqs.sort(key=lambda r: r["wall"])
    return out


def turn_context(turn: Dict[str, Any],
                 reqs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The turn's opening request, plus the in-window request count.

    A turn begins with opencode appending the runner's wake/ask as a user message, so
    the turn's first request is the one whose LAST message is that exact user text.
    Plain "earliest in window" is not enough: a timed-out previous turn's tool loop
    can keep logging requests into this turn's window (the abort is best-effort),
    and those end in tool results, not the new user message.
    """
    w0 = turn["wall_start"] - WINDOW_SLACK
    w1 = turn["wall_end"] + WINDOW_SLACK
    inwin = [r for r in reqs if w0 <= r["wall"] <= w1]
    if not inwin:
        return None
    want = str(turn.get("message_in") or "")

    def last_user_text(r: Dict[str, Any]) -> Optional[str]:
        last = r["messages"][-1]
        return _content_text(last.get("content")) if last.get("role") == "user" else None

    match = next((r for r in inwin if last_user_text(r) == want), None)
    if match is None:  # e.g. an opencode-side rewrite; still require a user tail
        match = next((r for r in inwin if last_user_text(r) is not None), None)
    picked, exact = (match, match is not None and last_user_text(match) == want) \
        if match is not None else (inwin[0], False)
    return {"messages": picked["messages"], "n_requests": len(inwin), "exact": exact,
            "tail_role": picked["messages"][-1].get("role")}


# ---------------------------------------------------------------------- rendering
def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{type:"text", text:...}, ...]
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p)
                         for p in content)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def _reasoning_text(m: Dict[str, Any]) -> str:
    for key in ("reasoning", "reasoning_content"):
        if isinstance(m.get(key), str) and m[key]:
            return m[key]
    det = m.get("reasoning_details")
    if isinstance(det, list):
        return "\n".join(d.get("text", "") for d in det if isinstance(d, dict))
    return ""


def render_message(m: Dict[str, Any], idx: int) -> str:
    role = str(m.get("role", "?"))
    content = _content_text(m.get("content"))
    bits: List[str] = []
    label = ""
    if role == "tool":
        label = str(m.get("name") or "")
    elif role == "assistant" and m.get("tool_calls"):
        names = [(tc.get("function") or {}).get("name", "?")
                 for tc in m["tool_calls"] if isinstance(tc, dict)]
        label = "→ " + ", ".join(names)
    reasoning = _reasoning_text(m) if role == "assistant" else ""
    if reasoning:
        bits.append(f'<div class="ctxsub">reasoning</div><pre>{esc(reasoning)}</pre>')
    if content:
        bits.append(f"<pre>{esc(content)}</pre>")
    for tc in (m.get("tool_calls") or []) if role == "assistant" else []:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        bits.append(f'<div class="ctxsub">tool call · {esc(fn.get("name", "?"))}</div>'
                    f'<pre>{esc(fn.get("arguments", ""))}</pre>')
    if not bits:
        bits.append("<pre><em>empty</em></pre>")
    peek = esc((reasoning or content)[:110])
    return (f'<details class="ctxmsg r-{esc(role)}"><summary><span class="r">{esc(role)}'
            f"</span>{esc(label)} <span>#{idx} · {len(content) + len(reasoning)} chars"
            f"</span> — {peek}</summary>{''.join(bits)}</details>")


def render_context(turn_i: int, ctx: Optional[Dict[str, Any]],
                   prev: Optional[List[Dict[str, Any]]], prev_turn: Optional[int]) -> str:
    if ctx is None:
        return ('<div class="ctxnote warn">no upstream request captured for this turn '
                "(timed out before the first model call, or no proxy dump)</div>")
    msgs = ctx["messages"]
    common = 0
    if prev is not None:
        while common < min(len(prev), len(msgs)) and prev[common] == msgs[common]:
            common += 1
    parts: List[str] = []
    if prev is not None and common:
        parts.append(
            f'<div class="ctxnote">messages 1–{common} unchanged since the context of '
            f'<a href="#turn-{prev_turn}">turn {prev_turn}</a> (this agent\'s previous '
            "turn) — shown there</div>")
    if prev is not None and common < len(prev):
        parts.append(
            f'<div class="ctxnote warn">context diverges from turn {prev_turn}: its '
            f"messages {common + 1}–{len(prev)} are gone (mid-turn continuation retry "
            "or compaction); everything from the divergence point is shown below</div>")
    if not ctx.get("exact"):
        parts.insert(0, (
            '<div class="ctxnote warn">attribution is approximate: no request in this '
            "turn's window ends with this turn's exact incoming message (tail role: "
            f'{esc(ctx.get("tail_role"))}) — shown is the closest candidate</div>'))
    parts += [render_message(m, common + 1 + j) for j, m in enumerate(msgs[common:])]
    if not msgs[common:]:
        parts.append('<div class="ctxnote">no new messages beyond the carried-over '
                     "prefix</div>")
    extra = (f" · {ctx['n_requests']} model calls this turn (context shown is the first)"
             if ctx["n_requests"] > 1 else "")
    return (f'<details class="ctxblock"><summary>context at turn start · '
            f"{len(msgs)} messages ({len(msgs) - common} new){extra}</summary>"
            f"{''.join(parts)}</details>")


# ---------------------------------------------------------------------- injection
def inject(run_path: Path) -> Path:
    run_dir = run_path.parent
    r = json.loads(run_path.read_text())
    reqs_by_agent = load_requests(run_dir)

    blocks: Dict[int, str] = {}
    prev_ctx: Dict[str, List[Dict[str, Any]]] = {}
    prev_turn: Dict[str, Optional[int]] = {}
    for i, t in enumerate(r.get("turns") or []):
        agent = str(t.get("agent", "")).lower()
        ctx = turn_context(t, reqs_by_agent.get(agent, []))
        blocks[i] = render_context(i, ctx, prev_ctx.get(agent), prev_turn.get(agent))
        if ctx is not None:
            prev_ctx[agent] = ctx["messages"]
            prev_turn[agent] = i

    out = viewer4.render(run_path)   # fresh page: idempotent, anchors guaranteed
    html = out.read_text()
    html = html.replace("</style>", CSS + "</style>", 1)
    for i, blk in blocks.items():
        anchor = f'id="turn-{i}"'
        a = html.find(anchor)
        if a < 0:
            continue
        b = html.find('<div class="body">', a)
        if b < 0:
            continue
        b += len('<div class="body">')
        html = html[:b] + blk + html[b:]
    out.write_text(html)
    return out


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args == ["--all"]:
        args = sorted(str(p) for p in RUNS.glob("*/run.json"))
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            p = p / "run.json"
        try:
            print(inject(p))
        except Exception as exc:
            print(f"{p}: FAILED — {exc}")


if __name__ == "__main__":
    main()
