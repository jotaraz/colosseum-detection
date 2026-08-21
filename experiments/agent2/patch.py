from __future__ import annotations

"""The workspace patch: the prompter's free-form edits to the base fixture's messages.

agent2 pins the *structure* of the world and frees its *content*. Roster, calendars, board,
conversations and their memberships are frozen; every message in them — and the pinned sprint
brief — is the prompter's to rewrite. Four ops::

    {"op": "add",    "conversation": "C-sprint", "after": "1754…"|"start",
                     "user": "Kira", "text": "…"}
    {"op": "edit",   "ts": "1754…", "text": "…"}          # author and position keep
    {"op": "delete", "ts": "1754…"}
    {"op": "pin",    "conversation": "C-sprint", "text": "…"}

A patch is always expressed against the **frozen base fixture**, never cumulatively: a step's
candidate is the whole treatment, so step N and step 1 stay comparable and a trajectory can be
read as edits (``render_diff`` diffs two patches for the viewer; both resolve against base).

**The prompter never writes a timestamp.** It anchors an addition to an existing message and
this module places it, spreading multiple additions evenly through the gap to the next message
(``_assign_ts``). That keeps ts monotone per conversation, unique globally — a ts is the message
identifier the tools, the uptake ledger and the fixture's ground truth all key on — and keeps
every planted message in the past, since an assistant reading a message stamped after ``now``
would see the future.

What this module rejects is only what would make the world *malformed* — a dangling anchor, an
author who is not in the conversation, a message in the future, two ops fighting over one
message, more ops than the budget. Everything about whether the patch is *admissible* (does it
plant an instruction, an excuse, a contradiction) or whether the scenario survives it belongs to
the gates: ``GATE_WORKSPACE.md`` and ``GATE_SCENARIO.md``. This is the cheap deterministic pass
that runs before a gate call is spent, exactly as ``social_jira4.blocks.validate`` did.
"""

import json
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from experiments.agent1.workspace import Conversation, Message, Workspace, human_time

#: Free-form was chosen deliberately, but an unbounded patch turns "the agent1 world under
#: pressure" into "whatever world scores". The cap is recorded in a run's metadata so a sweep can
#: lift it on purpose rather than by drift.
DEFAULT_PATCH_BUDGET = 12

#: Anchor meaning "before the conversation's first message".
START = "start"

#: Minimum spacing between placed messages, in seconds. Below this, ``to_ts``'s microsecond
#: formatting still separates them, but the conversation stops reading like one people wrote.
_MIN_GAP = 1.0

#: How far to reach when a gap has no far edge — an addition after the last message of a
#: conversation, or before the first. Clamped to ``now`` on the forward side.
_OPEN_GAP = 300.0


# --------------------------------------------------------------------------------- the ops
@dataclass
class AddMessage:
    conversation: str
    user: str
    text: str
    after: str = START

    op: ClassVar[str] = "add"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op,
            "conversation": self.conversation,
            "after": self.after,
            "user": self.user,
            "text": self.text,
        }


@dataclass
class EditMessage:
    ts: str
    text: str

    op: ClassVar[str] = "edit"

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "ts": self.ts, "text": self.text}


@dataclass
class DeleteMessage:
    ts: str

    op: ClassVar[str] = "delete"

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "ts": self.ts}


@dataclass
class EditPinned:
    conversation: str
    text: str

    op: ClassVar[str] = "pin"

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "conversation": self.conversation, "text": self.text}


PatchOp = Union[AddMessage, EditMessage, DeleteMessage, EditPinned]

_OP_TYPES: Dict[str, Any] = {t.op: t for t in (AddMessage, EditMessage, DeleteMessage, EditPinned)}
#: Accepted spellings for each op, so a prompter that writes the obvious synonym is not
#: failed for it. Canonicalised on parse; only the canonical name is ever serialised.
_OP_ALIASES: Dict[str, str] = {
    "add": "add", "add_message": "add", "post": "add", "insert": "add",
    "edit": "edit", "edit_message": "edit", "replace": "edit", "rewrite": "edit",
    "delete": "delete", "delete_message": "delete", "remove": "delete",
    "pin": "pin", "edit_pinned": "pin", "pinned": "pin", "set_pinned": "pin",
}

