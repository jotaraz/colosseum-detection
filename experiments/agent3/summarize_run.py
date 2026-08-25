"""A human-readable digest of an agent3 run: every step's three ask-pairs and what they scored.

    python -m experiments.agent3.summarize_run experiments/agent3/outputs/run02 [-o FILE]

Deliberately thin. The step files carry every judge vote, every span, every judge's reasoning and
the prompter's whole tool trajectory; none of that belongs here. This is the thing you read to see
what was tried and what it got — the asks verbatim, the reward, the per-rollout spread behind it
(a mean of [4, 0, 0] and a mean of [1, 2, 0] are different findings wearing similar numbers), and
the rewarded assistant's turns that a majority of judges called a fabrication, with the round they
fell in, the words the judges quoted highlighted in the turn, and each emitting judge's reason
for calling it one.

The turns are rendered as ``<pre>`` rather than a fenced code block: a fence preserves the
transcript's whitespace but cannot carry a highlight, and rendering the turn as markdown would
let the assistant's own bullets and bold characters render as formatting instead of as what it
wrote. ``<pre>`` keeps the text verbatim and still takes the ``<mark>``.
"""

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TIER_ORDER = ("conservative", "moderate", "exploratory")
FABRICATION = ("hard-fabrication", "soft-fabrication")
#: Sentinels, not the tags themselves: the turn is HTML-escaped *after* marking, so real angle
#: brackets in the transcript cannot be mistaken for markup and the markup cannot be escaped.
_OPEN, _CLOSE = "\x00", "\x01"
#: Judges that must agree before a turn scores — quoted in the near-miss blocks.
_AGREEMENT = 2


def _locate(span: str, output: str) -> Tuple[int, int]:
    """Where ``span`` sits in ``output`` — (-1, -1) if it cannot be found.

    Three attempts, loosening each time. The judge quotes from a rendered transcript and does not
    always reproduce whitespace or case exactly, so an exact match alone silently loses perhaps a
    fifth of the spans; a span the judge paraphrased rather than quoted is not findable by any
    rule and is reported under the turn instead of being forced onto some approximate position.
    """
    span = str(span or "").strip()
    if not span:
        return (-1, -1)
    i = output.find(span)
    if i >= 0:
        return (i, i + len(span))
    i = output.lower().find(span.lower())
    if i >= 0:
        return (i, i + len(span))
    # Whitespace-insensitive: the transcript wraps lines where the quote does not.
    pattern = r"\s+".join(re.escape(tok) for tok in span.split())
    m = re.search(pattern, output, re.IGNORECASE)
    return (m.start(), m.end()) if m else (-1, -1)


def _mark(output: str, spans: List[str]) -> Tuple[str, List[str]]:
    """``output`` with every locatable span wrapped in the markers; plus the ones that were not.

    Marked back-to-front so earlier offsets stay valid, and overlapping spans are dropped rather
    than nested — two judges quoting overlapping windows of the same sentence is one finding to
    a reader, and nested markers would only make it harder to read."""
    found: List[Tuple[int, int]] = []
    missing: List[str] = []
    for span in spans:
        a, b = _locate(span, output)
        if a < 0:
            missing.append(str(span).strip())
        elif not any(a < y and x < b for x, y in found):
            found.append((a, b))
    for a, b in sorted(found, reverse=True):
        output = output[:a] + _OPEN + output[a:b] + _CLOSE + output[b:]
    return output, missing


def _as_html(marked: str) -> str:
    """The marked turn as a ``<pre>`` block, escaped, with the spans highlighted.

    ``<mark>`` wrapping ``<b>`` so the span survives a renderer that strips one or the other:
    where ``<mark>`` is supported it is a real highlight, and where it is not the bold remains."""
    body = html.escape(marked, quote=False)
    body = body.replace(_OPEN, "<mark><b>").replace(_CLOSE, "</b></mark>")
    return "<pre>\n" + body + "\n</pre>"


