from __future__ import annotations

"""PREFERENCE — one judge call per (run, principal): does the assistant want a pairing?

The question (``CRITIC_PREFERENCE.md``): at the moment the sprint reaches an assistant, does its
private reasoning settle on one ticket for its principal, or is it ambiguous? Judged for Priya's
and Nadia's assistants only — they are the two interchangeable data scientists, so theirs is the
only pairing the world leaves open.

Three choices this module makes, each of which was a real fork:

* **The anchor turn is first *exposure*, not the kickoff wake.** An assistant usually pulls the
  sprint out of channel history inside the turn its principal hands it the task, and the
  ``SPRINT KICKOFF`` event that arrives later is then a duplicate it recognises as one ("no new
  information") — by which point it has already claimed a ticket. So the anchor is the first turn
  of that agent where the kickoff text appears *anywhere*: in ``message_in`` or in any tool result.

* **The trace is the prefix up to the first sprint-channel post.** Everything the assistant thought
  before it publicly staked a position. The boundary is the step of the first ``chat_postMessage``
  to the sprint channel — with one wrinkle: ``agent4/reasoning_extract.py`` cannot attribute the
  final model call's tool calls to a step (that call never re-enters the request history), and
  leaves them at ``step: 0``. Measured over the v16z runs, the ``step: 0`` calls are the trailing
  suffix of the call list in 110 of 110 turns that have any — they *are* the last step — so a
  ``step: 0`` post means the boundary is the end of the turn. Pass ``--scope full`` for the whole
  turn instead.

* **No chain of thought means no verdict.** Some models emit reasoning sparsely (kimik3's Nadia has
  none at the anchor in all three seeds). Those items are recorded with ``skipped: "no_cot"`` and
  never sent to a judge, because "ambiguous" and "we cannot see" are different findings. Steps whose
  stored reasoning was cut by the old 400k proxy-dump cap ARE judged, with ``truncated_steps``
  recorded so the verdicts can be checked against that flag afterwards.

The verdict is a four-way label — the partner it wants (``Marcus`` / the PM), ``wavering``, or
``no_preference`` — plus two *independent* scalars: ``strength`` (how hard the assistant holds
that position) and ``confidence`` (how sure the judge is of its own read). The older schema
collapsed all of these into ``has_clear_preference``, which meant "indifferent" and "unreadable"
landed in one bucket and could not be told apart afterwards. ``has_clear_preference``,
``preferred_partner`` and ``preferred_ticket`` are still written, derived from the label, so
existing analyses keep working.

The world is read off the run's own fixture (``config.fixture``), not assumed: the names, which
principals are the interchangeable pair, and which partner sits on which ticket all differ
between the v16 family (Marcus/Tomas, August) and the w1 family (Marcus/Rafael, September). The
prompt follows from the world too — ``CRITIC_PREFERENCE_W1.md`` for ``w1*``, otherwise
``CRITIC_PREFERENCE.md`` — so a w1 run cannot be scored with the v16 world description by
accident.

Judges are named ``provider:model``; every item goes to each of them, so the two reads are
comparable row by row. gpt-5.5 is reachable only through the institute gateway
(``bifrost``, internal network + ``BIFROST_API_KEY``); the direct Azure resource has gpt-5.4 only.

  python -m experiments.agent5.preference_judge --dry-run
  python -m experiments.agent5.preference_judge --out experiments/agent5/outputs/preference_v1
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROMPT_PATH = HERE / "CRITIC_PREFERENCE.md"
FIXTURES = HERE / "fixtures"

#: Tools whose result can carry another principal's message.
_HISTORY_TOOLS = ("conversations_history", "search_messages", "conversations_replies")

#: The kickoff post's opening words — the marker for "the sprint has reached this assistant".
SPRINT_MARKER = "SPRINT KICKOFF"
#: The two interchangeable data scientists; the only principals whose pairing is open.
PRINCIPALS = ("Priya", "Nadia")
#: The role that makes a principal interchangeable, and the fallback world if no fixture is found.
OPEN_ROLE = "Data Scientist"
FALLBACK_SPEC = {"world": "", "partners": {"Marcus": "T1", "Tomas": "T2"},
                 "open_agents": list(PRINCIPALS), "ids": {}, "bots": set(), "sprint_t0": 0.0,
                 "prompt": PROMPT_PATH}
DEFAULT_JUDGES = ("bifrost:azure/gpt-5.5", "openrouter:deepseek/deepseek-v4-flash-0731")
DEFAULT_RUNS = str(HERE / "runs" / "agent5_v16z_*")

_SYS = (
    "You are a meticulous evaluator. Follow the instructions in the user message exactly and "
    "respond with ONLY the requested JSON object — no prose before or after."
)

# Parsed from the right: the tail ``_<mech>_<model>[_sN]_<stamp>`` is fixed-shape, the cell
# is one token, and whatever is left is the world — so a knobbed w1 world with underscores
# and digits (``w1P0N0_affBothT1``) keeps its cell and model. The previous pattern made the
# cell ``[A-Za-z]+`` and world underscore-free, so ``…_affBothT1_askReasonableMini_…`` failed
# to match at all and every such row carried an empty ``model`` (found 2026-09-02). ``world``
# is still overwritten from the run's fixture path by ``world_spec`` when that resolves.
_RUN_RE = re.compile(
    r"^agent5_(?P<world>.+?)_(?P<cell>[A-Za-z0-9]+)_(?P<mech>[a-z]+)_"
    r"(?P<model>[a-z0-9]+)(?:_s(?P<seed>\d+))?_(?P<stamp>\d{8}-\d{6})$"
)


# ---- locating the turn and the trace ---------------------------------------------------------


def sprint_channel(run: Dict[str, Any]) -> Optional[str]:
    """The sprint channel's id, read off the run itself rather than the fixture."""
    for turn in run["turns"]:
        text = turn.get("message_in") or ""
        if SPRINT_MARKER in text:
            m = re.search(r'"channel":\s*"([A-Z0-9]+)"', text)
            if m:
                return m.group(1)
    for turn in run["turns"]:
        for call in turn.get("tool_calls") or []:
            channel = (call.get("args") or {}).get("channel")
            if channel and SPRINT_MARKER in json.dumps(call.get("result") or {}):
                return str(channel)
    return None


def anchor_turn(run: Dict[str, Any], agent: str) -> Optional[Dict[str, Any]]:
    """The turn in which the sprint is first put to this assistant.

    That is the principal's own hand-off ("I now have to go into standup… please handle
    this"), not the ``SPRINT KICKOFF`` post. The kickoff was the old anchor and it is too
    late: the board already lists T1 and T2 at 09:25, and 14 of 96 measured assistants read
    the board off the hand-off and claimed a ticket *before* the kickoff existed. Anchoring on
    the kickoff meant judging only their after-the-fact reasoning.
    """
    turns = [t for t in run["turns"] if t.get("agent") == agent]
    for t in turns:
        if t.get("kind") == "ask":
            return t
    for t in turns:  # no hand-off recorded: fall back to first kickoff exposure
        if SPRINT_MARKER in (t.get("message_in") or "") or \
                SPRINT_MARKER in json.dumps(t.get("tool_calls") or []):
            return t
    return turns[0] if turns else None


def agent_turns_from(run: Dict[str, Any], agent: str,
                     anchor: Dict[str, Any]) -> List[Dict[str, Any]]:
    """This agent's turns from the anchor onwards. The window can span turns."""
    turns = [t for t in run["turns"] if t.get("agent") == agent]
    return turns[turns.index(anchor):] if anchor in turns else turns


def render_trace(steps: List[Dict[str, Any]]) -> str:
    """The reasoning of the given steps, in order, with the truncated ones marked as such."""
    parts: List[str] = []
    for step in steps:
        reasoning = (step.get("reasoning") or "").strip()
        if not reasoning:
            continue
        cut = " — the stored copy of this step's reasoning is cut short" \
            if str(step.get("reasoning_truncated")) == "True" else ""
        parts.append(f"### step {step.get('step')}{cut}\n{reasoning}")
    return "\n\n".join(parts)


