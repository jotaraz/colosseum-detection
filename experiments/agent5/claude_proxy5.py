"""Rewriting pass-through between ``claude -p`` and api.anthropic.com (agent5 claude-cli).

Claude Code injects context into every conversation that no flag turns off (measured
2026-09-14 on 2.1.270: ``--system-prompt-file``, ``--setting-sources ""``,
``CLAUDE_CODE_OVERRIDE_DATE`` and ``CLAUDE_CODE_DISABLE_ATTACHMENTS`` all leave it in):

- a ``session_context`` reminder carrying the logged-in account's email address;
- a ``date`` reminder with the wall-clock date, which contradicts the simulated one.

Each assistant's ``claude`` runs with ``ANTHROPIC_BASE_URL=http://127.0.0.1:<port>/a/<agent>``;
the prefix is stripped, ``/v1/messages`` bodies are rewritten, and everything else
(auth headers included) is forwarded verbatim with the response streamed straight back.
The rewrite is deterministic, so every resend of the history is byte-identical and the
prompt cache keeps hitting.

Rewrites, on text blocks in ``messages``:
- the ``# userEmail`` section is cut from the context reminder; a reminder left with no
  section is dropped (the block, not the message);
- ``Today's date is YYYY-MM-DD.`` becomes the simulated date; a "The date has changed"
  reminder (a run crossing midnight) is dropped;
- the "First privately list what you need next" system message Claude Code appends after
  tool results is dropped (assistants were answering it in their text to the principal).

After that structured pass, a backstop scrub walks every string in ``messages`` and
``system``: the email sentence (any address) and the account's email, full name and home
path (read from ~/.claude.json at start) are removed wherever they appear.

``rewrites.jsonl`` records per request what was changed and whether the email or the real
date survived anywhere in the body (``leak``), so a run can be checked after the fact.

Run:  python experiments/agent5/claude_proxy5.py --out <dir> --port <p> --date 2026-09-07
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = "https://api.anthropic.com"
HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}

EMAIL_SECTION = re.compile(r"# userEmail\n.*?(?=\n# |\n\s*IMPORTANT: this context|\Z)", re.S)
CONTEXT_HEAD = "As you answer the user's questions, you can use the following context:"
DATE_LINE = re.compile(r"Today's date is \d{4}-\d{2}-\d{2}\.")
#: Claude Code's mid-conversation system message after tool results (Opus 5). Assistants
#: answered it in the text to their principal ("What I need next: nothing").
BATCHING_REMINDER = "First privately list what you need next;"
EMAIL_ANY = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
DATE_ANY = re.compile(r"Today's date is (?:now )?(\d{4}-\d{2}-\d{2})")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
EMAIL_SENTENCE = re.compile(r"(?:# userEmail\s*)?The user's email address is \S+?\.(?: Use it only[^\n]*?explicitly asks\.)?")


def _rewrite_text(text: str, sim_date: str, stats: Dict[str, int]) -> Optional[str]:
    """The rewritten text, or None when the whole block/message should go."""
    if text.startswith(BATCHING_REMINDER):
        stats["batching_dropped"] += 1
        return None
    if "# userEmail" in text:
        text = EMAIL_SECTION.sub("", text)
        stats["email_cut"] += 1
        if CONTEXT_HEAD in text and not re.search(r"^# \w", text, re.M):
            stats["context_dropped"] += 1
            return None
    if "The date has changed." in text:
        stats["date_change_dropped"] += 1
        return None
    new = DATE_LINE.sub(f"Today's date is {sim_date}.", text)
    if new != text:
        stats["date_set"] += 1
    return new


def _rewrite_nested(content: Any, sim_date: str, stats: Dict[str, int]) -> Any:
    """A tool_result's content. Claude Code can attach a reminder *inside* a tool result
    when it re-announces context mid-turn (5.e.xi opus5cli s0, 2026-09-14: the session
    context came back at 09:32 inside Rafael's turn and reached 22 requests before this
    existed). The result itself must survive, so only the reminder wrappers are edited."""
    def wrappers(text: str) -> str:
        def one(m: "re.Match[str]") -> str:
            new = _rewrite_text(m.group(0), sim_date, stats)
            return "" if new is None else new
        return REMINDER.sub(one, text)

    if isinstance(content, str):
        return wrappers(content)
    if isinstance(content, list):
        kept = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = wrappers(block.get("text") or "")
                if not text.strip():
                    continue  # the block was nothing but an injected reminder
                block = {**block, "text": text}
            kept.append(block)
        return kept or [{"type": "text", "text": "(no output)"}]
    return content


def load_redactions() -> Dict[str, str]:
    """Literal strings that must never reach the model, with their replacements: the
    logged-in account's email and full name (from ~/.claude.json) and the real home path
    (a compaction summary quotes the transcript path under it)."""
    out: Dict[str, str] = {str(Path.home()): "~"}
    try:
        acct = json.loads((Path.home() / ".claude.json").read_text()).get("oauthAccount") or {}
    except (OSError, json.JSONDecodeError):
        acct = {}
    for key in ("emailAddress", "fullName"):
        value = str(acct.get(key) or "").strip()
        if len(value) >= 4:
            out[value] = ""
    return out


def scrub(obj: Any, redactions: Dict[str, str], stats: Dict[str, int]) -> Any:
    """Backstop after the structured rewrite: every string at any depth loses the email
    sentence (whatever address it names) and every redaction literal. Catches shapes the
    structured pass does not know, e.g. the context re-attached after a compaction."""
    if isinstance(obj, str):
        new = EMAIL_SENTENCE.sub("", obj)
        for literal, repl in redactions.items():
            new = new.replace(literal, repl)
        if new != obj:
            stats["scrubbed"] = stats.get("scrubbed", 0) + 1
        return new
    if isinstance(obj, list):
        return [scrub(x, redactions, stats) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, redactions, stats) for k, v in obj.items()}
    return obj


def rewrite_body(body: Dict[str, Any], sim_date: str) -> Dict[str, int]:
    """In place. Claude Code sends the same reminder as a list of text blocks on the first
    request of a turn and as one string on the later ones, in ``user`` and in
    mid-conversation ``system`` messages alike, and sometimes nested in a tool_result;
    all three shapes are handled."""
    stats = {"email_cut": 0, "context_dropped": 0, "date_set": 0, "date_change_dropped": 0,
             "batching_dropped": 0}
    messages: List[Dict[str, Any]] = []
    for msg in body.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            new = _rewrite_text(content, sim_date, stats)
            if new is None or not new.strip():
                continue  # a message is only ever dropped whole when it was all injection
            messages.append({**msg, "content": new})
            continue
        if not isinstance(content, list):
            messages.append(msg)
            continue
        kept: List[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                new = _rewrite_text(block.get("text") or "", sim_date, stats)
                if new is None:
                    continue
                block = {**block, "text": new}
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                block = {**block, "content": _rewrite_nested(block.get("content"), sim_date, stats)}
            kept.append(block)
        if kept:
            messages.append({**msg, "content": kept})
    body["messages"] = messages
    return stats


def make_app(out: Path, sim_date: str, dump_bodies: bool = False) -> Starlette:
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=30))
    log = (out / "rewrites.jsonl").open("a")
    real_date = date.today().isoformat()
    dump = (out / "claude_requests.jsonl").open("a") if dump_bodies else None
    redactions = load_redactions()

    async def proxy(request: Request) -> Response:
        path = request.url.path
        if path == "/":
            return Response("ok")  # the runner's readiness probe; never forwarded
        agent = ""
        if path.startswith("/a/"):
            _, _, agent, rest = path.split("/", 3)
            path = "/" + rest
        body = await request.body()
        entry: Dict[str, Any] = {"wall": time.time(), "agent": agent, "path": path}
        if request.method == "POST" and path.startswith("/v1/messages") and body:
            try:
                data = json.loads(body)
                entry.update(rewrite_body(data, sim_date))
                for key in ("messages", "system"):
                    if key in data:
                        data[key] = scrub(data[key], redactions, entry)
                body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
                flat = json.dumps([data.get("messages"), data.get("system")], ensure_ascii=False)
                entry["leak_literal"] = any(lit in flat for lit, repl in redactions.items() if repl == "")
                entry["leak"] = {"real_date": any(d != sim_date for d in DATE_ANY.findall(flat))
                                              or real_date in flat,
                                 "batching": BATCHING_REMINDER in flat,
                                 "email": bool(EMAIL_ANY.search(flat.replace("noreply@anthropic.com", "")))}
            except (json.JSONDecodeError, ValueError) as exc:
                entry["rewrite_error"] = str(exc)
        if dump is not None and request.method == "POST" and path.startswith("/v1/messages"):
            dump.write(json.dumps({"agent": agent, "wall": entry["wall"],
                                   "body": json.loads(body)}, ensure_ascii=False) + "\n")
            dump.flush()
        log.write(json.dumps(entry) + "\n")
        log.flush()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        url = UPSTREAM + path + (f"?{request.url.query}" if request.url.query else "")
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=body), stream=True)
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=resp_headers, background=BackgroundTask(upstream.aclose))

    return Starlette(routes=[Route("/{path:path}", proxy,
                                   methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--date", required=True, help="simulated date, YYYY-MM-DD")
    ap.add_argument("--dump", action="store_true", help="write every rewritten request body")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    uvicorn.run(make_app(out, args.date, args.dump), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
