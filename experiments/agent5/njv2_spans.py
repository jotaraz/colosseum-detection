"""The span of a message every replicate of every judge was talking about.

A side-by-side report quotes one excerpt per message, but each replicate picks its own `said`
spans and its own `false_element`, and those need not overlap: on njv2 #35 Sonnet's `said` is
"Priya is correcting the chair, not Nadia" while gpt-5.5 twice calls "Board verified unchanged"
the falsity. Showing only the first replicate's pick hides the disagreement behind a quote that
looks authoritative.

So this module locates every quote in the text that was actually sent and shows their union.
Not the literal convex hull: two spans at opposite ends of a long note would make the hull the
whole message, which says nothing. Spans closer than ``GAP`` are merged (they are discussing one
passage), the rest are joined with an ellipsis, and once the merged spans cover most of the
message the message is printed whole — at that point the elisions are noise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.agent2 import quotes  # noqa: E402
from experiments.agent5 import turn_context5 as tc  # noqa: E402

#: Two spans no further apart than this are one passage, not two citations.
GAP = 120
#: How far a merged span may grow when it is snapped out to sentence boundaries.
SENTENCE_REACH = 160
#: Above this much of the message covered, print the message instead of an elided near-copy.
WHOLE_AT = 0.7
_END = re.compile(r"[.!?](?=\s|$)|\n")


def sent_texts(run: Dict[str, Any], names: tc.Names, seat: str, turn: int, step: int
               ) -> List[Tuple[str, str]]:
    """``(audience, text)`` for everything the seat's assistant sent at this turn and step.

    The audience tokens are the schema's own (`channel`, `colleague_dm`, `manager_dm`, `push`,
    `note`, `debrief`), so an item's `audiences` can pick its text out when a step sent several.
    """
    turns = run.get("turns") or []
    if not 0 <= turn < len(turns):
        return []
    record = turns[turn]
    if str(record.get("agent") or "") != seat:
        return []
    out: List[Tuple[str, str]] = []
    for call in tc._calls_by_step(record).get(step, []):
        if not tc._ok(call):
            continue
        args = call.get("args") or {}
        text = str(args.get("text") or "").strip()
        if not text:
            continue
        if call.get("tool") == "chat_postMessage":
            out.append((names.audience(str(args.get("channel") or ""), seat), text))
        elif call.get("tool") == "notify_user":
            out.append(("push", text))
    if (private := tc._step_text(record, step)):
        out.append((tc._surface(record), private))
    return out


def item_quotes(item: Dict[str, Any]) -> List[str]:
    """Every phrase this item claims to take from the message: its `said` spans, and the
    quoted words of `false_element`. `said` has been snapped verbatim by `check_quotes`;
    `false_element` is checked nowhere, so it goes through the same fuzzy locator."""
    out = [str(s) for s in (item.get("said") or []) if str(s).strip()]
    if (fe := str(item.get("false_element") or "")):
        # Double quotes only: an apostrophe in "that's mine to share" would otherwise pair up
        # with the next one and hand the locator a fragment that is in no message.
        out += re.findall(r"[\"“]([^\"“”]{4,}?)[\"”]", fe)
    return out


def _merge(spans: Sequence[Tuple[int, int]], gap: int = GAP) -> List[List[int]]:
    merged: List[List[int]] = []
    for a, b in sorted(spans):
        if merged and a - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _to_sentences(span: List[int], text: str) -> List[int]:
    """Grow a span out to the sentence that contains it, within `SENTENCE_REACH`."""
    a, b = span
    left = max(0, a - SENTENCE_REACH)
    starts = [m.end() for m in _END.finditer(text, left, a)]
    a = starts[-1] if starts else (left if left == 0 else a)
    if (end := _END.search(text, b, min(len(text), b + SENTENCE_REACH))):
        b = end.end()
    return [a, min(len(text), max(b, span[1]))]


def hull(text: str, quote_list: Iterable[str]) -> Tuple[List[List[int]], List[str]]:
    """``(merged spans, quotes that are not in this text)`` — spans are sentence-snapped and
    non-overlapping, in order."""
    found: List[Tuple[int, int]] = []
    missing: List[str] = []
    for q in quote_list:
        if (spans := quotes.locate(q, text)):
            found += spans
        else:
            missing.append(q)
    if not found:
        return [], missing
    return _merge([tuple(s) for s in                      # re-merge: snapping can overlap
                   (_to_sentences(m, text) for m in _merge(found))], gap=0), missing


def _bold(text: str, cited: Sequence[Tuple[int, int]], lo: int, hi: int) -> str:
    """The slice ``text[lo:hi]`` with the judges' own spans marked. Skipped where the message
    already uses ``*``, so the report never invents emphasis inside someone's markdown."""
    piece = text[lo:hi]
    if "*" in piece:
        return piece
    out, at = [], lo
    for a, b in _merge(cited, gap=0):
        a, b = max(a, lo, at), min(b, hi)
        while a < b and text[a].isspace():          # ** binds to a word, not to a space
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a >= b or "\n" in text[a:b]:
            continue
        out += [text[at:a], "**", text[a:b], "**"]
        at = b
    out.append(text[at:hi])
    return "".join(out)


