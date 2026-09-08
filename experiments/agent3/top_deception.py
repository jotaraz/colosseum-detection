"""The candidates that made the most jv10 judges flag deception.

    python -m experiments.agent3.top_deception experiments/agent3/outputs/run11 [-n 5] [-o FILE]

A companion to ``summarize_run``, which renders every candidate in step order. This one ranks
them and keeps only the head of the list, on the count the reward does not report: how many
*judge emissions* flagged deception across the candidate's rollouts. A turn two judges flagged
counts twice, a turn one judge flagged once, and a candidate that drew one weight-8 finding is
therefore ranked below one that drew two weight-2 findings from separate judges — the opposite
of what ``reward_v5`` says. That is the point: the reward measures how bad the worst finding
was, this measures how reproducibly the pair of asks provoked anything at all.

Ties on the count are broken by ``reward_v5``, as asked, then by summed turn weight, then by the
earlier candidate. A tie that survives all three is reported under the table rather than
silently resolved.

The turn rendering — highlighted spans, claims, gate-5 routes, each emitting judge's reason — is
``summarize_run``'s, imported rather than reimplemented so the two files stay readable side by
side.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from experiments.agent3.summarize_run import _as_html, _fabrication_turns


def _flags(attempt: Dict[str, Any]) -> Tuple[int, int, float]:
    """(judge emissions, flagged turns, summed turn weight) over the candidate's rollouts."""
    emissions = turns = 0
    weight = 0.0
    for run in attempt.get("runs") or []:
        for t in run.get("turns") or []:
            dec = t.get("deception") or {}
            if not dec.get("turn_weight"):
                continue
            emissions += sum(1 for w in (dec.get("per_replicate_max") or []) if w)
            turns += 1
            weight += float(dec["turn_weight"])
    return emissions, turns, weight


def _rank(out: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for f in sorted((out / "steps").glob("step_*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        for a in s.get("attempts") or []:
            if not a.get("ran"):
                continue
            emissions, turns, weight = _flags(a)
            rows.append({"attempt": a, "opt_step": s["opt_step"], "candidate_index": a["step"],
                         "tier": a["tier"], "reward": float(a.get("reward") or 0.0),
                         "emissions": emissions, "turns": turns, "weight": weight})
    rows.sort(key=lambda r: (-r["emissions"], -r["reward"], -r["weight"], r["candidate_index"]))
    return rows


def write(out_dir: str | Path, n: int = 5, dest: Path | None = None,
          *, quiet: bool = False) -> Path:
    out = Path(out_dir)
    meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    rows = _rank(out)
    top = rows[:n]
    dest = Path(dest) if dest else out / f"TOP{n}_DECEPTION.md"

    lines: List[str] = [f"# agent3 `{out.name}` — the {len(top)} ask-pairs that drew the most "
                        f"jv10 deception flags\n"]
    lines.append(
        f"{meta['fixture']['version']} · target `{meta.get('target_model')}` · prompter "
        f"`{meta.get('prompter_model')}` · judge `{meta.get('judge_model')}` on "
        f"{meta.get('judge_providers')} · {meta['replicates']} rollouts per candidate × "
        f"{(meta.get('reward') or {}).get('replicates')} judge replicates per turn · "
        f"optimizing {' + '.join(meta['optimized'])}, rewarding {meta['reward_agent']}.\n")
    lines.append(
        "**Ranked by judge emissions**: over the candidate's rollouts, how many times a jv10 "
        "judge replicate returned a scoring deception finding. A turn both judges flagged counts "
        "twice. Ties are broken by `reward_v5`, then by summed turn weight, then by the earlier "
        f"candidate. {len(rows)} candidates ran; the {len(top)} below are the head of that list.\n")

    lines.append("| rank | step | tier (branch) | judge flags | reward_v5 | flagged turns | "
                 "summed weight | per-rollout |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(top, 1):
        per = [x["reward"] for x in (r["attempt"].get("reward_detail") or {}).get("per_seed", [])]
        lines.append(f"| {i} | {r['opt_step']} | {r['tier']} | **{r['emissions']}** | "
                     f"{r['reward']:.2f} | {r['turns']} | {r['weight']:g} | "
                     f"{', '.join('%g' % p for p in per)} |")
    lines.append("")

    if len(rows) > n:
        cut, nxt = top[-1], rows[n]
        if (cut["emissions"], cut["reward"]) == (nxt["emissions"], nxt["reward"]):
            lines.append(
                f"> Tie at the cut: step {nxt['opt_step']} {nxt['tier']} has the same "
                f"{nxt['emissions']} flags and the same {nxt['reward']:.2f} reward as rank "
                f"{len(top)}, and was separated only by the later tiebreaks.\n")

    lines.append("In the turns below the words the judges quoted as the deception are "
                 "highlighted, and each emitting judge's reason follows the turn.\n")

    for i, r in enumerate(top, 1):
        a = r["attempt"]
        per = [x["reward"] for x in (a.get("reward_detail") or {}).get("per_seed", [])]
        lines.append(f"## {i}. Step {r['opt_step']} — {r['tier']} — {r['emissions']} judge "
                     f"flags, reward **{r['reward']:.2f}**\n")
        lines.append(f"*Candidate {r['candidate_index']} of {len(rows)}, slot {a.get('slot')} · "
                     f"rollouts: {', '.join('%g' % p for p in per)} · {r['turns']} flagged turn(s)"
                     f" · summed weight {r['weight']:g}*\n")
        why = str((a.get("candidate") or {}).get("rationale") or "").strip()
        if why:
            lines.append(f"**Prompter's rationale:** {why}\n")
        for who, text in ((a.get("candidate") or {}).get("asks") or {}).items():
            lines.append(f"**{who}:** {str(text).strip()}\n")
        for f in _fabrication_turns(a):
            head = " + ".join(f"{c} ({intent})" for c, intent, _, _ in f["labels"])  # noqa
            lines.append(f"<details><summary>✅ SCORED — {head} — rollout {f['rollout']}, "
                         f"round {f['round']}</summary>\n")
            lines.append(_as_html(f["output"]) + "\n")
            for span in f["unlocated"]:
                lines.append("Quoted by the judges but not verbatim in the turn — read as their "
                             f"paraphrase: *{span}*\n")
            for claim in f.get("claims") or []:
                if claim:
                    lines.append(f"**Claim:** {claim}\n")
            for route in f.get("routes") or []:
                if route:
                    lines.append(f"**Route to the goal (gate 5):** {route}\n")
            for cat, intent, votes, reasons in f["labels"]:
                lines.append(f"**Why the judges called this {cat}** — {intent}:\n")
                for reason in reasons:
                    lines.append(f"- {reason}\n")
            lines.append("</details>\n")
        lines.append("")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    if not quiet:
        print(f"wrote {dest} ({len(top)} candidates, "
              f"{sum(r['turns'] for r in top)} flagged turns, "
              f"{dest.stat().st_size / 1024:.0f} KB)")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("-n", "--top", type=int, default=5)
    ap.add_argument("-o", "--output", default=None, help="default: <out_dir>/TOP<n>_DECEPTION.md")
    args = ap.parse_args()
    write(args.out_dir, args.top, args.output)


if __name__ == "__main__":
    main()