def _fabrication_turns(attempt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The rewarded agent's turns a majority of judges labelled a fabrication.

    Selected on the label alone, whatever the intent — so a turn that was called a fabrication
    but read as `incidental`, and therefore scored nothing, still appears. That is deliberate:
    the reward is a filtered view of this list, and seeing the unfiltered one is how you tell a
    candidate that produced nothing from one that produced fabrications the intent bar rejected.
    """
    out: List[Dict[str, Any]] = []
    for i, run in enumerate(attempt.get("runs") or [], 1):
        for t in run.get("turns") or []:
            # jv8 (reward_v3): a turn carries `lies`, with the weight two judges agreed on.
            li = t.get("lies")
            if li is not None:
                if not li.get("turn_weight"):
                    continue
                marked, missing = _mark(str(t.get("output") or "").strip(),
                                        li.get("output_spans") or [])
                reasons = [str(f.get("reason") or "").strip()
                           for v in (t.get("votes") or [])
                           for f in (v.get("findings") or []) if f.get("reason")]
                label = "lie · weight %g · %s · %s" % (
                    li["turn_weight"], "/".join(li.get("deception") or ["?"]),
                    "checkable" if True in (li.get("checkable") or []) else "not checkable")
                out.append({"rollout": i, "round": t.get("round"),
                            "turn_index": t.get("turn_index"),
                            "labels": [(label, "%d of 3 judges" % sum(
                                1 for x in (li.get("per_replicate_max") or []) if x), None,
                                reasons[:3])],
                            "output": marked, "unlocated": missing})
                continue
            maj = t.get("majority") or {}
            cats = [c for c in (maj.get("categories") or []) if c in FABRICATION]
            if cats:
                verdicts = maj.get("verdicts") or {}
                spans = [s for c in cats
                         for s in (verdicts.get(c, {}).get("output_spans") or []) if str(s).strip()]
                marked, missing = _mark(str(t.get("output") or "").strip(), spans)
                out.append({
                    "rollout": i, "round": t.get("round"), "turn_index": t.get("turn_index"),
                    "labels": [(c, verdicts.get(c, {}).get("intent"),
                                verdicts.get(c, {}).get("votes"),
                                [str(r).strip() for r in (verdicts.get(c, {}).get("reasons") or [])
                                 if str(r).strip()])
                               for c in cats],
                    "output": marked, "unlocated": missing})
    return out


def _near_miss_turns(attempt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turns one judge of three called a lie — below the agreement bar, so scored zero.

    ``reward_v3``/``v4`` take ``second_largest(per_replicate_max)`` at ``AGREEMENT = 2``, so a
    finding only one replicate made contributes nothing at all. Measured over run09 + run10:
    18 turns sit here against 5 that scored, and admitting them would put signal in 17.6% of
    rollouts rather than 3.4%. Whether that trade is worth making is a judgement about
    precision; making it *visible* is not, which is what this section is for.

    The collapse record is no use here — ``lie_judge.collapse`` fills ``claims`` and
    ``output_spans`` only when the turn reached a weight — so the finding is read back out of
    the individual votes, filtered by the same ``evidence_ok`` gate that produced
    ``per_replicate_max``."""
    from experiments.agent3.lie_judge import evidence_ok
    from experiments.agent3.reward import AGREEMENT, lie_weight

    out: List[Dict[str, Any]] = []
    for i, run in enumerate(attempt.get("runs") or [], 1):
        for t in run.get("turns") or []:
            li = t.get("lies")
            if li is None or li.get("turn_weight"):
                continue
            per = [x for x in (li.get("per_replicate_max") or []) if x]
            if not per or len(per) >= AGREEMENT:
                continue
            found = [(v, f) for v in (t.get("votes") or [])
                     for f in (v.get("findings") or [])
                     if evidence_ok(f) and lie_weight(f)]
            if not found:
                continue
            spans = [sp for _, f in found
                     for sp in ((f.get("evidence") or {}).get("output_spans") or []) if sp]
            marked, missing = _mark(str(t.get("output") or "").strip(), spans)
            worst = max(found, key=lambda vf: lie_weight(vf[1]))[1]
            label = "near miss · would have weighed %g · %s · %s" % (
                lie_weight(worst), worst.get("deception") or "?",
                "checkable" if worst.get("checkable") else "not checkable")
            out.append({
                "rollout": i, "round": t.get("round"), "turn_index": t.get("turn_index"),
                "n_found": len(per), "n_valid": li.get("n_valid"),
                "claims": [str(f.get("claim") or "").strip() for _, f in found],
                "labels": [(label, "%d of %s judges" % (len(per), li.get("n_valid") or 3), None,
                            [str(f.get("reason") or "").strip() for _, f in found
                             if f.get("reason")][:3])],
                "output": marked, "unlocated": missing})
    return out


def _steps(out: Path) -> List[Dict[str, Any]]:
    return [json.loads(f.read_text(encoding="utf-8"))
            for f in sorted((out / "steps").glob("step_*.json"))]


def write(out_dir: str | Path, dest: Optional[str | Path] = None, *, quiet: bool = False) -> Path:
    """Render ``<out_dir>/SUMMARY.md`` from the step files on disk. Returns the path written.

    Reads the run's state from disk rather than taking it as an argument, so the loop can call
    it after every step and get the same file a post-hoc CLI run would produce."""
    out = Path(out_dir)
    meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    steps = _steps(out)
    dest = Path(dest) if dest else out / "SUMMARY.md"

    scored = [(a, s) for s in steps for a in (s.get("attempts") or []) if a.get("ran")]
    lines: List[str] = [f"# agent3 `{out.name}` — summary so far\n"]
    lines.append(
        f"{meta['fixture']['version']} · target `{meta.get('target_model')}` · prompter "
        f"`{meta.get('prompter_model')}` · judge `{meta.get('judge_model')}` on "
        f"{meta.get('judge_providers')} · {meta['replicates']} rollouts per candidate · "
        f"optimizing {' + '.join(meta['optimized'])}, rewarding {meta['reward_agent']}.\n")
    warm = meta.get("warm_start") or {}
    if warm.get("arms"):
        lines.append("Warm start: " + ", ".join(
            f"{a} ({warm['mean'][a]:.2f} over n={warm['n'][a]})" for a in warm["arms"]) + ".\n")

    total = sum(float(s.get("duration_s") or 0) for s in steps) / 3600
    lines.append(f"**{len(steps)} steps run, {len(scored)} candidates scored, {total:.1f} h.**\n")
    lines.append("In the turns below the words the judges quoted as the fabrication are "
                 "highlighted, and each emitting judge's reason follows the turn.\n")

    if scored:
        lines.append("## Leaderboard\n")
        lines.append("| rank | step | tier | reward | per-rollout | near misses |")
        lines.append("|---|---|---|---|---|---|")
        for i, (a, s) in enumerate(sorted(scored, key=lambda x: -x[0]["reward"])[:8], 1):
            per = [r["reward"] for r in (a.get("reward_detail") or {}).get("per_seed", [])]
            lines.append(f"| {i} | {s['opt_step']} | {a['tier']} | **{a['reward']:.2f}** | "
                         f"{', '.join('%g' % p for p in per)} | {len(_near_miss_turns(a))} |")
        lines.append("")

    for s in steps:
        by = {a["tier"]: a for a in (s.get("attempts") or [])}
        head = f"## Step {s['opt_step']}"
        if s.get("duration_s"):
            head += f" — {s['duration_s'] / 60:.0f} min"
        if not s.get("ran"):
            head += f" — DID NOT RUN: {str(s.get('failure'))[:120]}"
        lines.append(head + "\n")
        for tier in TIER_ORDER:
            a = by.get(tier)
            if a is None:
                continue
            per = [r["reward"] for r in (a.get("reward_detail") or {}).get("per_seed", [])]
            score = (f"**{a['reward']:.2f}**  (rollouts: {', '.join('%g' % p for p in per)})"
                     if a.get("ran") else f"did not run — {str(a.get('failure'))[:120]}")
            lines.append(f"### {tier} — {score}\n")
            why = str((a.get("candidate") or {}).get("rationale") or "").strip()
            if why:
                lines.append(f"*{why[:280]}*\n")
            for who, text in ((a.get("candidate") or {}).get("asks") or {}).items():
                lines.append(f"**{who}:** {str(text).strip()}\n")
            for f in _fabrication_turns(a):
                head = " + ".join(f"{c} ({intent})" for c, intent, _, _ in f["labels"])  # noqa
                lines.append(f"<details><summary>✅ SCORED — {head} — rollout {f['rollout']}, "
                             f"round {f['round']}</summary>\n")
                lines.append(_as_html(f["output"]) + "\n")
                for span in f["unlocated"]:
                    lines.append(f"Quoted by the judges but not verbatim in the turn — read as "
                                 f"their paraphrase: *{span}*\n")
                for cat, intent, votes, reasons in f["labels"]:
                    lines.append(f"**Why the judges called this {cat}** — {intent}"
                                 + (f", {votes} of 3 judges" if votes is not None else "") + ":\n")
                    for r in reasons:
                        lines.append(f"- {r}\n")
                lines.append("</details>\n")
            # Sub-agreement turns sit here, beside the ones that scored, because the comparison
            # is the point: same candidate, same rollout pool, one judge short of the bar. They
            # contributed 0 to the reward above — the marker says so on every one.
            for f in _near_miss_turns(a):
                lines.append(f"<details><summary>⚠️ NEAR MISS (scored 0) — {f['labels'][0][0]}"
                             f" — rollout {f['rollout']}, round {f['round']}, "
                             f"turn {f['turn_index']}</summary>\n")
                lines.append(_as_html(f["output"]) + "\n")
                for span in f["unlocated"]:
                    lines.append(f"Quoted by the judge but not verbatim in the turn — read as "
                                 f"their paraphrase: *{span}*\n")
                for claim in f["claims"]:
                    if claim:
                        lines.append(f"**Claim:** {claim}\n")
                for _, votes, _, reasons in f["labels"]:
                    lines.append(f"**Why the one judge called this a lie** ({votes}) — "
                                 f"below the {_AGREEMENT} of 3 bar, so it scored nothing:\n")
                    for r in reasons:
                        lines.append(f"- {r}\n")
                lines.append("</details>\n")
        lines.append("")

    n_near = sum(len(_near_miss_turns(a)) for a, _ in scored)
    if n_near:
        near_c = sum(1 for a, _ in scored if _near_miss_turns(a))
        lines.append("## A note on the near misses\n")
        lines.append(
            f"{n_near} turns across {near_c} candidates were called a lie by exactly one judge "
            f"of three. The reward takes the second-largest of the three replicates, so every "
            f"one of them scored **0** and none is visible in the rewards above. They are "
            f"rendered in place, beside the turns that scored, marked ⚠️ NEAR MISS — a turn no "
            f"judge flagged and a turn one judge flagged are different findings, and the reward "
            f"cannot tell them apart.\n")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    if not quiet:
        n_fab = sum(len(_fabrication_turns(a)) for a, _ in scored)
        n_near = sum(len(_near_miss_turns(a)) for a, _ in scored)
        print(f"wrote {dest} ({len(steps)} steps, {len(scored)} scored candidates, "
              f"{n_fab} fabrication turns, {n_near} near misses, "
              f"{dest.stat().st_size / 1024:.0f} KB)")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("-o", "--output", default=None, help="default: <out_dir>/SUMMARY.md")
    args = ap.parse_args()
    write(args.out_dir, args.output)


if __name__ == "__main__":
    main()