_REQUIRED: Dict[str, Tuple[str, ...]] = {
    "add": ("conversation", "user", "text"),
    "edit": ("ts", "text"),
    "delete": ("ts",),
    "pin": ("conversation", "text"),
}


def parse_op(d: Dict[str, Any]) -> PatchOp:
    """One op dict → the op. Raises ``ValueError`` on a shape no op can be made of."""
    if not isinstance(d, dict):
        raise ValueError(f"patch op must be an object, got {type(d).__name__}")
    raw = str(d.get("op") or "").strip().lower()
    name = _OP_ALIASES.get(raw)
    if not name:
        raise ValueError(f"unknown patch op {d.get('op')!r}; expected one of {sorted(_OP_TYPES)}")
    missing = [f for f in _REQUIRED[name] if d.get(f) in (None, "")]
    if missing:
        raise ValueError(f"{name} op missing fields: {missing}")
    if name == "add":
        return AddMessage(
            conversation=str(d["conversation"]),
            user=str(d["user"]),
            text=str(d["text"]),
            after=str(d.get("after") or START),
        )
    if name == "edit":
        return EditMessage(ts=str(d["ts"]), text=str(d["text"]))
    if name == "delete":
        return DeleteMessage(ts=str(d["ts"]))
    return EditPinned(conversation=str(d["conversation"]), text=str(d["text"]))


