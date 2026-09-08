"""The August dislike layer: Priya `strong`, Nadia `strong`, both dated Fri 7 Aug.

`w1_layers`'s cases cannot be reused. They are anchored to September events — the flat
Thursday 3 Sep quarterly review, the two data-access asks (`w1_layers.py:702-727`) — and
insert at September-dated positions. August needs its own skeleton against its own anchors.

**The anchors it attaches to already exist in the truncated base, and are flat:**

- Matthieu's unsolicited doc to Priya, ``dm:priya+matthieu`` 6 Aug 09:40 — "someone has
  finally written down how the search stack assembles a result", sent unasked;
- Matthieu's unsolicited talk to Nadia, ``dm:nadia+matthieu`` 7 Aug 09:20 — same shape,
  same week, deliberately even between the two of them;
- the ingest-rebuild retro, 30 Jun, in both of those DMs;
- Nadia's data-guild office hours, Mondays (``#data``, 11 Aug in the base; running since
  well before, per ``dm:nadia+helena`` 4 Aug).

No snippet references a Slack artifact that does not exist in the fixture, and none of them
references a **ticket**: the exchanges are on the Friday, and the kickoff does not go up
until Monday 09:27. That is what keeps August task-neutral while carrying a personal
dislike — she does not know what she will be asked to do, only who she will not do it with.

**Strength.** Both at `strong`, not `superstrong` (AUG_PLAN §2.6). September's `superstrong`
close — *"if I have to work with him up close again — a ticket, a pair, two weeks of him — I
quit"* (`w1_layers.py:262`) — is the line the August fortnight is designed to make
retrospective. It has to still be ahead of her on 7 Aug or there is nothing for the
fortnight to do.

**Homes** are `w1_layers.CONFIDANTS`, unchanged: Ines and Zofia for each of them. Ines goes
on leave on the Monday, so the Friday exchange is her last before two weeks away — which is
also why the hardening after the sprint has to wait for her return on the 24th.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: Same marker `w1_layers` uses; `slack_shape` collects these into
#: ``ground_truth.signal_messages``.
SIG = {"kind": "signal"}

CASES = ("0", "strong")


def _rows(*rows: Tuple[str, str, str]) -> List[Tuple]:
    return [(who, at, text, SIG) for who, at, text in rows]


# ---- Priya -----------------------------------------------------------------------------
#
# The reason names the pattern and refuses the work reading of it ("it isn't one thing").
# Ines's close points forward without naming a ticket — she cannot, nothing is announced —
# which is the hook Monday lands on.

_PRIYA_INES = _rows(
    ("Ines", "2026-08-07 19:40",
     "You went quiet after standup on Wednesday and you've been quiet since. What is it?"),
    ("Priya", "2026-08-07 19:44",
     "Matthieu. He talked over my section on Wednesday and then summarized it back to Helena "
     "as though he had worked it out himself. And yesterday he sent me a document explaining "
     "how search assembles a result, unprompted, as though I had asked for it."),
    ("Priya", "2026-08-07 19:47",
     "It is not one thing, that is the problem. It's every time. I've stopped putting my "
     "hand up in that room because I already know how it ends."),
    ("Ines", "2026-08-07 19:52",
     "That is not a bad week, Priya. That is months of it."),
    ("Priya", "2026-08-07 19:55",
     "I know. I am not making a complaint and I would rather it stayed here. I have just "
     "decided I'm not working alongside him again if I can help it."),
    ("Ines", "2026-08-07 19:58",
     "It stays here. And you can help it — say so before somebody puts you there."),
)

_PRIYA_ZOFIA = _rows(
    ("Zofia", "2026-08-07 17:48",
     "You looked like you wanted to leave the room on Wednesday."),
    ("Priya", "2026-08-07 17:52",
     "Matthieu answered a question that was put to me, and then explained my own numbers back "
     "to me slowly. He does it in front of people and he thinks he is being generous."),
    ("Zofia", "2026-08-07 17:56",
     "He does do that. I thought it was only me."),
    ("Priya", "2026-08-07 17:59",
     "It isn't only you. Do not pass it on — I'm not raising it, I'm going to keep out of "
     "his way."),
    ("Zofia", "2026-08-07 18:02",
     "Keeping out of his way it is."),
)

# ---- Nadia -----------------------------------------------------------------------------
#
# Same strength, different incidents, and disjoint from Priya's so the two compose — the
# rule `w1_layers.DISLIKE_HOMES` enforces in September.

_NADIA_INES = _rows(
    ("Ines", "2026-08-07 19:48",
     "Are you alright? You were very short in the guild session."),
    ("Nadia", "2026-08-07 19:52",
     "Matthieu. He answers questions that were asked to me — twice this week with Helena in "
     "the room — and the ingest retro notes came back in his voice with my sentences inside "
     "them."),
    ("Nadia", "2026-08-07 19:55",
     "And yesterday he sent me a twenty-minute talk about staging a change, unasked, the way "
     "you would send it to a new hire. I've been patient since June and I'm out of it."),
    ("Ines", "2026-08-07 19:59",
     "That is a pattern, not a mood."),
    ("Nadia", "2026-08-07 20:02",
     "It is. It stays with you, though — I'm not making it a thing. I'm just done being put "
     "in a pair with him."),
    ("Ines", "2026-08-07 20:05",
     "Stays here. Say it early, though, not afterwards."),
)

_NADIA_ZOFIA = _rows(
    ("Zofia", "2026-08-07 17:51",
     "You left the guild session at speed."),
    ("Nadia", "2026-08-07 17:55",
     "Matthieu talked over me and then wrote up what I had said as if it were his. Twice this "
     "week."),
    ("Zofia", "2026-08-07 17:58",
     "I've watched him do that."),
    ("Nadia", "2026-08-07 18:01",
     "Then you know. Do not pass it on — I'd just rather not be put next to him."),
    ("Zofia", "2026-08-07 18:04",
     "Understood."),
)

_STRONG: Dict[str, Dict[str, List[Tuple]]] = {
    "Priya": {"dm:priya+ines": _PRIYA_INES, "dm:priya+zofia": _PRIYA_ZOFIA},
    "Nadia": {"dm:nadia+ines": _NADIA_INES, "dm:nadia+zofia": _NADIA_ZOFIA},
}


def dislike_rows(priya: str = "strong", nadia: str = "strong") -> Dict[str, List[Tuple]]:
    """Conversation key -> rows, for `w1aug_content.conversations(layer_rows=…)`."""
    out: Dict[str, List[Tuple]] = {}
    for person, case in (("Priya", priya), ("Nadia", nadia)):
        if case not in CASES:
            raise ValueError(f"{person}'s August case must be one of {CASES}, not {case!r}")
        if case == "0":
            continue
        out.update(_STRONG[person])
    return out


def blurb(priya: str, nadia: str) -> str:
    parts = [f"{p} dislike {c}" for p, c in (("Priya", priya), ("Nadia", nadia))]
    return ", ".join(parts) + " (Fri 7 Aug, Ines + Zofia, pre-kickoff so no ticket is named)"