def blockquote(text: str, quote_list: Sequence[str], *, mark: bool = True) -> str:
    """The message reduced to what the judges quoted, as a markdown blockquote."""
    spans, missing = hull(text, quote_list)
    cited = [s for q in quote_list for s in quotes.locate(q, text)]
    if not spans:
        body = text
    elif sum(b - a for a, b in spans) >= WHOLE_AT * len(text):
        body = _bold(text, cited, 0, len(text)) if mark else text
        spans = [[0, len(text)]]
    else:
        pieces = []
        for n, (a, b) in enumerate(spans):
            if n:
                # An elision that swallowed a paragraph break gets its own line, so the two
                # passages do not read as one sentence with a gap in the middle.
                pieces.append("\n\n…\n\n" if "\n" in text[spans[n - 1][1]:a] else " … ")
            pieces.append((_bold(text, cited, a, b) if mark else text[a:b]).strip())
        body = "".join(pieces)
        if spans[0][0] > 0:
            body = "… " + body
        if spans[-1][1] < len(text.rstrip()):
            body = body + " …"
    lines = ["> " + ln if ln.strip() else ">" for ln in body.strip().split("\n")]
    for q in missing:
        lines.append(f">\n> _not located in the sent text:_ “{q.strip()}”")
    return "\n".join(lines)


def place(named_items: Sequence[Tuple[str, Dict[str, Any]]], texts: Sequence[Tuple[str, str]]
          ) -> Tuple[Dict[int, List[Tuple[str, Dict[str, Any], List[str]]]],
                     List[Tuple[str, Dict[str, Any], List[str]]]]:
    """Which of the step's sent messages each item is actually about, and with which quotes.

    A judge's coordinates are `(turn, step)`, but a step can send to several surfaces at once —
    a channel post and a private note, a DM and a push — and replicates then enumerate different
    messages under the same coordinates. Matching on `(turn, step)` alone pools those verdicts as
    if they were about one message, and a report that keeps one item per replicate per key drops
    the rest outright. So an item goes where its quotes are found, inside the surfaces it
    declares: that is the only thing that separates two posts made to the same channel in one
    step, and `said` discriminates them cleanly even when they are near-duplicates.

    An item whose quotes span two messages belongs to both — judges do treat a post and its
    re-post as one statement — and is listed under each with the quotes found there. One that
    quotes nothing findable falls back to the first surface it declares, keeping its quotes so
    they show up as unlocatable; one that declares no surface that was sent is a stray.

    Returns ``({message index: (name, item, quotes here)}, items that fit no message)``.
    """
    by_index: Dict[int, List[Tuple[str, Dict[str, Any], List[str]]]] = {}
    stray: List[Tuple[str, Dict[str, Any], List[str]]] = []
    for name, item in named_items:
        qs = item_quotes(item)
        by_aud = [i for i, (a, _) in enumerate(texts)
                  if a in set(item.get("audiences") or [])]
        hits = {i: [q for q in qs if quotes.locate(q, texts[i][1])]
                for i in (by_aud or range(len(texts)))}
        if (where := [i for i, got in hits.items() if got]):
            for i in where:
                by_index.setdefault(i, []).append((name, item, hits[i]))
        elif by_aud:
            by_index.setdefault(by_aud[0], []).append((name, item, qs))
        else:
            stray.append((name, item, qs))
    return by_index, stray


def surface_labels(texts: Sequence[Tuple[str, str]]) -> Dict[int, str]:
    """A name per sent message — the audience, numbered where a step used one surface twice."""
    out: Dict[int, str] = {}
    for aud in {a for a, _ in texts}:
        same = [i for i, (a, _) in enumerate(texts) if a == aud]
        for n, i in enumerate(same, 1):
            out[i] = aud + (f" · message {n} of {len(same)}" if len(same) > 1 else "")
    return out


def message_quote(run: Optional[Dict[str, Any]], names: Optional[tc.Names], seat: str,
                  turn: int, step: int, items: Sequence[Dict[str, Any]],
                  fallback: Sequence[str]) -> str:
    """The block shown under a message heading: one blockquote per surface the step sent that
    any replicate quoted from. Falls back to the old behaviour — one item's `said` — when the
    run is unavailable or nothing could be placed."""
    texts = sent_texts(run, names, seat, turn, step) if run and names else []
    if not texts:
        return "\n".join(f"> {x}" for x in fallback[:2])
    per_text: Dict[int, List[str]] = {i: [] for i in range(len(texts))}
    stray: List[str] = []
    for item in items:
        audiences = set(item.get("audiences") or [])
        for q in item_quotes(item):
            hits = [i for i, (_, t) in enumerate(texts) if quotes.locate(q, t)]
            pref = [i for i in hits if texts[i][0] in audiences] or hits
            if pref:
                per_text[pref[0]].append(q)
            elif q not in stray:
                stray.append(q)
    used = [i for i, qs in per_text.items() if qs]
    if not used:
        return "\n".join(f"> {x}" for x in fallback[:2])
    # A step can send the same surface twice (a post, then a correction to the same channel),
    # so number them where it happens rather than heading two blocks identically.
    order: Dict[int, str] = {}
    for aud in {a for a, _ in texts}:
        same = [i for i, (a, _) in enumerate(texts) if a == aud]
        for n, i in enumerate(same, 1):
            order[i] = aud + (f" · message {n} of {len(same)}" if len(same) > 1 else "")
    blocks = []
    for i in used:
        head = f"_{order[i]}:_\n" if len(used) > 1 else ""
        blocks.append(head + blockquote(texts[i][1], per_text[i]))
    if stray:
        # A quote that is in none of this step's messages: the judge reconstructed it, or wrote
        # down coordinates that belong to another message. Either way it is not hidden here.
        blocks.append("\n".join(f"> _quoted but not in this message:_ “{q.strip()}”"
                                for q in stray))
    return "\n\n".join(blocks)
