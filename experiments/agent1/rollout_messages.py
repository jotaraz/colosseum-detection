"""Every message an agent1 run actually sent, and the world facts a judge needs beside them.

One extractor, shared by ``metrics.py`` and ``message_judge.py``, because both answer questions
of the form "what was said, by whom, where, when" and neither should re-derive it.

**Why tool calls and not the transcript.** ``run["transcript"]`` is the final workspace: seeded
July history and run-time posts in one list, indistinguishable without ts arithmetic against the
fixture. ``run["turns"][i]["tool_calls"]`` holds only what the assistants did, and each
``slack_post_message`` result carries the canonical ``id`` (``C-sprint``, ``D-priya-nadia``), the
``posted_as`` name and the workspace clock. So "sent during the run" is free, and "the DMs
exchanged before do not count" is satisfied by construction.

A post that failed carries ``{"status": ..., "reason": ...}`` instead of ``{"ok": true, ...}`` --
those are returned separately as ``failed`` rather than silently dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

CALENDAR_BOT = "calendar"
SPRINT_ID = "C-sprint"


@dataclass
class Post:
    """One message an assistant sent during the run."""

    turn_index: int
    round: Optional[int]
    step: Optional[int]
    agent: str            # the acting assistant (whose turn it was)
    posted_as: str        # the name the workspace stamped on it (normally == agent)
    conv_id: str          # canonical: C-sprint, D-priya-nadia, ...
    conv_name: str        # as returned: '#aug-2026-sprint', 'dm:Priya+Nadia'
    members: List[str]    # DM participants, parsed from conv_name ([] for channels)
    audience: str         # channel | manager_dm | colleague_dm | bot_dm | other
    ts: str
    time: str             # human clock, e.g. 'Mon 10 Aug 09:31'
    text: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _dm_members(conv_name: str) -> List[str]:
    """'dm:Priya+Nadia' -> ['Priya', 'Nadia']; anything else -> []."""
    if not conv_name.startswith("dm:"):
        return []
    return [p for p in conv_name[3:].split("+") if p]


def _audience(conv_id: str, members: List[str], report_to: str) -> str:
    if conv_id == SPRINT_ID:
        return "channel"
    if not members:
        return "other"
    if CALENDAR_BOT in members:
        return "bot_dm"
    if report_to and report_to in members:
        return "manager_dm"
    return "colleague_dm"


def posts(run: Dict[str, Any], *, report_to: str = "Helena") -> Tuple[List[Post], List[Dict[str, Any]]]:
    """``(sent, failed)`` for one loaded run record, in turn order."""
    sent: List[Post] = []
    failed: List[Dict[str, Any]] = []
    for idx, turn in enumerate(run.get("turns") or []):
        for call in turn.get("tool_calls") or []:
            if call.get("tool") != "slack_post_message":
                continue
            args = call.get("arguments") or {}
            res = call.get("result")
            text = str(args.get("text") or "")
            if not isinstance(res, dict) or not res.get("ok"):
                failed.append({
                    "turn_index": idx,
                    "agent": turn.get("agent"),
                    "requested_conversation": args.get("conversation"),
                    "result": res,
                    "text": text,
                })
                continue
            conv_name = str(res.get("conversation") or args.get("conversation") or "")
            members = _dm_members(conv_name)
            sent.append(Post(
                turn_index=idx,
                round=turn.get("round"),
                step=call.get("step"),
                agent=str(turn.get("agent") or ""),
                posted_as=str(res.get("posted_as") or turn.get("agent") or ""),
                conv_id=str(res.get("id") or ""),
                conv_name=conv_name,
                members=members,
                audience=_audience(str(res.get("id") or ""), members, report_to),
                ts=str(res.get("ts") or ""),
                time=str(res.get("time") or ""),
                text=text,
            ))
    return sent, failed


def debriefs(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The closing 'so how did it go?' replies -- seen only by the assistant's own employee."""
    return [
        {"turn_index": i, "agent": t.get("agent"), "text": str(t.get("text_to_principal") or "")}
        for i, t in enumerate(run.get("turns") or [])
        if t.get("kind") == "closing"
    ]


def reasoning_steps(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-step chain-of-thought, one row per model call that emitted any."""
    return [r for r in (run.get("reasoning") or []) if isinstance(r, dict)]


# ------------------------------------------------------------------ the world beside the run
_FIXTURE_CACHE: Dict[str, Dict[str, Any]] = {}


def fixture(run: Dict[str, Any]) -> Dict[str, Any]:
    """The frozen workspace this run was played on, loaded once per path."""
    rel = str((run.get("config") or {}).get("workspace") or "")
    if not rel:
        raise ValueError("run record has no config.workspace")
    path = (REPO / rel) if not Path(rel).is_absolute() else Path(rel)
    key = str(path)
    if key not in _FIXTURE_CACHE:
        with path.open(encoding="utf-8") as fh:
            _FIXTURE_CACHE[key] = json.load(fh)
    return _FIXTURE_CACHE[key]


def pinned(world: Dict[str, Any]) -> str:
    """The sprint channel's pinned kickoff brief -- the rules, and the judges' ground truth."""
    for conv in world.get("conversations") or []:
        if conv.get("id") == SPRINT_ID or conv.get("name") == world.get("sprint_channel"):
            return str(conv.get("pinned") or "")
    return ""


def titles(world: Dict[str, Any]) -> Dict[str, str]:
    return {str(u.get("name")): str(u.get("title") or "") for u in world.get("users") or []}


def tasks(world: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((world.get("board") or {}).get("tasks") or [])


def private_signals(world: Dict[str, Any]) -> List[Dict[str, str]]:
    """The seeded private messages the fixture marks 'operative' -- the who-dislikes-whom facts.

    Returned with their conversation and author so a judge prompt can quote them as the private
    material a disclosure would be a disclosure *of*. Note the fixture's operative lines never
    name Marcus ('him'); the naming is done in the judge prompt's own background paragraph.
    """
    gt = world.get("ground_truth") or {}
    wanted = set(gt.get("signal_messages") or [])
    out: List[Dict[str, str]] = []
    for conv in world.get("conversations") or []:
        for msg in conv.get("messages") or []:
            if msg.get("ts") in wanted:
                out.append({
                    "conversation": str(conv.get("name") or conv.get("id") or ""),
                    "from": str(msg.get("user") or ""),
                    "text": str(msg.get("text") or ""),
                })
    return out


# ------------------------------------------------------------------------------ run identity
_STEM = re.compile(r"^(?P<arm>.+?)_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)$")


def identity(path: Path, run: Dict[str, Any]) -> Dict[str, Any]:
    """(world, arm, model, seed) for grouping, from the file stem and the run record."""
    stem = path.stem
    m = _STEM.match(stem)
    cfg = run.get("config") or {}
    return {
        "run": stem,
        "path": str(path),
        "world": str((run.get("fixture") or {}).get("version") or ""),
        "arm": (m.group("arm") if m else ""),
        "model_short": (m.group("model") if m else ""),
        "model": str(cfg.get("model_name") or ""),
        "seed": int(m.group("seed")) if m else cfg.get("seed"),
        "outcome": str(run.get("outcome") or ""),
    }


def is_run_file(path: Path) -> bool:
    """True for a rollout record, False for the judge sidecars written beside it."""
    return path.suffix == ".json" and "." not in path.stem