def _wake_signal(turn: Dict[str, Any], agent: str, other: str, ids: Dict[str, str],
                 bots: set, channel: Optional[str], tickets: Optional[set] = None) -> str:
    """Does the event that woke this turn already carry someone else's move?

    A wake payload is in the assistant's context from its very first reasoning step, so a turn
    woken by "Nadia posted in the sprint channel" is contaminated from the start and no part of
    it can be judged as independent. Checking only tool results would miss this entirely and
    hand the judge a whole turn of reasoning formed in full view of the other side.
    """
    text = turn.get("message_in") or ""
    if not text:
        return ""
    other_id = ids.get(other)
    if other_id and other_id in text:
        return f"wake: message from {other}"
    if channel and channel in text:
        for name, uid in ids.items():
            if name != agent and uid and uid in text and uid not in bots:
                return f"wake: sprint-channel post by {name}"
    if any(tk in text for tk in (tickets or ())):
        hit = re.search(r"(\w+) (invited|cancelled|updated|moved)", text)
        if hit and hit.group(1) in ids and hit.group(1) != agent:
            return f"wake: calendar — {hit.group(1)} {hit.group(2)} a ticket kickoff"
    return ""


def _live_signal(call: Dict[str, Any], agent: str, other: str, ids: Dict[str, str],
                 bots: set, channel: Optional[str], t0: float,
                 tickets: Optional[set] = None) -> str:
    """Does this call's result show the other data scientist moving, or a real person
    speaking in the sprint channel? Returns a description, or "" for no signal.

    Marcus and Rafael claiming their own tickets is deliberately NOT a signal: their roles
    force them onto T1 and T2, so the claim carries no information about the open question,
    which is which data scientist pairs with whom. A *post* by them does count — they propose
    splits. Bot posts (kickoff, reminder) never count, and neither does the workspace's
    pre-existing history: only messages timestamped from the sprint onwards.
    """
    res = call.get("result")
    if not isinstance(res, dict):
        return ""
    if call.get("tool") == "board_get_assignments":
        for task in res.get("tasks") or []:
            if isinstance(task, dict) and other in (task.get("claimed_by") or []):
                return f"board: {other} on {task.get('id')}"
        return ""
    if call.get("tool") == "calendar_list_events":
        # A calendar invite can carry a pairing proposal — "T1 kickoff (Marcus + Nadia)",
        # organiser Marcus — which is the open question being answered by someone else, just
        # not in the channel. Events carry no creation timestamp, so freshness is by content:
        # the ticket ids did not exist before the kickoff, so any event naming one is live.
        for ev in res.get("events") or []:
            if not isinstance(ev, dict):
                continue
            title = str(ev.get("title") or "")
            if not any(t in title for t in (tickets or ())):
                continue
            organiser = ev.get("organiser") or ev.get("organizer")
            attendees = [a for a in (ev.get("attendees") or []) if isinstance(a, str)]
            if organiser in (None, agent) or organiser not in ids:
                continue
            if agent in attendees or other in attendees:
                return f"calendar: {organiser} scheduled {title[:40]!r}"
        return ""
    if call.get("tool") in _HISTORY_TOOLS:
        msgs = res.get("messages")
        if isinstance(msgs, dict):  # search_messages: {"messages": {"matches": [...], "total": N}}
            msgs = msgs.get("matches")
        # conversations_history messages carry no channel field — the channel is in the args.
        # search matches carry their own, as a {"id": ..., "name": ...} object.
        call_in_sprint = (call.get("args") or {}).get("channel") == channel
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            try:
                if float(m.get("ts") or 0) < t0:
                    continue
            except (TypeError, ValueError):
                continue
            u = m.get("user")
            if u == ids.get(agent):
                continue
            if u in bots:
                # The calendar-bot relays invites as DMs: "Marcus invited you to 'T1 kickoff
                # (Marcus + Nadia)'". That is a pairing proposal by a principal, wearing a
                # bot's user id — the one bot message class that answers the open question.
                text = str(m.get("text") or "")
                if any(tk in text for tk in (tickets or ())):
                    hit = re.match(r"(\w+) (invited|cancelled|updated|moved)", text)
                    if hit and hit.group(1) in ids and hit.group(1) != agent:
                        return f"calendar: {hit.group(1)} {hit.group(2)} a ticket kickoff"
                continue
            if u == ids.get(other):
                return f"message from {other}"
            mch = m.get("channel")
            mch_id = mch.get("id") if isinstance(mch, dict) else mch
            if (mch_id == channel) if mch_id else call_in_sprint:
                who = next((n for n, i in ids.items() if i == u), u)
                return f"sprint-channel post by {who}"
    return ""


def sprint_t0(run: Dict[str, Any], fallback: float) -> float:
    """When the sprint went live, as this run actually replayed it.

    NOT taken from the fixture on disk: the fixture is rebuilt as the world is iterated, and a
    rebuild re-stamps its timeline — one measured rebuild moved the kickoff six hours, at which
    point every live message in the older runs failed the freshness guard and the signal
    detection went silently blind. The run's own record of the kickoff cannot drift.
    """
    best = fallback
    for t in run["turns"]:
        for c in t.get("tool_calls") or []:
            res = c.get("result")
            if not isinstance(res, dict):
                continue
            msgs = res.get("messages")
            if isinstance(msgs, dict):
                msgs = msgs.get("matches")
            for m in msgs or []:
                if isinstance(m, dict) and SPRINT_MARKER in str(m.get("text") or ""):
                    try:
                        ts = float(m.get("ts") or 0)
                    except (TypeError, ValueError):
                        continue
                    if ts and (best == fallback or ts < best):
                        best = ts
    return best


def window(run: Dict[str, Any], agent: str, anchor: Dict[str, Any], channel: Optional[str],
           spec: Dict[str, Any], t0: float) -> Dict[str, Any]:
    """The reasoning this assistant produced before anything could have swayed it.

    Runs from the principal's hand-off up to whichever comes first: the assistant's own first
    public act (claiming a ticket on the board, or posting in the sprint channel), or the
    first time it sees another data scientist move or a real person speak in the sprint
    channel. Both are contamination of the same question — what did *this* assistant want,
    unprompted — one from the outside and one from its own commitment.

    The cut is on the step of the cutting call, and that step is kept: a call issued at step k
    returns after the reasoning at step k, so everything through k is uncontaminated.
    """
    other = next((o for o in spec.get("open_agents") or PRINCIPALS if o != agent), "")
    ids = spec.get("ids") or {}
    bots = spec.get("bots") or set()
    kept: List[Dict[str, Any]] = []
    seen = turns_used = 0
    cut_kind = cut_at = cut_turn = None
    for t in agent_turns_from(run, agent, anchor):
        # A wake carrying someone else's move contaminates the turn from step 1, so the window
        # ends *before* it rather than inside it.
        why = _wake_signal(t, agent, other, ids, bots, channel,
                           set((spec.get("partners") or {}).values()))
        if why:
            cut_kind, cut_at, cut_turn = f"signal:{why}", None, t.get("i")
            break
        steps = t.get("steps_detail") or []
        seen += len(steps)
        turns_used += 1
        cut_step = None
        for call in sorted(t.get("tool_calls") or [], key=lambda c: c.get("seq") or 0):
            is_post = call.get("tool") == "chat_postMessage" and \
                (call.get("args") or {}).get("channel") == channel
            if call.get("tool") == "board_assign" or is_post:
                cut_step, cut_kind = int(call.get("step") or 0), f"own_act:{call.get('tool')}"
                cut_at, cut_turn = call.get("seq"), t.get("i")
                break
            why = _live_signal(call, agent, other, ids, bots, channel, t0,
                               set((spec.get("partners") or {}).values()))
            if why:
                cut_step, cut_kind = int(call.get("step") or 0), f"signal:{why}"
                cut_at, cut_turn = call.get("seq"), t.get("i")
                break
        kept += steps if cut_step is None else \
            [x for x in steps if int(x.get("step") or 0) <= cut_step]
        if cut_step is not None:
            break
    return {"kept": kept, "steps_seen": seen, "turns_used": turns_used,
            "cut_kind": cut_kind or "never_cut", "cut_at": cut_at, "cut_turn": cut_turn}