def parse_patch(items: Optional[Sequence[Any]]) -> List[PatchOp]:
    """A patch list (as the prompter emits it) → ops. An empty/absent patch is legal: a
    candidate may leave the world alone and press only through the prompts."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise ValueError(f"patch must be a list of ops, got {type(items).__name__}")
    return [parse_op(d) for d in items]


def patch_to_list(ops: Sequence[PatchOp]) -> List[Dict[str, Any]]:
    return [op.to_dict() for op in ops]


# ------------------------------------------------------------------------------- indexing
def _conv_index(ws: Workspace) -> Dict[str, Conversation]:
    """Every reference that unambiguously names a conversation → that conversation.

    Ids, ``#channel``, the bare channel name, and the ``dm:A+B`` label the tools *list* DMs
    under — the prompter reads the world in that vocabulary, so it writes patches in it too.
    A spelling that two conversations answer to is dropped rather than resolved arbitrarily.
    """
    index: Dict[str, Conversation] = {}
    clashes: set[str] = set()

    def offer(key: str, conv: Conversation) -> None:
        key = key.strip()
        if not key:
            return
        if index.get(key, conv) is not conv:
            clashes.add(key)
        index[key] = conv

    for conv in ws.conversations.values():
        offer(conv.id, conv)
        offer(conv.label, conv)
        if conv.name:
            offer(conv.name, conv)
            offer(f"#{conv.name}", conv)
        if conv.type == "dm":
            offer(f"dm:{'+'.join(reversed(conv.members))}", conv)
    for key in clashes:
        index.pop(key, None)
    return index


def _ts_index(ws: Workspace) -> Dict[str, Tuple[Conversation, Message]]:
    return {m.ts: (conv, m) for conv in ws.conversations.values() for m in conv.messages}


def _now_ts(ws: Workspace) -> float:
    return ws.now.timestamp()


# ----------------------------------------------------------------------------- validation
def validate(
    ws: Workspace,
    ops: Sequence[PatchOp],
    *,
    budget: int = DEFAULT_PATCH_BUDGET,
) -> List[str]:
    """Return human-readable problems ([] == applicable). ``ws`` is the **base** workspace.

    Every message is stated with enough context for the prompter to fix it without another
    look at the world — a bare "unknown ts" costs a repair attempt to diagnose.
    """
    problems: List[str] = []
    convs = _conv_index(ws)
    by_ts = _ts_index(ws)
    now = _now_ts(ws)

    if budget is not None and len(ops) > budget:
        problems.append(
            f"patch has {len(ops)} ops, over the budget of {budget}; make fewer, larger edits"
        )

    touched: Dict[str, str] = {}   # ts -> the op that claimed it
    deleted: set[str] = set()
    pinned_seen: set[str] = set()
    # conversation -> anchor -> how many additions land there, for the spacing check below
    placements: Dict[Tuple[str, str], int] = {}

    for i, op in enumerate(ops):
        where = f"op {i} ({op.op})"

        if isinstance(op, (EditMessage, DeleteMessage)):
            found = by_ts.get(op.ts)
            if not found:
                problems.append(f"{where}: no message with ts {op.ts!r} in the base workspace")
                continue
            conv, msg = found
            if op.ts in touched:
                problems.append(
                    f"{where}: message {op.ts!r} ({conv.label}, {msg.user}) is already the "
                    f"subject of {touched[op.ts]}; one op per message"
                )
                continue
            touched[op.ts] = where
            if isinstance(op, DeleteMessage):
                deleted.add(op.ts)
            elif not op.text.strip():
                problems.append(f"{where}: empty text — to remove a message use the delete op")
            continue

        if isinstance(op, EditPinned):
            conv = convs.get(op.conversation.strip())
            if not conv:
                problems.append(
                    f"{where}: no conversation matching {op.conversation!r} "
                    f"(use an id, #channel, or the dm:A+B label)"
                )
                continue
            if conv.pinned is None:
                problems.append(
                    f"{where}: {conv.label} has no pinned brief to rewrite; the pin op edits an "
                    f"existing brief, it does not create one"
                )
                continue
            if conv.id in pinned_seen:
                problems.append(f"{where}: the pinned brief of {conv.label} is edited twice")
                continue
            pinned_seen.add(conv.id)
            if not op.text.strip():
                problems.append(f"{where}: empty pinned brief")
            continue

        # --- add
        conv = convs.get(op.conversation.strip())
        if not conv:
            problems.append(
                f"{where}: no conversation matching {op.conversation!r} "
                f"(use an id, #channel, or the dm:A+B label)"
            )
            continue
        if op.user not in ws.users:
            problems.append(f"{where}: {op.user!r} has no account; a patch cannot invent people")
            continue
        if op.user not in conv.members:
            problems.append(
                f"{where}: {op.user} is not in {conv.label} (members: {', '.join(conv.members)}); "
                f"a patch cannot change who is in a conversation"
            )
            continue
        if not op.text.strip():
            problems.append(f"{where}: empty text")
            continue
        anchor = op.after.strip()
        if anchor.lower() != START:
            found = by_ts.get(anchor)
            if not found:
                problems.append(
                    f"{where}: anchor ts {anchor!r} is not a message in the base workspace"
                )
                continue
            anchor_conv, _ = found
            if anchor_conv.id != conv.id:
                problems.append(
                    f"{where}: anchor {anchor!r} is in {anchor_conv.label}, but the message is "
                    f"being added to {conv.label}; anchor to a message in the same conversation"
                )
                continue
            if float(anchor) > now:
                problems.append(f"{where}: anchor {anchor!r} is after the run's clock")
                continue
        placements[(conv.id, anchor.lower() if anchor.lower() == START else anchor)] = (
            placements.get((conv.id, anchor.lower() if anchor.lower() == START else anchor), 0) + 1
        )

    # An anchor that some other op deletes would leave the addition dangling. Checked after the
    # loop because a patch may legally delete a message that appears earlier in the list.
    for i, op in enumerate(ops):
        if isinstance(op, AddMessage) and op.after.strip() in deleted:
            problems.append(
                f"op {i} (add): anchored to {op.after!r}, which another op deletes; "
                f"anchor to a message that survives the patch"
            )

    # Room to place them: the gap has to hold every addition anchored into it.
    for (conv_id, anchor), count in placements.items():
        conv = ws.conversations[conv_id]
        try:
            lo, hi = _gap(conv, anchor, now)
        except ValueError as exc:
            problems.append(f"add into {conv.label}: {exc}")
            continue
        if (hi - lo) / (count + 1) < _MIN_GAP:
            problems.append(
                f"add into {conv.label}: {count} messages will not fit between {anchor!r} and the "
                f"next message ({hi - lo:.1f}s of room); anchor some of them elsewhere"
            )
    return problems


# ------------------------------------------------------------------------------- applying
def _gap(conv: Conversation, anchor: str, now: float) -> Tuple[float, float]:
    """The open interval (lo, hi) an addition anchored at ``anchor`` may be placed in."""
    stamps = [float(m.ts) for m in conv.messages]
    if anchor == START:
        hi = min(stamps) if stamps else now
        return hi - _OPEN_GAP, hi
    lo = float(anchor)
    later = [s for s in stamps if s > lo]
    if later:
        return lo, min(later)
    hi = min(lo + _OPEN_GAP, now)
    if hi <= lo:
        raise ValueError(
            f"anchor {anchor!r} is the last message and sits at the run's clock; there is no room "
            f"after it — anchor earlier"
        )
    return lo, hi


def _assign_ts(lo: float, hi: float, count: int, taken: set[str]) -> List[str]:
    """Spread ``count`` additions evenly through (lo, hi), skipping any ts already in use."""
    step = (hi - lo) / (count + 1)
    out: List[str] = []
    for i in range(count):
        value = lo + step * (i + 1)
        ts = f"{value:.6f}"
        while ts in taken:  # microsecond nudge; the ordering is already fixed by `value`
            value += 1e-6
            ts = f"{value:.6f}"
        taken.add(ts)
        out.append(ts)
    return out


def _synced_raw(ws: Workspace) -> Dict[str, Any]:
    """The fixture dict brought back into line with a mutated ``Workspace``.

    Only what a patch can touch is written back — messages, pinned briefs, and the ground-truth
    annotation — so anything the fixture carries that this module does not model survives
    untouched rather than being dropped by a partial re-serialization.
    """
    raw = ws.raw
    for entry in raw.get("conversations") or []:
        conv = ws.conversations.get(str(entry.get("id")))
        if conv is None:
            continue
        entry["messages"] = [{"ts": m.ts, "user": m.user, "text": m.text} for m in conv.messages]
        if conv.pinned is not None:
            entry["pinned"] = conv.pinned
    if ws.ground_truth:
        raw["ground_truth"] = ws.ground_truth
    return raw


@dataclass
class PatchResult:
    """The patched world plus a record of what the patch did to it."""

    workspace: Workspace
    added: List[Tuple[str, str]]          # (conversation label, new ts)
    edited: List[Tuple[str, str]]         # (conversation label, ts)
    deleted: List[Tuple[str, str]]        # (conversation label, ts)
    pinned: List[str]                     # ids of conversations whose brief was rewritten
    #: Base messages the fixture flagged as carrying the dislike signal that this patch edited
    #: or deleted. The scenario invariant is verified semantically by ``GATE_SCENARIO.md`` — the
    #: signal messages are editable — but a step record that says which ones moved is what makes
    #: a scenario rejection readable after the fact.
    signal_impact: Dict[str, List[str]]

    def marks(self) -> set[str]:
        """What ``render_world(patched_ts=…)`` should mark: every ts the patch put there or
        rewrote, plus the ids of conversations whose brief it rewrote. Deletions are absent by
        construction — there is nothing left to mark."""
        return {ts for _, ts in self.added} | {ts for _, ts in self.edited} | set(self.pinned)

    def summary(self) -> str:
        bits = []
        for label, items in (
            ("added", self.added), ("edited", self.edited), ("deleted", self.deleted)
        ):
            if items:
                bits.append(f"{len(items)} {label}")
        if self.pinned:
            bits.append(f"{len(self.pinned)} brief(s) rewritten")
        return ", ".join(bits) or "no changes"


def apply(
    base: Workspace,
    ops: Sequence[PatchOp],
    *,
    budget: int = DEFAULT_PATCH_BUDGET,
) -> PatchResult:
    """Apply a patch to a **fresh copy** of ``base`` and return the patched world.

    Copies rather than mutates so one loaded fixture serves every step of a run: a patch is
    against the frozen base by definition, and a runner that patched in place would carry step
    N−1's world into step N.

    Ops are applied edits → additions → deletions, which is what makes placement independent of
    the order the prompter happened to list them in: additions are spread through the gaps of the
    *base* conversation, and a deletion never moves a sibling's anchor.
    """
    problems = validate(base, ops, budget=budget)
    if problems:
        raise ValueError("patch does not apply:\n  - " + "\n  - ".join(problems))

    ws = Workspace(json.loads(json.dumps(base.raw)))
    convs = _conv_index(ws)
    by_ts = _ts_index(ws)
    taken = set(by_ts)
    now = _now_ts(ws)

    signal_ids = {str(t) for t in (ws.ground_truth.get("signal_messages") or [])}
    impact: Dict[str, List[str]] = {}

    added: List[Tuple[str, str]] = []
    edited: List[Tuple[str, str]] = []
    deleted: List[Tuple[str, str]] = []
    pinned: List[str] = []

    for op in ops:
        if isinstance(op, EditMessage):
            conv, msg = by_ts[op.ts]
            msg.text = op.text
            edited.append((conv.label, op.ts))
            if op.ts in signal_ids:
                impact.setdefault("edited", []).append(op.ts)
        elif isinstance(op, EditPinned):
            conv = convs[op.conversation.strip()]
            conv.pinned = op.text
            pinned.append(conv.id)

    # Additions, grouped by (conversation, anchor) so several messages sharing an anchor are
    # spread through one gap in the order the prompter listed them.
    groups: Dict[Tuple[str, str], List[AddMessage]] = {}
    for op in ops:
        if isinstance(op, AddMessage):
            anchor = op.after.strip()
            anchor = START if anchor.lower() == START else anchor
            groups.setdefault((convs[op.conversation.strip()].id, anchor), []).append(op)

    for (conv_id, anchor), items in groups.items():
        conv = ws.conversations[conv_id]
        lo, hi = _gap(conv, anchor, now)
        for ts, op in zip(_assign_ts(lo, hi, len(items), taken), items):
            conv.messages.append(Message(ts=ts, user=op.user, text=op.text))
            added.append((conv.label, ts))
        conv.messages.sort(key=lambda m: float(m.ts))

    for op in ops:
        if isinstance(op, DeleteMessage):
            conv, msg = by_ts[op.ts]
            conv.messages = [m for m in conv.messages if m.ts != op.ts]
            deleted.append((conv.label, op.ts))
            if op.ts in signal_ids:
                impact.setdefault("deleted", []).append(op.ts)

    # The fixture's own annotation of which messages carry the dislike signal is stated in ts,
    # so a deleted one has to go: leaving it would name a message that no longer exists.
    if signal_ids and impact.get("deleted"):
        ws.ground_truth["signal_messages"] = [
            t for t in ws.ground_truth.get("signal_messages", []) if str(t) not in set(impact["deleted"])
        ]

    # Write the patched state back into the fixture dict and rebuild, so the world is
    # internally consistent in all three of the ways something downstream reads it: the object
    # graph the tools serve, ``raw`` (which a run record persists), and ``sha`` — a content
    # digest, so a patched world MUST NOT keep reporting the base fixture's. Rebuilding also
    # re-runs ``Workspace._validate``, which is a free check that the patch left every
    # conversation member with an account and every principal with one.
    patched = Workspace(_synced_raw(ws))

    return PatchResult(
        workspace=patched,
        added=added,
        edited=edited,
        deleted=deleted,
        pinned=pinned,
        signal_impact=impact,
    )


# -------------------------------------------------------------------------------- reading
def _when(ts: str) -> str:
    from datetime import datetime

    return human_time(datetime.fromtimestamp(float(ts)))


def _quote(text: str, *, indent: str = "      ") -> str:
    return "\n".join(f"{indent}{line}" for line in (text or "").splitlines() or [""])


def render_diff(base: Workspace, ops: Sequence[PatchOp]) -> str:
    """The patch as a readable message diff — what ``GATE_WORKSPACE.md`` reads as ``{patch_diff}``.

    Grouped by conversation and ordered by position, because the gate's questions ("would
    repeating this be the lie", "what contradicts it") are about a message *in its thread*, not
    about an op list. Deletions and edits carry the original text: the gate cannot ask what a
    patch removed if it only sees what replaced it.
    """
    if not ops:
        return "(no workspace edits — the world is the base fixture unchanged)"

    convs = _conv_index(base)
    by_ts = _ts_index(base)
    # conversation id -> lines, each keyed by the position it belongs at
    per_conv: Dict[str, List[Tuple[float, str]]] = {}
    headers: Dict[str, Conversation] = {}

    def bucket(conv: Conversation) -> List[Tuple[float, str]]:
        headers[conv.id] = conv
        return per_conv.setdefault(conv.id, [])

    for op in ops:
        if isinstance(op, AddMessage):
            conv = convs.get(op.conversation.strip())
            if not conv:
                continue
            anchor = op.after.strip()
            at = float(anchor) + 0.5 if anchor.lower() != START else float("-inf")
            bucket(conv).append((
                at,
                f"  [+] ADDED after {'the start of the conversation' if anchor.lower() == START else _when(anchor)}"
                f" — {op.user}:\n{_quote(op.text)}",
            ))
        elif isinstance(op, EditMessage):
            found = by_ts.get(op.ts)
            if not found:
                continue
            conv, msg = found
            bucket(conv).append((
                float(op.ts),
                f"  [~] EDITED {_when(op.ts)} — {msg.user}:\n"
                f"    - was:\n{_quote(msg.text, indent='        ')}\n"
                f"    + now:\n{_quote(op.text, indent='        ')}",
            ))
        elif isinstance(op, DeleteMessage):
            found = by_ts.get(op.ts)
            if not found:
                continue
            conv, msg = found
            bucket(conv).append((
                float(op.ts),
                f"  [-] DELETED {_when(op.ts)} — {msg.user}:\n{_quote(msg.text)}",
            ))
        else:
            conv = convs.get(op.conversation.strip())
            if not conv:
                continue
            bucket(conv).append((
                float("-inf"),
                f"  [~] PINNED BRIEF REWRITTEN:\n"
                f"    - was:\n{_quote(conv.pinned or '(none)', indent='        ')}\n"
                f"    + now:\n{_quote(op.text, indent='        ')}",
            ))

    out: List[str] = []
    for conv_id in sorted(per_conv, key=lambda c: headers[c].label):
        conv = headers[conv_id]
        members = ", ".join(conv.members)
        out.append(f"### {conv.label}  (id {conv.id}; members: {members})")
        out.extend(line for _, line in sorted(per_conv[conv_id], key=lambda p: p[0]))
        out.append("")
    return "\n".join(out).rstrip()


def render_world(ws: Workspace, *, patched_ts: Iterable[str] = ()) -> str:
    """The whole workspace as text — ``{patched_world}`` for the workspace and scenario gates.

    Every conversation in full, patched messages marked, plus the calendars, board and clock a
    planted claim is checked against. Nothing is filtered by viewer: the gates judge the world,
    not one assistant's view of it.
    """
    marked = {str(t) for t in patched_ts}
    out: List[str] = [
        f"NOW: {human_time(ws.now)}",
        f"BOARD ({ws.board_name}): "
        + "; ".join(f"{t.id} — {t.title} [needs: {t.needs}]" for t in ws.tasks.values()),
        f"PRINCIPALS (each has an assistant): {', '.join(ws.principals)}",
        f"REPORTER: {ws.reporter or '—'} → {ws.report_to or '—'}",
        "",
        "## People",
    ]
    for user in ws.users.values():
        bot = " [bot]" if user["is_bot"] else ""
        out.append(f"- {user['name']} — {user['title']}, {user['department']}{bot}")

    out += ["", "## Calendars"]
    for person, events in ws.calendars.items():
        out.append(f"- {person}:")
        for ev in events:
            view = ev.view()
            out.append(f"    {view['start']}–{view['end']}  {view['title']}")
        if not events:
            out.append("    (nothing scheduled)")

    out += ["", "## Conversations"]
    for conv in sorted(ws.conversations.values(), key=lambda c: c.label):
        out.append(f"\n### {conv.label}  (id {conv.id}; members: {', '.join(conv.members)})")
        if conv.pinned:
            marker = " [PATCHED]" if conv.id in marked else ""
            out.append(f"  PINNED BRIEF{marker}:\n{_quote(conv.pinned)}")
        for msg in conv.messages:
            marker = " [PATCHED]" if msg.ts in marked else ""
            out.append(f"  [{msg.ts}] {_when(msg.ts)} — {msg.user}{marker}:\n{_quote(msg.text)}")
        if not conv.messages:
            out.append("  (no messages)")
    return "\n".join(out)
