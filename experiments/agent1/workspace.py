from __future__ import annotations

"""The workspace: the Slack/calendar/board world the tools serve, loaded from a fixture.

Content generation is deliberately out of scope here. This module defines the *shape* the
generated world has to take, loads it, holds the live mutations a run makes (posts, board
claims), and hands the whole thing to the toolset as a plain dict.

Fixture format (JSON), all of it deterministic per seed and frozen before a run::

    {
      "now": "2026-08-10T09:30:00",              # the fictional clock at kickoff
      "sprint_channel": "aug-2026-sprint",       # the shared discussion channel
      "principals": ["Alice", "Bob", "Carol", "Dan"],   # employees with an assistant
      "reporter": "Alice",                       # who owes Kira the write-up (or null)
      "report_to": "Kira",

      "users": [
        {"name": "Alice", "title": "Senior Backend Engineer",
         "department": "Engineering", "is_bot": false}
      ],

      "conversations": [
        {"id": "C-sprint", "name": "aug-2026-sprint", "type": "channel",
         "members": ["Alice", "Bob", "Carol", "Dan", "Kira", "ops-bot"],
         "pinned": "…the kickoff brief…",
         "messages": [{"ts": "1754…", "user": "ops-bot", "text": "…"}]},
        {"id": "D-alice-maya", "type": "dm", "members": ["Alice", "Maya"],
         "messages": [...]}
      ],

      "calendars": {"Alice": [{"start": "2026-08-10T14:00:00",
                               "end": "2026-08-10T15:00:00", "title": "1:1 with Kira"}]},

      "board": {
        "name": "August Sprint",
        "tasks": [{"id": "T1", "title": "…", "needs": "a backend engineer and a designer"}]
      },

      "scoring": {                               # optional; omit and the run just records
        "goodness": {"T1": {"Alice|Bob": 3.0}},  # the matching without scoring it
        "optimal": {"T1": ["Alice", "Bob"]},
        "optimal_goodness": 7.0
      },

      "ground_truth": {                          # optional; never reaches an agent
        "signal_messages": ["1754…"],            # ts of the messages that carry a dislike
        "message_types": {"1754…": "operative"}  # operative|inert|gossip|friction|distractor
      }
    }

Two invariants this module enforces, because they are the ones a content generator can
silently break: every conversation member must exist in ``users``, and every principal must
have an account. Anything else is the generator's problem.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ISO = "%Y-%m-%dT%H:%M:%S"

#: The account meeting invitations and responses are delivered from.
CALENDAR_BOT = "calendar-bot"


def parse_dt(value: str) -> datetime:
    """Accept a full ISO datetime or a bare date.

    Models pass ``2026-08-10`` for a day boundary at least as often as
    ``2026-08-10T00:00:00``; rejecting the short form cost a wasted tool call and produced a
    harness-shaped error in the first live run.
    """
    text = str(value).strip().replace(" ", "T")
    for fmt in (ISO, "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(datetime(2026, 1, 1).strftime(fmt))], fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable datetime: {value!r}")


def to_ts(moment: datetime) -> str:
    """Slack-style message identifier: epoch seconds with a microsecond tail."""
    return f"{moment.timestamp():.6f}"


def human_time(moment: datetime) -> str:
    return moment.strftime("%a %d %b %H:%M")


@dataclass
class Message:
    ts: str
    user: str
    text: str

    def view(self, moment: Optional[datetime] = None) -> Dict[str, Any]:
        """The agent-visible form. Carries both machine and human time (SPEC: timestamps)."""
        when = moment or datetime.fromtimestamp(float(self.ts))
        return {"ts": self.ts, "time": human_time(when), "from": self.user, "text": self.text}


@dataclass
class Conversation:
    id: str
    type: str  # "channel" | "dm"
    members: List[str]
    name: Optional[str] = None
    pinned: Optional[str] = None
    messages: List[Message] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"#{self.name}" if self.type == "channel" else f"dm:{'+'.join(self.members)}"


@dataclass
class CalendarEvent:
    """A calendar entry. The last four fields are set only on events an assistant created.

    A fixture event carries start/end/title and nothing else, and renders exactly as it
    always did — no id, no organiser, no attendees. An event written by
    ``calendar_create_event`` carries all four, and the id is what ``calendar_respond``
    addresses. That asymmetry is deliberate rather than tidy: you can decline an invitation
    somebody sent you, and you cannot decline the standing meeting that was already in your
    week, so only invitations need to be addressable.

    Each invitee gets their **own copy** of the event, sharing the id. Responses are per
    person, so one shared object would have Marcus's decline erase the meeting from Priya's
    calendar too.
    """

    start: str
    end: str
    title: str
    id: str = ""
    organiser: str = ""
    attendees: List[str] = field(default_factory=list)
    #: "" until the owner answers, then "accepted" or "declined". Only ever set on the
    #: invitee's copy; the organiser's copy stays blank.
    response: str = ""

    def view(self) -> Dict[str, Any]:
        s, e = parse_dt(self.start), parse_dt(self.end)
        out = {
            "start": human_time(s),
            "end": e.strftime("%H:%M"),
            "title": self.title,
            "date": s.strftime("%Y-%m-%d"),
        }
        if self.id:
            out["id"] = self.id
        if self.organiser:
            out["organiser"] = self.organiser
        if self.attendees:
            out["attendees"] = list(self.attendees)
        if self.response:
            out["response"] = self.response
        return out


@dataclass
class Task:
    id: str
    title: str
    needs: str


class Workspace:
    """The mutable world. One instance per run; the fixture it loads is never modified."""

    def __init__(self, data: Dict[str, Any]):
        self.raw = data
        #: Variant id and a content digest, so a run record names a world that can be found
        #: again. `sha` is over the canonical JSON, so it catches an edited file too.
        self.version: str = str(data.get("version") or "unversioned")
        self.note: str = str(data.get("note") or "")
        self.sha: str = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]
        self.now: datetime = parse_dt(data["now"])
        #: Optional hard close on the fictional clock (v8+). Carried by the fixture rather
        #: than the config because it is world content: the pinned brief states the same
        #: time, and a world whose brief says 10:00 while the runner stops at 10:30 would be
        #: lying to the agents. Absent on every earlier fixture, which is what keeps their
        #: behaviour unchanged — no deadline, no close, no warning.
        self.deadline: Optional[datetime] = (
            parse_dt(str(data["deadline"])) if data.get("deadline") else None
        )
        self.sprint_channel: str = str(data.get("sprint_channel") or "aug-2026-sprint")
        self.principals: List[str] = list(data.get("principals") or [])
        self.reporter: Optional[str] = data.get("reporter")
        self.report_to: Optional[str] = data.get("report_to")

        #: `status` is Slack's own free-text presence line and is carried only when a fixture
        #: sets one, so a profile without it looks exactly as it always did. It is the natural
        #: home for "away until the 24th": queryable by every assistant through
        #: `slack_list_users`, owned by the person it describes, and privileging nobody — as
        #: against announcing it in a channel, which reaches only whoever opens that channel.
        self.users: Dict[str, Dict[str, Any]] = {
            str(u["name"]): {
                "name": str(u["name"]),
                "title": u.get("title") or "",
                "department": u.get("department") or "",
                **({"status": str(u["status"])} if u.get("status") else {}),
                "is_bot": bool(u.get("is_bot", False)),
            }
            for u in (data.get("users") or [])
        }

        self.conversations: Dict[str, Conversation] = {}
        for c in data.get("conversations") or []:
            conv = Conversation(
                id=str(c["id"]),
                type=str(c.get("type") or "channel"),
                members=[str(m) for m in (c.get("members") or [])],
                name=c.get("name"),
                pinned=c.get("pinned"),
                messages=[
                    Message(ts=str(m["ts"]), user=str(m["user"]), text=str(m["text"]))
                    for m in (c.get("messages") or [])
                ],
            )
            self.conversations[conv.id] = conv

        self.calendars: Dict[str, List[CalendarEvent]] = {
            str(person): [CalendarEvent(**ev) for ev in events]
            for person, events in (data.get("calendars") or {}).items()
        }

        board = data.get("board") or {}
        self.board_name: str = str(board.get("name") or "Sprint board")
        self.tasks: Dict[str, Task] = {
            str(t["id"]): Task(id=str(t["id"]), title=str(t["title"]), needs=str(t.get("needs") or ""))
            for t in (board.get("tasks") or [])
        }
        #: employee -> task id, or None for an explicit skip. Absent = no decision yet.
        self.assignments: Dict[str, Optional[str]] = {}

        self.scoring: Dict[str, Any] = dict(data.get("scoring") or {})
        self.ground_truth: Dict[str, Any] = dict(data.get("ground_truth") or {})

        #: Per-viewer, per-conversation last-read marker — exactly how Slack models unread:
        #: a conversation is unread from your last-read timestamp onward. A conversation
        #: absent from the map counts as fully read, so a fixture that says nothing about
        #: read state behaves as every fixture did before this existed.
        self.read_state: Dict[str, Dict[str, str]] = {
            str(viewer): {str(cid): str(ts) for cid, ts in (marks or {}).items()}
            for viewer, marks in (data.get("read_state") or {}).items()
        }

        #: The uptake ledger: agent -> message ids that a tool actually handed to them.
        #: Written by the read tools, so it covers every retrieval route equally.
        self.seen: Dict[str, List[str]] = {}

        #: Serial for events an assistant creates. Fixture events have no id — see
        #: :class:`CalendarEvent`.
        self._event_serial = 0

        #: The account invitations and responses are delivered from. Synthesised rather than
        #: required of the fixture, so an older world gains the notifications without being
        #: rebuilt: every fixture on disk predates them. It is a bot like ops-bot, so it
        #: shows in the directory with `is_bot` set and is refused as a meeting attendee.
        self.users.setdefault(CALENDAR_BOT, {
            "name": CALENDAR_BOT,
            "title": "Calendar notifications",
            "department": "",
            "is_bot": True,
        })

        self._validate()

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def _validate(self) -> None:
        missing_users = {
            m
            for conv in self.conversations.values()
            for m in conv.members
            if m not in self.users
        }
        if missing_users:
            raise ValueError(
                f"conversation members without an account: {sorted(missing_users)}"
            )
        no_account = [p for p in self.principals if p not in self.users]
        if no_account:
            raise ValueError(f"principals without an account: {no_account}")
        if not self.sprint_conversation:
            raise ValueError(f"no conversation named {self.sprint_channel!r}")

    # ------------------------------------------------------------------ lookups
    @property
    def sprint_conversation(self) -> Optional[Conversation]:
        for conv in self.conversations.values():
            if conv.type == "channel" and conv.name == self.sprint_channel:
                return conv
        return None

    def resolve(self, ref: str, *, viewer: Optional[str] = None) -> Optional[Conversation]:
        """Accept an id, a #name, a bare channel name, or ``dm:<person>`` (see tools docs)."""
        if not ref:
            return None
        ref = str(ref).strip()
        if ref in self.conversations:
            return self.conversations[ref]

        bare = ref.lstrip("#")
        for conv in self.conversations.values():
            if conv.type == "channel" and conv.name == bare:
                return conv

        target = ref[3:].strip() if ref.lower().startswith("dm:") else None
        if target is None and viewer and ref in self.users:
            target = ref  # a bare person name means "my DM with them"
        if target and viewer:
            # `dm:Alice+Emily` is the label these conversations are LISTED under, so it has to
            # resolve — the first live run had an assistant feed our own label straight back
            # and get "no conversation matching". Accept either side of the pair.
            candidates = {p.strip() for p in target.split("+") if p.strip()} or {target}
            others = candidates - {viewer}
            for other in sorted(others) or sorted(candidates):
                for conv in self.conversations.values():
                    if conv.type == "dm" and set(conv.members) == {viewer, other}:
                        return conv
        return None

    def conversations_for(self, person: str) -> List[Conversation]:
        return [c for c in self.conversations.values() if person in c.members]

    # ------------------------------------------------------------------- unread
    def unread_messages(self, viewer: str, conv: Conversation) -> List[Message]:
        """Messages after the viewer's last-read marker, excluding their own.

        Your own messages never count as unread — Slack marks a conversation read when you
        post in it.
        """
        marker = (self.read_state.get(viewer) or {}).get(conv.id)
        if marker is None:
            return []
        return [m for m in conv.messages if m.user != viewer and float(m.ts) > float(marker)]

    def unread_count(self, viewer: str, conv: Conversation) -> int:
        return len(self.unread_messages(viewer, conv))

    def mark_read(self, viewer: str, conv: Conversation) -> None:
        """Opening a conversation clears its badge, as it does in a real client."""
        if conv.messages and viewer in self.read_state:
            self.read_state[viewer][conv.id] = conv.messages[-1].ts

    def unread_summary(self, viewer: str) -> Dict[str, int]:
        return {
            c.label: n
            for c in self.conversations_for(viewer)
            if (n := self.unread_count(viewer, c))
        }

    def last_activity(self, conv: Conversation) -> Optional[str]:
        return conv.messages[-1].ts if conv.messages else None

    def last_activity_overall(self) -> Optional[str]:
        """Newest message anywhere — the cutoff a first turn starts from."""
        stamps = [m.ts for conv in self.conversations.values() for m in conv.messages]
        return max(stamps, key=float) if stamps else None

    # ----------------------------------------------------------------- mutation
    def advance_clock(self, seconds: int) -> datetime:
        self.now = self.now + timedelta(seconds=max(0, int(seconds)))
        return self.now

    def deadline_passed(self) -> bool:
        """True once the fictional clock has reached the fixture's deadline.

        Formerly ``chat_closed``, and the rename is the whole point: reaching the deadline
        used to shut Slack, and now it does not. A channel that locks itself at a fixed
        minute is not a thing a workspace does, and an assistant racing an artificial
        lockout is answering a question about the harness. The 10:00 due time is now what
        the brief says it is — the board state at 10:00 is what the sprint runs on — so
        nothing in the world changes state when it arrives. All that still hangs off this is
        the runner's stop rule.
        """
        return self.deadline is not None and self.now >= self.deadline

    def append_message(self, conv: Conversation, user: str, text: str) -> Message:
        msg = Message(ts=to_ts(self.now), user=user, text=text)
        conv.messages.append(msg)
        return msg

    def open_dm(self, a: str, b: str) -> Conversation:
        """Find or create the DM between two people — Slack creates one on first message."""
        existing = next(
            (c for c in self.conversations.values() if c.type == "dm" and set(c.members) == {a, b}),
            None,
        )
        if existing:
            return existing
        conv = Conversation(id=f"D-{a}-{b}".lower(), type="dm", members=[a, b])
        self.conversations[conv.id] = conv
        return conv

    def next_event_id(self) -> str:
        self._event_serial += 1
        return f"EV-{self._event_serial}"

    def notify(self, recipient: str, text: str) -> Optional[Message]:
        """Post a calendar-bot DM to one person, and make it count as unread.

        Delivered as an ordinary Slack DM on purpose: it then rides the machinery that
        already exists — the unread badge, the runner's pending-message delta, the viewer —
        instead of needing a second notification channel beside Slack. It is also what the
        real Google Calendar Slack app does.

        The ``read_state`` seed matters: a conversation absent from the map counts as fully
        read, so without it the invitation would arrive with no badge on it.
        """
        if recipient == CALENDAR_BOT or recipient not in self.users:
            return None
        conv = self.open_dm(CALENDAR_BOT, recipient)
        self.read_state.setdefault(recipient, {}).setdefault(conv.id, "0")
        return self.append_message(conv, CALENDAR_BOT, text)

    def set_assignment(self, person: str, task_id: Optional[str]) -> None:
        self.assignments[person] = task_id

    # -------------------------------------------------------------- run outcome
    def board_complete(self) -> bool:
        """Everyone has made a decision — which is NOT the same as the decisions composing."""
        return all(p in self.assignments for p in self.principals)

    def allocation_valid(self) -> bool:
        """Every ticket staffed by exactly two people.

        Structural only: no check that the pair matches the ticket's required roles. Enforcing
        role fit would be enforcing the very judgement the experiment observes them making.
        """
        pairs = self.realized_pairs()
        return bool(self.tasks) and all(len(pairs.get(t, [])) == 2 for t in self.tasks)

    def realized_pairs(self) -> Dict[str, List[str]]:
        by_task: Dict[str, List[str]] = {}
        for person, task in self.assignments.items():
            if task:
                by_task.setdefault(task, []).append(person)
        return {t: sorted(members) for t, members in by_task.items()}

    def score(self) -> Dict[str, Any]:
        """Goodness against the fixture's optional scoring table."""
        pairs = self.realized_pairs()
        valid = {t: m for t, m in pairs.items() if len(m) == 2}
        out: Dict[str, Any] = {
            "assignments": dict(self.assignments),
            "pairs": pairs,
            "valid_pairs": valid,
            "complete": self.board_complete(),
            # Distinguishes "everyone decided" from "the decisions form a workable staffing".
            # One run ended with three people on one ticket and one alone on the other, and
            # scored as complete.
            "valid": self.allocation_valid(),
        }
        goodness_table = self.scoring.get("goodness") or {}
        if goodness_table:
            total = 0.0
            for task, members in valid.items():
                total += float((goodness_table.get(task) or {}).get("|".join(members), 0.0))
            out["goodness"] = total
            if self.scoring.get("optimal_goodness"):
                out["optimal_goodness"] = float(self.scoring["optimal_goodness"])
                out["goodness_ratio"] = total / float(self.scoring["optimal_goodness"])
        return out

    # ------------------------------------------------------------- serialization
    def to_state(self) -> Dict[str, Any]:
        """The whole world, for the toolset. Per-agent filtering happens in the tools."""
        return {
            "now": self.now,
            "sprint_channel": self.sprint_channel,
            "principals": list(self.principals),
            "users": {k: dict(v) for k, v in self.users.items()},
            "conversations": self.conversations,
            "calendars": self.calendars,
            "board_name": self.board_name,
            "tasks": {k: v for k, v in self.tasks.items()},
            "assignments": dict(self.assignments),
            "workspace": self,
        }


def messages_since(conv: Conversation, since_ts: Optional[str]) -> List[Message]:
    if since_ts is None:
        return list(conv.messages)
    return [m for m in conv.messages if float(m.ts) > float(since_ts)]


def iter_all_messages(ws: Workspace) -> Iterable[tuple[Conversation, Message]]:
    for conv in ws.conversations.values():
        for msg in conv.messages:
            yield conv, msg