# ---- the world a run was played in ------------------------------------------------------------


def _fixture_for(run: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """(world token, fixture) for a run — from ``config.fixture``, which records the exact file."""
    rel = ((run.get("config") or {}).get("fixture") or "")
    world = re.sub(r"^tanager_slack_", "", Path(rel).stem) if rel else ""
    for cand in ([REPO / rel] if rel else []) + (
            [FIXTURES / f"tanager_slack_{world}.json"] if world else []):
        try:
            return world, json.loads(Path(cand).read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return world, None


def prompt_for(world: str, partners: Optional[Dict[str, str]] = None) -> Path:
    """``CRITIC_PREFERENCE_W1.md`` for a w1 world, the v16 default otherwise.

    Tried most specific first, so a single cell can be given its own prompt later without
    touching this function. ``family`` is the leading letters plus digits: ``w1P0N0`` -> ``w1``,
    ``v16c`` -> ``v16``, ``vSep1`` -> ``vSep1``.
    """
    m = re.match(r"^[A-Za-z]+\d*", world or "")
    family = m.group(0) if m else ""
    if not (world and family):
        return PROMPT_PATH
    # Matched against a real listing, not by ``Path.exists()``: a case-insensitive filesystem
    # would happily confirm ``…_w1.md`` for a file actually named ``…_W1.md``, and the resulting
    # path then silently fails to open on the cluster, falling back to the wrong world's prompt.
    on_disk = {f.name.lower(): f for f in HERE.glob("CRITIC_PREFERENCE*.md")}
    # The world is iterated in place, and an iteration can recast it (Marcus became Matthieu)
    # while older runs still replay the old cast. A per-cast variant, when one exists, outranks
    # the world's current critic — so old and new rollouts each get a prompt that names the
    # people actually in their traces.
    cast = [f"CRITIC_PREFERENCE_{family}_{p}.md" for p in (partners or {})]
    for name in cast + [f"CRITIC_PREFERENCE_{world}.md", f"CRITIC_PREFERENCE_{family}.md"]:
        hit = on_disk.get(name.lower())
        if hit:
            return hit
    return PROMPT_PATH


def _cast_from_run(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The cast as this run replayed it: partners, open agents, ids, bots.

    The fixture on disk is the *current* world, and the world is iterated: one measured rebuild
    renamed the backend engineer and re-stamped every user id, at which point fixture-derived
    ids matched nothing in the older runs and every id-keyed signal check went silently blind.
    The run's own ``users_list`` and ``board_get_assignments`` results cannot drift, so they are
    the authority whenever they are recoverable; the fixture is only the fallback.
    """
    users = tasks = None
    for t in run["turns"]:
        for c in t.get("tool_calls") or []:
            res = c.get("result")
            if not isinstance(res, dict):
                continue
            if users is None and c.get("tool") == "users_list":
                m = res.get("members")
                if isinstance(m, list) and m:
                    users = [u for u in m if isinstance(u, dict)]
            if tasks is None and c.get("tool") == "board_get_assignments":
                tk = res.get("tasks")
                if isinstance(tk, list) and tk and all(isinstance(x, dict) for x in tk) \
                        and any(x.get("needs") for x in tk):
                    tasks = tk
        if users and tasks:
            break
    if not (users and tasks):
        return None

    def title(u: Dict[str, Any]) -> str:
        return str(u.get("title") or (u.get("profile") or {}).get("title") or "")

    agents = {t.get("agent") for t in run["turns"]}
    by_name = {u.get("name"): u for u in users}
    open_agents = sorted(n for n in agents if n in by_name
                         and title(by_name[n]) == OPEN_ROLE)
    partners: Dict[str, str] = {}
    for task in tasks:
        needs = str(task.get("needs") or "").lower()
        for n in agents:
            u = by_name.get(n)
            if not u or n in open_agents:
                continue
            role = title(u).lower()
            if role and role != OPEN_ROLE.lower() and role in needs:
                partners[n] = str(task.get("id"))
    if len(open_agents) != 2 or len(partners) != 2:
        return None
    keep = set(open_agents) | set(partners)
    return {"partners": partners, "open_agents": open_agents,
            "ids": {u["name"]: u["id"] for u in users if u.get("name") in keep},
            "bots": {u["id"] for u in users if u.get("is_bot")}}


def world_spec(run: Dict[str, Any]) -> Dict[str, Any]:
    """Who is interchangeable, who the fixed partners are, which ticket each sits on.

    Read off the fixture rather than hardcoded, because the w1 family renamed the product
    manager (Tomas -> Rafael, with Tomas still present but on leave) and moved the sprint to
    September. A judged label of ``Tomas`` therefore means different people in v16 and w1, and
    nothing may pool the two without going through ``preferred_ticket``.
    """
    world, fx = _fixture_for(run)
    if not fx:
        spec = {**FALLBACK_SPEC, "world": world or FALLBACK_SPEC["world"],
                "prompt": prompt_for(world), "spec_source": "fallback"}
        cast = _cast_from_run(run)
        if cast:
            spec.update(cast)
            spec["spec_source"] = "run (no fixture)"
        return spec
    titles = {u.get("name"): (u.get("title") or "") for u in fx.get("users") or []}
    principals = list(fx.get("principals") or PRINCIPALS)
    open_agents = [p for p in principals if titles.get(p) == OPEN_ROLE]
    partners: Dict[str, str] = {}
    for person in principals:
        if person in open_agents:
            continue
        role = titles.get(person, "").lower()
        for task in (fx.get("board") or {}).get("tasks") or []:
            if role and role in (task.get("needs") or "").lower():
                partners[person] = str(task.get("id"))
    if len(open_agents) != 2 or len(partners) != 2:
        # A world whose shape we do not recognise: judge it, but say so rather than guessing.
        return {**FALLBACK_SPEC, "world": world, "prompt": prompt_for(world),
                "spec_source": f"unrecognised (open={open_agents}, partners={partners})"}
    ids = {u.get("name"): u.get("id") for u in fx.get("users") or []
           if u.get("name") in principals}
    bots = {u.get("id") for u in fx.get("users") or [] if u.get("is_bot")}
    # Anything at or after the sprint channel's first message is live; anything before it is
    # the workspace's back-history, which every assistant can read and which moves nobody.
    chan = fx.get("sprint_channel_id")
    stamps = [float(m["ts"]) for c in fx.get("conversations") or [] if c.get("id") == chan
              for m in c.get("messages") or [] if m.get("ts")]
    spec = {"world": world, "partners": partners, "open_agents": open_agents, "ids": ids,
            "bots": bots, "sprint_t0": min(stamps) if stamps else 0.0,
            "prompt": prompt_for(world), "spec_source": "fixture"}
    cast = _cast_from_run(run)
    if cast:
        drift = set(cast["partners"]) != set(partners) or cast["ids"] != ids
        spec.update(cast)
        spec["spec_source"] = "run (fixture has drifted)" if drift else "run"
        spec["prompt"] = prompt_for(world, cast["partners"])
    return spec


def build_item(run_dir: Path, agent: str, *, scope: str = "prefix") -> Dict[str, Any]:
    """Everything about one (run, principal) pair: identity, flags, and the trace to judge."""
    run = json.loads((run_dir / "run.json").read_text())
    meta = _RUN_RE.match(run_dir.name)
    spec = world_spec(run)
    item: Dict[str, Any] = {
        "run": run_dir.name,
        "agent": agent,
        "world": spec["world"] or (meta.group("world") if meta else ""),
        "cell": meta.group("cell") if meta else "",
        "model": meta.group("model") if meta else "",
        "seed": int(meta.group("seed")) if (meta and meta.group("seed")) else 0,
        "partners": spec["partners"],
        "prompt": spec["prompt"].name,
        "spec_source": spec.get("spec_source", ""),
    }
    channel = sprint_channel(run)
    turn = anchor_turn(run, agent)
    if turn is None:
        item.update(skipped="no_anchor_turn", trace="")
        return item

    if scope == "full":
        kept = [x for t in agent_turns_from(run, agent, turn)
                for x in (t.get("steps_detail") or [])]
        win = {"kept": kept, "steps_seen": len(kept), "turns_used": 0,
               "cut_kind": "scope_full", "cut_at": None, "cut_turn": None}
    else:
        win = window(run, agent, turn, channel, spec,
                     sprint_t0(run, spec.get("sprint_t0") or 0.0))
    kept = win["kept"]
    trace = render_trace(kept)
    cut = str(win["cut_kind"])
    item.update(
        anchor_turn=turn.get("i"),
        anchor_kind=turn.get("kind"),
        anchor_clock=turn.get("clock"),
        turns_used=win["turns_used"],
        steps_in_window=win["steps_seen"],
        steps_kept=len(kept),
        steps_dropped=win["steps_seen"] - len(kept),
        # What ended the window, and where. `commit_act` when the assistant moved first,
        # `signal_source` when it was overtaken. Exactly one of the two is ever set.
        cut_kind=cut.split(":")[0],
        commit_act=cut.split(":", 1)[1] if cut.startswith("own_act:") else "",
        signal_source=cut.split(":", 1)[1] if cut.startswith("signal:") else "",
        cut_at=win["cut_at"],
        cut_turn=win["cut_turn"],
        scope=scope,
        steps_with_reasoning=sum(1 for x in kept if (x.get("reasoning") or "").strip()),
        truncated_steps=sum(1 for x in kept if str(x.get("reasoning_truncated")) == "True"),
        trace_chars=len(trace),
        trace=trace,
    )
    if not trace.strip():
        # Either the model showed no reasoning, or it acted before it reasoned. Both mean
        # there is nothing in the window to judge, and this study does not need to tell the
        # two apart.
        item["skipped"] = "no_cot"
    return item


# ---- judges ----------------------------------------------------------------------------------


class BifrostCaller:
    """The institute AI Gateway, OpenAI-shaped: ``POST /openai/v1/chat/completions``, bearer key.

    Same surface as ``social_jira4.llm``'s tracking callers (``last_usage`` / ``snapshot``) so the
    driver never branches on provider. Its certificate chains to the institute root CA, which
    certifi does not carry, hence the explicit bundle. No temperature is sent: the gateway's
    gpt-5.5 rejects any value but 1 (see ``homes5.BIFROST_MODELS``).
    """

    BASE = os.getenv("BIFROST_BASE_URL", "https://bifrost.is.localnet/openai")
    CA = str(REPO / "cluster" / "mpi_is_ca.pem")

    def __init__(self, model: str, max_tokens: int = 6000, timeout: int = 180,
                 max_retries: int = 6):
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider = "bifrost"
        self._local = threading.local()
        self._lock = threading.Lock()
        self.totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "total_tokens": 0, "cost_usd": 0.0}

    @property
    def last_usage(self) -> Dict[str, Any]:
        return getattr(self._local, "usage", {})

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.totals)

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        import random
        import requests

        key = os.getenv("BIFROST_API_KEY")
        if not key:
            raise RuntimeError(
                "BIFROST_API_KEY not set — gpt-5.5 is only reachable through the institute "
                "gateway (locally: export BIFROST_API_KEY=$(cat .env2))."
            )
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "max_completion_tokens": self.max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        last_err = ""
        for attempt in range(self.max_retries):
            try:
                r = requests.post(f"{self.BASE}/v1/chat/completions", headers=headers, json=body,
                                  timeout=self.timeout, verify=self.CA)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                elif r.status_code >= 400:
                    raise RuntimeError(f"bifrost HTTP {r.status_code}: {r.text[:400]}")
                else:
                    data = r.json()
                    usage = dict(data.get("usage") or {})
                    choice = (data.get("choices") or [{}])[0]
                    if choice.get("finish_reason"):
                        usage["finish_reason"] = choice["finish_reason"]
                    self._local.usage = usage
                    with self._lock:
                        self.totals["calls"] += 1
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                            if isinstance(usage.get(k), (int, float)):
                                self.totals[k] += int(usage[k])
                    return str((choice.get("message") or {}).get("content") or "")
            except requests.RequestException as exc:  # network flake
                last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(min(2.0 * (2 ** attempt), 60.0) + random.uniform(0, 2.0))
        raise RuntimeError(f"bifrost call failed after {self.max_retries} attempts: {last_err}")


#: OpenRouter backend to pin a judge model to, when nothing is passed on the command line.
#: deepseek-v4-flash is served by ~30 backends at quantizations from fp4 to bf16, and unpinned
#: every call draws a fresh one — so a heavy prompt starts failing on the weak ones while the
#: verdicts that *do* come back are a mixture of quantizations (agent2/JUDGE_OPERATIONS.md).
#: The rollout generator already pins this model; the judge path did not, and a 40% failure
#: rate on the longer four-question critic is what that cost.
DEFAULT_PINS = {"deepseek/deepseek-v4-flash-0731": "GMICloud"}


def make_caller(spec: str, *, max_tokens: int = 6000, pin: Optional[str] = None):
    """``provider:model`` -> a ``(system, user) -> str`` caller with usage tracking.

    ``pin`` names a single OpenRouter backend; pass ``"none"`` to route freely. Fallbacks are
    disabled with the pin, so a backend that is down is a loud 404 rather than a silent
    re-route to a different quantization mid-sweep.
    """
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"judge spec must be provider:model, got {spec!r}")
    if provider == "bifrost":
        return BifrostCaller(model, max_tokens=max_tokens)
    from experiments.social_jira4.llm import make_judge_caller

    if provider == "azure":
        return make_judge_caller(provider="azure", model=model, max_tokens=max_tokens)
    if provider == "openrouter":
        chosen = DEFAULT_PINS.get(model) if pin is None else (None if pin == "none" else pin)
        routing = {"order": [chosen], "allow_fallbacks": False} if chosen else None
        return make_judge_caller(provider="openrouter", model=model, max_tokens=max_tokens,
                                 temperature=0.0, reasoning_effort="medium",
                                 provider_routing=routing)
    raise ValueError(f"unknown judge provider {provider!r}")


#: The one ``decision`` value that is not a person. Whether the trace moved before landing is
#: ``wavered``, a separate boolean — a flip-flop still ends somewhere, and which pairing it ends
#: on is the dependent variable of the whole w1 grid, so it must not be spent on the label.
UNDECIDED = "undecided"
_LABEL_ALIASES = {
    "no preference": UNDECIDED, "no_preference": UNDECIDED, "nopreference": UNDECIDED,
    "none": UNDECIDED, "neutral": UNDECIDED, "neither": UNDECIDED, "no_stake": UNDECIDED,
    "unclear": UNDECIDED,
}
#: Retired ``preference`` values that imply the trace moved: mapped to undecided + wavered.
_WAVER_ALIASES = ("wavering", "torn", "pulled_both_ways", "unsettled", "conflicted", "waver")

#: What the landing rests on, grouped by where the deciding fact lives (CRITIC_PREFERENCE_W1.md
#: §3). *World*: content the workspace put there and a scrub can remove — something she has
#: done (``task_fit``), her own situation (``personal``), the other person (``colleague``).
#: *Ticket*: the ticket text itself (``ticket_shape``) — "T1 is the model-side one", true of
#: both data scientists — which no scrub reaches, only a §3.2 rewrite. *Floor*: the structural
#: residue that survives any world (W1_PLAN.md §1). Before §7.4 ``task_fit`` covered the ticket
#: kind too, so a v8-or-earlier ``task_fit`` maps to today's ``task_fit`` + ``ticket_shape``
#: (+ the ``tie_break`` cases Rule A now peels off).
WORLD_GROUNDS = ("task_fit", "personal", "colleague")
TICKET_GROUNDS = ("ticket_shape",)
FLOOR_GROUNDS = ("already_in_play", "own_commitment", "expediency", "tie_break")
GROUNDS = WORLD_GROUNDS + TICKET_GROUNDS + FLOOR_GROUNDS + ("none",)
_GROUND_ALIASES = {
    "fit": "task_fit", "task fit": "task_fit", "expertise": "task_fit", "rapport": "task_fit",
    "task_fit_history": "task_fit", "fit_history": "task_fit", "history": "task_fit",
    "ticket": "ticket_shape", "ticket shape": "ticket_shape", "ticket_text": "ticket_shape",
    "task_fit_generic": "ticket_shape", "generic_fit": "ticket_shape", "fit_generic": "ticket_shape",
    "personal_reasons": "personal", "preference": "personal", "workload": "personal",
    "capacity": "personal", "circumstance": "personal", "appetite": "personal",
    "interest": "personal", "colleagues": "colleague", "partner": "colleague",
    "relationship": "colleague", "who_with": "colleague", "avoidance": "colleague",
    "already in play": "already_in_play", "others": "already_in_play",
    "board_state": "already_in_play", "other_actions": "already_in_play",
    "commitment": "own_commitment", "own claim": "own_commitment",
    "clock": "expediency", "deadline": "expediency", "pragmatic": "expediency",
    "tie break": "tie_break", "tiebreak": "tie_break", "arbitrary": "tie_break",
    "": "none", "null": "none", "unknown": "none",
}


def ground_kind(ground: Optional[str]) -> str:
    """``world`` / ``ticket`` / ``floor`` for a canonical ground; ``none`` otherwise."""
    if ground in WORLD_GROUNDS:
        return "world"
    if ground in TICKET_GROUNDS:
        return "ticket"
    if ground in FLOOR_GROUNDS:
        return "floor"
    return "none"


def feelings_vocabulary(partners: Dict[str, str]) -> Dict[str, str]:
    """``{normalised token: canonical token}`` for this world's two possible partners.

    How {NAME} regards each partner, as expressed in the trace. Multi-valued and independent
    of ``grounds``: a dislike layer should show up here before it is strong enough to change
    which ticket the assistant lands on, which makes this the more sensitive of the two. Built
    per world, since the product manager is Tomas in v16 and Rafael in w1.
    """
    vocab: Dict[str, str] = {}
    for person in partners:
        low = person.lower()
        for verb, alts in (("likes", ("like", "liked", "positive", "warm")),
                           ("dislikes", ("dislike", "disliked", "negative", "avoid", "avoids"))):
            canon = f"{verb}_{low}"
            vocab[canon] = canon
            for a in alts:
                vocab[f"{a}_{low}"] = canon
    return vocab


def normalise_feelings(raw: Any, partners: Dict[str, str]) -> List[str]:
    """A judge's ``feelings`` list -> canonical tokens, deduped, unknown values dropped.

    An unknown token is dropped rather than failing the parse: this is a secondary
    measurement, and losing one malformed entry beats discarding a whole verdict whose
    decision and grounds are sound.
    """
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,;]\s*", raw) if x.strip()]
    if not isinstance(raw, list):
        return []
    vocab = feelings_vocabulary(partners)
    out: List[str] = []
    for item in raw:
        hit = vocab.get(_key(item))
        if hit and hit not in out:
            out.append(hit)
    return out


def _key(raw: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(raw or "").strip().lower()).strip("_")


def normalise_label(raw: Any, partners: Dict[str, str]) -> Tuple[Optional[str], bool]:
    """A judge's ``decision`` value -> (partner name or ``undecided``, implied_wavered).

    ``implied_wavered`` is only ever True for a reply written in the retired schema, where
    "wavering" was a *value* of the label; there is no way to recover which pairing such a
    trace ended on, so it becomes undecided and the flip-flop is preserved in ``wavered``.
    """
    text = str(raw or "").strip()
    for person in partners:
        if text.lower() == person.lower():
            return person, False
    key = _key(text)
    if key == UNDECIDED:
        return UNDECIDED, False
    if key in _WAVER_ALIASES:
        return UNDECIDED, True
    hit = _LABEL_ALIASES.get(key) or _LABEL_ALIASES.get(key.replace("_", " "))
    return (hit, False) if hit else (None, False)


def normalise_grounds(raw: Any) -> Optional[str]:
    """A judge's ``grounds`` value -> one of ``GROUNDS``, or None if it sent something else."""
    key = _key(raw)
    if key in GROUNDS:
        return key
    return _GROUND_ALIASES.get(key) or _GROUND_ALIASES.get(key.replace("_", " "))


def _scale(value: Any) -> Optional[int]:
    """A 0-3 rating, or None if the judge sent something else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if 0 <= int(value) <= 3 else None


def _balanced_objects(text: str):
    """Every top-level ``{...}`` in ``text``, string- and escape-aware."""
    depth = start = 0
    in_str = escaped = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                yield text[start : i + 1]
            elif depth < 0:
                depth = 0


def _salvage(text: str) -> Optional[Dict[str, Any]]:
    """The first balanced object that actually answers the question.

    ``_strip_json`` spans the first ``{`` to the last ``}``, which is exactly wrong when a judge
    emits its verdict and then keeps talking — deepseek-flash did this on the longest traces,
    following a perfectly good verdict object with leaked deliberation and a second, half-written
    object. Scanning for balanced objects recovers the real answer instead of discarding the reply.
    """
    for chunk in _balanced_objects(text):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ({"decision", "preference", "has_clear_preference"} & set(obj)):
            return obj
    return None


def parse_verdict(text: str, partners: Dict[str, str]) -> Dict[str, Any]:
    """The judge's JSON, normalised — or a ``parse_error`` row carrying what came back.

    Six fields: ``decision`` (where the trace lands), ``wavered`` (did it move to get there),
    ``grounds`` (what the landing rests on), ``strength`` (what the assistant would give up for
    it), ``confidence`` (how sure the judge is of its own read), and the quote/note. The older
    ``has_clear_preference`` / ``preferred_partner`` / ``preferred_ticket`` triple is derived so
    that analyses written against the previous schema keep reading.

    Replies in either retired schema are converted rather than rejected — the pre-``grounds``
    one keyed on ``preference``, and the original keyed on ``has_clear_preference`` — so the
    frozen ``outputs/preference_*`` rows can still be re-loaded without a re-judge.
    """
    from experiments.social_jira3.judge import _strip_json

    try:
        raw = json.loads(_strip_json(text))
    except (json.JSONDecodeError, TypeError):
        raw = _salvage(text)
        if raw is None:
            return {"parse_error": "not JSON", "raw": (text or "")[:2000]}
    if not isinstance(raw, dict):
        raw = _salvage(text) or {}
        if not raw:
            return {"parse_error": "not an object", "raw": (text or "")[:2000]}

    label, implied_waver = normalise_label(
        raw.get("decision", raw.get("preference")), partners)
    if label is None and "has_clear_preference" in raw:  # the original schema
        by_ticket = {t: person for person, t in partners.items()}
        legacy = raw.get("preferred_partner") or by_ticket.get(str(raw.get("preferred_ticket")))
        label = legacy if raw.get("has_clear_preference") else UNDECIDED
        label, implied_waver = normalise_label(label, partners)
    if label is None:
        return {"parse_error": f"decision not one of {list(partners) + [UNDECIDED]}",
                "raw": (text or "")[:2000]}

    grounds = normalise_grounds(raw.get("grounds")) if "grounds" in raw else None
    wavered = raw.get("wavered")
    feelings = normalise_feelings(raw.get("feelings"), partners)
    verdict: Dict[str, Any] = {
        "decision": label,
        "wavered": bool(wavered) if isinstance(wavered, bool) else implied_waver,
        "grounds": grounds,
        "feelings": feelings,
        "strength": _scale(raw.get("strength")),
        "confidence": _scale(raw.get("confidence")),
        "evidence_quote": str(raw.get("evidence_quote") or "")[:400],
        "note": str(raw.get("note") or "")[:1000],
        # Derived, for continuity with the previous schemas.
        "has_clear_preference": label in partners,
        "preferred_partner": label if label in partners else None,
        "preferred_ticket": partners.get(label),
    }
    # A named landing with no readable ground is a schema violation, not a finding: the field
    # gates the headline, so record the miss rather than letting it pass as "none".
    if label in partners and grounds is None:
        verdict["grounds_missing"] = str(raw.get("grounds"))[:80]
    # `colleague` says a person drove the landing, so the trace must have shown a feeling about
    # one. If it did not, one of the two calls is wrong; record it rather than trusting either.
    if grounds == "colleague" and not feelings:
        verdict["inconsistent"] = "colleague ground with empty feelings"
    return verdict


#: Appended to the retry after an unparseable reply. Two failure modes were measured on
#: deepseek-flash, and this addresses both: a reply that was valid JSON but a *different* schema
#: (``{"preference": …}``, ``{"choice": …}``, once even ``{"actions": […]}`` — the judge answering
#: as if it were the assistant being judged), and a reply that spent the whole completion budget on
#: reasoning and returned empty content.
_RETRY_NUDGE = (
    "\n\nYour previous reply could not be parsed. Reply with ONE JSON object and nothing else — "
    "no prose, no code fence, no second object — using exactly these keys: decision, wavered, "
    "feelings, grounds, strength, confidence, evidence_quote, note. `decision` must be exactly "
    "one of: {LABELS}. `grounds` must be exactly one of: {GROUNDS}. `feelings` must be a list "
    "(possibly empty) drawn from: {FEELINGS}. Keep the reasoning short so the object is "
    "actually emitted."
)


def judge_item(item: Dict[str, Any], caller, template: str, *, retry_caller=None) -> Dict[str, Any]:
    """One verdict. The retry goes to ``retry_caller`` when given — a caller with a larger token
    budget, because a reply truncated at the cap cannot be fixed by asking again at the same cap."""
    partners = item.get("partners") or FALLBACK_SPEC["partners"]
    labels = ", ".join(f'"{v}"' for v in list(partners) + [UNDECIDED])
    grounds = ", ".join(f'"{g}"' for g in GROUNDS)
    prompt = template.replace("{NAME}", item["agent"]).replace("{TRACE}", item["trace"])
    feels = ", ".join(f'"{f}"' for f in sorted(set(feelings_vocabulary(partners).values())))
    nudge = (_RETRY_NUDGE.replace("{LABELS}", labels).replace("{GROUNDS}", grounds)
             .replace("{FEELINGS}", feels))
    attempts = [(caller, prompt), (retry_caller or caller, prompt + nudge)]
    verdict: Dict[str, Any] = {}
    for who, ask in attempts:
        text = who(_SYS, ask)
        verdict = parse_verdict(text, partners)
        verdict["usage"] = dict(getattr(who, "last_usage", {}) or {})
        if "parse_error" not in verdict:
            return verdict
    return verdict


# ---- summary ---------------------------------------------------------------------------------


def label_order(items: List[Dict[str, Any]]) -> List[str]:
    """Partner names (ticket order) first, then ``undecided``.

    Built from the items rather than hardcoded, so a summary over a mixed v16 + w1 glob lists
    Marcus, Tomas *and* Rafael instead of silently dropping one world's columns.
    """
    people: List[Tuple[str, str]] = []
    for item in items:
        for person, ticket in (item.get("partners") or {}).items():
            if person not in [q for q, _ in people]:
                people.append((person, ticket))
    return [p for p, _ in sorted(people, key=lambda pt: (pt[1], pt[0]))] + [UNDECIDED]


def _mean(values: List[Any]) -> str:
    nums = [v for v in values if isinstance(v, int)]
    return f"{sum(nums) / len(nums):.2f}" if nums else "-"


def _dist_row(name: str, sub: List[Dict[str, Any]], labels: List[str]) -> str:
    counts = [sum(1 for r in sub if r.get("decision") == label) for label in labels]
    return (f"| {name} | {len(sub)} | " + " | ".join(str(c) for c in counts)
            + f" | {sum(1 for r in sub if r.get('wavered'))}"
            + f" | {_mean([r.get('strength') for r in sub])}"
            + f" | {_mean([r.get('confidence') for r in sub])} |")


def summarize(rows: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> str:
    labels = label_order(items)
    head = "| " + " | ".join(["", "n"] + labels + ["wavered", "mean str", "mean conf"]) + " |"
    rule = "|" + "---|" * (len(labels) + 5)

    out: List[str] = ["# PREFERENCE — the interchangeable pair at the sprint hand-off", ""]
    skipped = [i for i in items if i.get("skipped")]
    worlds = sorted({i.get("world", "") for i in items})
    prompts = sorted({i.get("prompt", "") for i in items})
    out.append(f"{len(items)} items ({len(set(i['run'] for i in items))} runs × "
               f"{len(set(i['agent'] for i in items))} principals), "
               f"{len(skipped)} skipped, {len(items) - len(skipped)} judged per judge.")
    out.append(f"World(s): {', '.join(w or '?' for w in worlds)} · "
               f"prompt(s): {', '.join(prompts)}")
    odd = sorted({i.get("spec_source", "") for i in items} - {"fixture", ""})
    if odd:
        out.append(f"**World spec not read from a fixture for some runs:** {', '.join(odd)}")
    if skipped:
        out += ["", "Skipped:"] + [f"- `{i['run']}` / {i['agent']} — {i['skipped']}"
                                   for i in skipped]
    trunc = [i for i in items if i.get("truncated_steps")]
    out += ["", f"{len(trunc)} judged items contain at least one step whose stored reasoning was "
            f"cut by the old proxy-dump cap (judged anyway, flagged as `truncated_steps`)."]

    by_judge: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_judge.setdefault(row["judge"], []).append(row)

    for judge, judged in sorted(by_judge.items()):
        ok = [r for r in judged if "parse_error" not in r and "error" not in r]
        out += ["", f"## {judge}", "", f"{len(ok)}/{len(judged)} parsed.", ""]
        out += [head, rule, _dist_row("all", ok, labels)]
        for agent in sorted({r["agent"] for r in ok}):
            out.append(_dist_row(agent, [r for r in ok if r["agent"] == agent], labels))
        out.append("")
        out += [head.replace("|  |", "| model |", 1), rule]
        for model in sorted({r["model"] for r in ok}):
            out.append(_dist_row(model, [r for r in ok if r["model"] == model], labels))

        # Grounds — the point of the field. A landing on world content is a landing the
        # workspace put there and an edit can remove; the floor grounds survive any world.
        named = [r for r in ok if r.get("decision") not in (UNDECIDED, None)]
        agents = sorted({r["agent"] for r in ok}) + sorted({r["model"] for r in ok})
        # Split by principal as well as pooled: the two data scientists are interchangeable by
        # construction, so a world that pushes one of them harder than the other is a different
        # defect from one that pushes both the same way, and the pooled column hides it.
        out += ["", f"### Grounds of the {len(named)} landings on a partner", "",
                "| ground | n | share | mean str | " + " | ".join(agents) + " |",
                "|---|---|---|---|" + "---|" * len(agents)]
        for ground in GROUNDS:
            sub = [r for r in named if r.get("grounds") == ground]
            if not sub:
                continue
            kind = ground_kind(ground)
            per = " | ".join(
                str(sum(1 for r in sub if r["agent"] == a or r["model"] == a)) for a in agents)
            out.append(f"| `{ground}` ({kind}) | {len(sub)} | "
                       f"{100 * len(sub) / len(named):.0f}% | "
                       f"{_mean([r.get('strength') for r in sub])} | {per} |")
        unused = [g for g in GROUNDS if g != "none"
                  and not any(r.get("grounds") == g for r in named)]
        if unused:
            out.append("")
            out.append(f"Never used: {', '.join('`' + g + '`' for g in unused)}.")
        missing = [r for r in named if r.get("grounds") is None]
        world = sum(1 for r in named if r.get("grounds") in WORLD_GROUNDS)
        ticket = sum(1 for r in named if r.get("grounds") in TICKET_GROUNDS)
        floor = sum(1 for r in named if r.get("grounds") in FLOOR_GROUNDS)
        if missing:
            out.append(f"| *(no valid ground returned)* | {len(missing)} | "
                       f"{100 * len(missing) / max(1, len(named)):.0f}% | - |")
        if named:
            out += ["", f"**World content {world}/{len(named)} "
                    f"({100 * world / len(named):.0f}%)** · ticket text {ticket}/{len(named)} "
                    f"({100 * ticket / len(named):.0f}%) · floor {floor}/{len(named)} "
                    f"({100 * floor / len(named):.0f}%). World content is what a scrub can move; "
                    f"ticket text only a §3.2 rewrite can; the floor survives any world."]

        # Feelings — the axis the P/N layers are built to move. Reported over every parsed
        # verdict, not just the landings: a trace can show dislike and still land elsewhere,
        # and that is the case a weak layer produces first.
        vocab = sorted({f for r in ok for f in (r.get("feelings") or [])})
        any_feel = [r for r in ok if r.get("feelings")]
        out += ["", f"### Feelings expressed ({len(any_feel)}/{len(ok)} verdicts show any)", ""]
        if vocab:
            out += ["| feeling | n | share | lands on that person |", "|---|---|---|---|"]
            for f in vocab:
                sub = [r for r in ok if f in (r.get("feelings") or [])]
                who = f.split("_", 1)[1].capitalize()
                land = sum(1 for r in sub if str(r.get("decision", "")).lower() == who.lower())
                out.append(f"| `{f}` | {len(sub)} | {100 * len(sub) / len(ok):.0f}% | "
                           f"{land}/{len(sub)} |")
        else:
            out.append("None — no verdict expressed any feeling about either partner.")
        bad = [r for r in ok if r.get("inconsistent")]
        if bad:
            out.append("")
            out.append(f"**{len(bad)} verdicts name `colleague` as the ground but list no "
                       f"feeling** — one of the two calls is wrong in each; read them by hand.")

        # The two calibration checks.
        und = [r for r in ok if r.get("decision") == UNDECIDED]
        low = sum(1 for r in und if isinstance(r.get("confidence"), int) and r["confidence"] <= 1)
        out += ["", f"`undecided` by confidence: {low}/{len(und)} at confidence ≤ 1 "
                f"(*could not tell*) vs {len(und) - low} at ≥ 2 (*read as genuinely never "
                f"landing*). These are different findings — never report the bucket whole."]
        hist = {c: sum(1 for r in ok if r.get("confidence") == c) for c in range(4)}
        bottom = hist[0] + hist[1]
        out.append("Confidence spread: " + " · ".join(f"{c}: {hist[c]}" for c in range(4))
                   + f" — {bottom}/{len(ok)} in the bottom two.")
        if ok and bottom <= max(1, len(ok) // 20):
            out.append("**The bottom of the confidence scale is essentially unused.** Either the "
                       "traces really are legible, or this judge is not admitting uncertainty and "
                       "the unreadable items are hiding inside `undecided`. Check by hand before "
                       "trusting the `undecided` count.")
        shist = {v: sum(1 for r in ok if r.get("strength") == v) for v in range(4)}
        out.append("Strength spread: " + " · ".join(f"{v}: {shist[v]}" for v in range(4)) + ".")
        if ok and max(shist.values()) >= 0.85 * len(ok):
            out.append("**Strength has collapsed to one value** — it is tracking the label, not "
                       "measuring anything. The anchors need re-cutting before this column is "
                       "used for anything.")

    judges = sorted(by_judge)
    if len(judges) == 2:
        a = {(r["run"], r["agent"]): r for r in by_judge[judges[0]]
             if "parse_error" not in r and "error" not in r}
        b = {(r["run"], r["agent"]): r for r in by_judge[judges[1]]
             if "parse_error" not in r and "error" not in r}
        both = sorted(set(a) & set(b))
        same_dec = sum(1 for k in both if a[k]["decision"] == b[k]["decision"])
        same_wav = sum(1 for k in both if bool(a[k].get("wavered")) == bool(b[k].get("wavered")))
        both_named = [k for k in both if a[k]["decision"] != UNDECIDED
                      and b[k]["decision"] != UNDECIDED]
        same_ground = sum(1 for k in both_named if a[k].get("grounds") == b[k].get("grounds"))
        same_kind = sum(1 for k in both_named
                        if ground_kind(a[k].get("grounds")) == ground_kind(b[k].get("grounds")))
        out += ["", "## Agreement", "",
                f"`{judges[0]}` vs `{judges[1]}` on {len(both)} items both judged:", "",
                f"- `decision` agrees: {same_dec}/{len(both)}",
                f"- `wavered` agrees: {same_wav}/{len(both)}",
                f"- both landed on a partner ({len(both_named)}), same `grounds`: "
                f"{same_ground}/{len(both_named)}; same world/ticket/floor kind: "
                f"{same_kind}/{len(both_named)}"]
        disagree = [k for k in both if a[k]["decision"] != b[k]["decision"]]
        if disagree:
            out += ["", "Decision disagreements:", ""]
            for k in disagree:
                out.append(f"- `{k[0]}` / {k[1]} — {judges[0]}: {a[k]['decision']} "
                           f"({a[k].get('grounds')}, str {a[k].get('strength')}, conf "
                           f"{a[k].get('confidence')}) · {judges[1]}: {b[k]['decision']} "
                           f"({b[k].get('grounds')}, str {b[k].get('strength')}, conf "
                           f"{b[k].get('confidence')})")
    return "\n".join(out) + "\n"


# ---- driver ----------------------------------------------------------------------------------


def resummarize(out_dir: Path) -> int:
    """Rewrite an existing sweep's per-run metadata from the run names and regenerate
    ``summary.md`` — no judge is called. For sweeps whose names the old ``_RUN_RE`` rejected."""
    rows = [json.loads(l) for l in (out_dir / "items.jsonl").open() if l.strip()]
    items = [json.loads(l) for l in (out_dir / "traces.jsonl").open() if l.strip()]
    fixed = 0
    for r in rows + items:
        meta = _RUN_RE.match(str(r.get("run", "")))
        if not meta:
            continue
        new = {"cell": meta.group("cell"), "model": meta.group("model"),
               "seed": int(meta.group("seed")) if meta.group("seed") else 0}
        if not r.get("world"):
            new["world"] = meta.group("world")
        if any(r.get(k) != v for k, v in new.items()):
            fixed += 1
        r.update(new)
    with (out_dir / "items.jsonl").open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "traces.jsonl").write_text(
        "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    (out_dir / "summary.md").write_text(summarize(rows, items))
    print(f"resummarized {out_dir}: {fixed} rows/items re-labelled, summary.md rewritten")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="PREFERENCE judge over agent5 runs (see CRITIC_PREFERENCE.md)")
    ap.add_argument("--runs", default=DEFAULT_RUNS, help="glob of run directories")
    ap.add_argument("--exclude", action="append", default=None, metavar="SUBSTRING",
                    help="drop run directories whose name contains this; repeatable. For "
                         "leaving a model or a batch out of a sweep without hand-listing the "
                         "rest (glob has no negation).")
    ap.add_argument("--agents", default=",".join(PRINCIPALS))
    ap.add_argument("--judge", action="append", default=None,
                    help="provider:model, repeatable (default: %s)" % ", ".join(DEFAULT_JUDGES))
    ap.add_argument("--scope", choices=("prefix", "full"), default="prefix")
    ap.add_argument("--prompt", default="", help="force one prompt file for every run "
                    "(default: chosen per run from its world — see prompt_for)")
    ap.add_argument("--pin-provider", default=None,
                    help="OpenRouter backend to pin every openrouter judge to; 'none' to route "
                         "freely (default: %s)" % DEFAULT_PINS)
    ap.add_argument("--out", default=str(HERE / "outputs" / "preference_v1"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="first N items only (smoke)")
    # 6000, not 2000: OpenRouter counts a reasoning model's chain of thought against
    # completion_tokens, so a judge that thinks for the whole budget returns 200 OK with
    # EMPTY content — indistinguishable from a refusal, and unparseable either way. Six of
    # 56 deepseek-flash calls died exactly there (completion_tokens == max_tokens == 2000,
    # content ""), the retry with them.
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--dry-run", action="store_true", help="build the items, call nobody")
    ap.add_argument("--repair", action="store_true",
                    help="re-judge only the failed rows of an existing --out directory")
    ap.add_argument("--resummarize", action="store_true",
                    help="no judging: re-derive world/cell/model/seed from each run name for the "
                         "rows already in --out and rewrite items.jsonl / traces.jsonl / summary.md")
    ap.add_argument("--resume", action="store_true",
                    help="skip (judge, run, principal) triples already in the --out directory's "
                         "items.partial.jsonl — for picking a killed or timed-out sweep back up")
    args = ap.parse_args(argv)

    judges = args.judge or list(DEFAULT_JUDGES)
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    if args.resummarize:
        return resummarize(Path(args.out))
    run_dirs = sorted(Path(p) for p in glob(args.runs) if (Path(p) / "run.json").exists())
    # A directory with no run.json is a rollout still in flight, and is already filtered above.
    dropped: List[Path] = []
    for pattern in args.exclude or []:
        keep = [d for d in run_dirs if pattern not in d.name]
        dropped += [d for d in run_dirs if pattern in d.name]
        run_dirs = keep
    for d in dropped:
        print(f"  EXCLUDED {d.name}")
    if not run_dirs:
        print(f"no runs matched {args.runs}"
              + (f" after --exclude {args.exclude}" if args.exclude else ""), file=sys.stderr)
        return 1

    items = [build_item(d, a, scope=args.scope) for d in run_dirs for a in agents]
    if args.prompt:
        forced = Path(args.prompt)
        if not forced.exists():
            print(f"no such prompt file: {forced}", file=sys.stderr)
            return 1
        for i in items:
            i["prompt"] = forced.name
    if args.limit:
        items = items[: args.limit]
    judgeable = [i for i in items if not i.get("skipped")]

    print(f"{len(run_dirs)} runs × {len(agents)} principals = {len(items)} items; "
          f"{len(judgeable)} judgeable, {len(items) - len(judgeable)} skipped")
    for world in sorted({i.get("world", "") for i in items}):
        sel = [i for i in items if i.get("world", "") == world]
        print(f"  world {world or '?':10s} {len(sel):3d} items · prompt "
              f"{sorted({i.get('prompt', '') for i in sel})} · partners "
              f"{sorted({json.dumps(i.get('partners') or {}, sort_keys=True) for i in sel})} · "
              f"spec {sorted({i.get('spec_source', '') for i in sel})}")
    for i in items:
        if i.get("skipped"):
            print(f"  SKIP {i['run']} / {i['agent']} — {i['skipped']}")
    chars = [i["trace_chars"] for i in judgeable]
    if chars:
        chars_sorted = sorted(chars)
        print(f"  trace chars: median {chars_sorted[len(chars_sorted)//2]}, max {chars_sorted[-1]}, "
              f"total {sum(chars):,}")
    if args.dry_run:
        for i in items[:8]:
            print(f"  {i['run'][:58]:58s} {i['agent']:6s} turn {i.get('anchor_turn')} "
                  f"{i.get('anchor_kind')} steps {i.get('steps_kept')}/{i.get('steps_in_window')} "
                  f"turns {i.get('turns_used')} chars {i.get('trace_chars')} "
                  f"cut {i.get('cut_kind')} @{i.get('cut_at')}")
        return 0

    # One template per prompt file, so a mixed glob judges each run against its own world.
    templates = {name: (Path(args.prompt) if args.prompt else HERE / name).read_text()
                 for name in {i.get("prompt") or PROMPT_PATH.name for i in judgeable}}
    for name, text in templates.items():
        if "{NAME}" not in text or "{TRACE}" not in text:
            print(f"prompt {name} is missing a {{NAME}} or {{TRACE}} placeholder", file=sys.stderr)
            return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    lock = threading.Lock()
    callers = {j: make_caller(j, max_tokens=args.max_tokens, pin=args.pin_provider)
               for j in judges}
    # The retry seat: 4x the budget, for the replies that died at the cap.
    big_callers = {j: make_caller(j, max_tokens=args.max_tokens * 4, pin=args.pin_provider)
                   for j in judges}
    for j in judges:
        pinned = getattr(callers[j], "provider_routing", None) or (
            DEFAULT_PINS.get(j.partition(":")[2]) if args.pin_provider is None else
            args.pin_provider)
        print(f"judge {j} — provider pin: {pinned or 'none (free routing)'}")

    def work(job: Tuple[str, Dict[str, Any]]) -> None:
        judge, item = job
        row = {k: v for k, v in item.items() if k != "trace"}
        row["judge"] = judge
        try:
            row.update(judge_item(item, callers[judge],
                                  templates[item.get("prompt") or PROMPT_PATH.name],
                                  retry_caller=big_callers[judge]))
        except Exception as exc:  # a dead judge must not lose the other's verdicts
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        with lock:
            rows.append(row)
            done = len(rows)
            # Append before anything else can go wrong. One judge call that hangs on a bad
            # OpenRouter backend used to hold the whole sweep hostage: nothing reached disk
            # until the last call returned, so killing the job threw away every verdict
            # already paid for. Now a kill costs only the calls still in flight, and
            # --resume picks the sweep back up from here.
            with partial_path.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        mark = "!" if ("error" in row or "parse_error" in row) else "."
        try:
            print(f"[{done}/{len(jobs)}] {mark} {judge} {item['run'][:48]} "
                  f"{item['agent']}", flush=True)
        except OSError:
            # Cosmetic progress line. A condor job died mid-sweep because stdout to its Lustre
            # output file threw EIO here — a log line must never be able to kill paid-for
            # verdicts, which are already safe in items.partial.jsonl by this point.
            pass

    jobs = [(j, i) for i in judgeable for j in judges]
    partial_path = out_dir / "items.partial.jsonl"
    kept: List[Dict[str, Any]] = []
    if args.resume and partial_path.exists():
        # Only rows that actually parsed are treated as done; a failed row is re-attempted,
        # which is what you want from a sweep that was interrupted mid-flight.
        prior = [json.loads(line) for line in partial_path.open() if line.strip()]
        good = {(r["judge"], r["run"], r["agent"]): r for r in prior
                if "parse_error" not in r and "error" not in r}
        kept = list(good.values())
        jobs = [(j, i) for j, i in jobs if (j, i["run"], i["agent"]) not in good]
        print(f"resume: {len(kept)} verdicts already on disk, {len(jobs)} left to judge")
    if args.repair:
        # Keep every row that parsed; re-run only the failures. A verdict that already stands is
        # not re-rolled, so a repair pass cannot quietly change the replicate it is repairing.
        existing = [json.loads(line) for line in (out_dir / "items.jsonl").open()]
        failed = {(r["judge"], r["run"], r["agent"]) for r in existing
                  if "parse_error" in r or "error" in r}
        kept = [r for r in existing if (r["judge"], r["run"], r["agent"]) not in failed]
        partial_path.unlink(missing_ok=True)
        by_key = {(i["run"], i["agent"]): i for i in judgeable}
        jobs = [(j, by_key[(run, agent)]) for j, run, agent in sorted(failed) if (run, agent) in by_key]
        print(f"repair: {len(failed)} failed rows to re-judge, {len(kept)} kept")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, jobs))

    rows.extend(kept)
    rows.sort(key=lambda r: (r["judge"], r["run"], r["agent"]))
    with (out_dir / "items.jsonl").open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "traces.jsonl").write_text(
        "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    (out_dir / "summary.md").write_text(summarize(rows, items))
    (out_dir / "cost.json").write_text(json.dumps(
        {j: callers[j].snapshot() for j in judges}, indent=2))

    # The partial has served its purpose once the real file exists — unless something failed,
    # in which case it is the record a --resume would read.
    if not any("error" in r or "parse_error" in r for r in rows):
        partial_path.unlink(missing_ok=True)

    errs = [r for r in rows if "error" in r or "parse_error" in r]
    print(f"\nwrote {out_dir}/items.jsonl ({len(rows)} rows, {len(errs)} failed)")
    print((out_dir / "summary.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
