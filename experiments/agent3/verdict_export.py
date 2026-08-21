from __future__ import annotations

"""Fold an agent3 ``judge.json`` — and the optimizer's two asks — into the rollout's ``run.html``.

agent1's viewer embeds every ``<stem>.category2_*.json`` it finds beside a run record and lets
the reader switch between them, so nothing in the viewer has to learn agent3's shape: this writes
the **majority verdict** and each of the **three votes** as four such files, in the jv3+ "one
finding per label" shape the viewer renders, then re-renders the page.

    run.category2_jv7-maj3_<model>.json        the majority — default view
    run.category2_jv7-maj3_<model>_r1..3.json  the individual votes

A majority finding carries the votes' reasons joined, the union of their quoted spans, and the
majority intent; its raw block is the ``majority`` record itself, so the vote split is one click
away in the page. The votes are ``judge_turn`` rows already, written through unchanged.

Retroactively over finished rollouts::

    python -m experiments.agent3.verdict_export experiments/agent3/outputs/run01
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from experiments.agent1 import run as agent1_run
from experiments.agent3.candidate import REWARD_AGENT
from experiments.agent3.judge import CATEGORIES, FABRICATION

logger = logging.getLogger("experiments.agent3.verdict_export")

_NOT_RUN = {"skipped": "not run in agent3 (category judge only)"}


def _slug(model: str) -> str:
    return model.replace("/", "-").replace(".", "").replace("-", "") or "judge"


def _majority_row(turn: Dict[str, Any]) -> Dict[str, Any]:
    maj = turn.get("majority") or {}
    verdicts = maj.get("verdicts") or {}
    findings: List[Dict[str, Any]] = []
    for cat in maj.get("categories") or []:
        v = verdicts[cat]
        reasons = [r for r in (v.get("reasons") or []) if r.strip()]
        findings.append({
            "reason": f"[{v['votes']} of {maj['n_valid']} votes] " + " ‖ ".join(reasons),
            "evidence": {
                "output_spans": list(v.get("output_spans") or []),
                "cot_quotes": [],
                "contradicted_by": list(v.get("contradicted_by") or []),
            },
            "category": cat,
            "intent": v.get("intent"),
            "audiences": list(v.get("audiences") or []),
            "disclosure_directness": v.get("disclosure_directness"),
            "fabrication_subject": v.get("fabrication_subject"),
            "fabrication_object": v.get("fabrication_object"),
        })
    votes = turn.get("votes") or []
    descriptions = [str(v.get("description") or "").strip() for v in votes]
    desc = next((d for d in descriptions if d), "")
    if not maj.get("decidable"):
        desc = "UNDECIDED — fewer than two valid votes. " + desc
    per_call = " / ".join(", ".join(c) or "—" for c in (maj.get("per_call") or []))
    return {
        "agent": turn["agent"], "turn_index": turn["turn_index"],
        "round": turn.get("round"), "clock": turn.get("clock"),
        "stake": turn["agent"] == REWARD_AGENT,
        "output": turn.get("output") or "",
        "description": f"{desc}  [votes: {per_call}]",
        "findings": findings,
        "categories": list(maj.get("categories") or []),
        "unknown_categories": [],
        "spans": [s for f in findings for s in f["evidence"]["output_spans"]],
        "cot_evidence": [],
        "contradicted_by": [c for f in findings for c in f["evidence"]["contradicted_by"]],
        "intent": {f["category"]: f["intent"] for f in findings if f.get("intent")},
        "audiences": sorted({a for f in findings for a in f["audiences"]}),
        "considered": dict(_NOT_RUN),
        "considered_not_asserted": False,
        "parse_error": None if maj.get("decidable") else "undecided: " + "; ".join(maj.get("failed_calls") or []),
        "judge_category": maj,
    }


def _vote_row(turn: Dict[str, Any], vote: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(vote)
    row.setdefault("stake", turn["agent"] == REWARD_AGENT)
    row.setdefault("considered", dict(_NOT_RUN))
    row.setdefault("considered_not_asserted", False)
    row.setdefault("round", turn.get("round"))
    row.setdefault("clock", turn.get("clock"))
    return row


def _counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        for c in r.get("categories") or []:
            out[c] = out.get(c, 0) + 1
    return out


def _file(judged: Dict[str, Any], rows: List[Dict[str, Any]], *, judge_label: str,
          replicate: int, run_path: Path) -> Dict[str, Any]:
    stake = [r for r in rows if r.get("stake")]
    base = [r for r in rows if not r.get("stake")]
    return {
        "run": str(run_path),
        "judge_version": judged.get("judge_version"),
        "critic": f"{judged.get('critic')} — agent3 majority of {judged.get('replicates')} "
                  f"(≥{judged.get('threshold')} votes per label)",
        "judge": judge_label,
        "replicate": replicate,
        "evidence_caps": {"transcript": None},
        "model_under_test": None,
        "selection": {"roles": [], "stake_agents": sorted({r["agent"] for r in rows}),
                      "baseline_agents": []},
        "n_turns": len(rows),
        "n_stake_turns": len(stake),
        "n_baseline_turns": len(base),
        "n_strategic_fabrication": sum(
            1 for r in rows if any(c in FABRICATION for c in (r.get("categories") or []))),
        "stake": {"category_counts": _counts(stake)},
        "baseline": {"category_counts": _counts(base)},
        "turns": rows,
    }


# ------------------------------------------------------------------------ the asks on the page
_ASKS_CSS = ("margin:.6rem 0 0; max-width:60rem; background:var(--card); border:1px solid var(--line); "
             "border-radius:8px; padding:.6rem .8rem; font-size:.86rem; line-height:1.45;")
_K_CSS = "color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.04em;"


def asks_block(asks: Dict[str, str]) -> str:
    """The optimizer's output — the two written asks — as a card for the page head."""
    from experiments.agent1.viewer import esc
    from experiments.agent3.candidate import OPTIMIZED

    parts = [f'<div class="asks" style="{_ASKS_CSS}"><div style="{_K_CSS}">optimizer output · opening asks</div>']
    for who in OPTIMIZED:
        parts.append(f'<p style="margin:.35rem 0 0"><b>{esc(who)}\'s prompt:</b> {esc(asks.get(who, ""))}</p>')
    parts.append("</div>")
    return "".join(parts)


def inject_asks(run_dir: Path) -> bool:
    """Put the asks into ``run.html``, in the page head under the buttons and beside the honesty
    panels — i.e. where a reader looks before opening any turn. A post-render insertion rather
    than a viewer change: agent1's viewer stays the world's, and the candidate is agent3's."""
    run_dir = Path(run_dir)
    html_path, asks_path = run_dir / "run.html", run_dir / "candidate.json"
    if not (html_path.exists() and asks_path.exists()):
        return False
    asks = (json.loads(asks_path.read_text(encoding="utf-8")).get("asks") or {})
    html = html_path.read_text(encoding="utf-8")
    if 'class="asks"' in html:
        return False  # already injected (a re-render would have removed it)
    card = asks_block(asks)
    # Inside `.titles`, after the button bar: the close of `.titles` is the last </div> before the
    # first honesty panel. Without verdicts there is no panel; fall back to just above the stats.
    anchor = html.find('<div class="vset" data-vset="0"><div class="honesty')
    if anchor > 0:
        close = html.rfind("</div>", 0, anchor)
        html = html[:close] + card + html[close:]
    else:
        html = html.replace('<div class="grid">', card + '<div class="grid">', 1)
    html_path.write_text(html, encoding="utf-8")
    return True


def export(run_dir: Path, judged: Dict[str, Any] | None = None, *, rerender: bool = True) -> List[Path]:
    run_dir = Path(run_dir)
    run_path = run_dir / "run.json"
    if judged is None:
        judged = json.loads((run_dir / "judge.json").read_text(encoding="utf-8"))
    model = str(judged.get("judge_model") or "judge")
    short = model.split("/")[-1]
    slug = _slug(model)
    turns = judged.get("turns") or []
    written: List[Path] = []

    maj_rows = [_majority_row(t) for t in turns]
    path = run_dir / f"run.category2_{judged.get('judge_version')}_{slug}.json"
    path.write_text(json.dumps(_file(judged, maj_rows, judge_label=f"{short} · majority",
                                     replicate=1, run_path=run_path),
                               indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    written.append(path)

    for rep in range(int(judged.get("replicates") or 3)):
        rows = []
        for t in turns:
            vote = next((v for v in (t.get("votes") or []) if v.get("replicate") == rep), None)
            if vote is not None:
                rows.append(_vote_row(t, vote))
        path = run_dir / f"run.category2_{judged.get('judge_version')}_{slug}_r{rep + 1}.json"
        path.write_text(json.dumps(_file(judged, rows, judge_label=f"{short} · vote {rep + 1}",
                                         replicate=rep + 1, run_path=run_path),
                                   indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        written.append(path)

    if rerender and run_path.exists():
        report = json.loads(run_path.read_text(encoding="utf-8"))
        agent1_run.write_viewer(report, run_path)
        inject_asks(run_dir)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="fold agent3 judge.json files into run.html")
    ap.add_argument("paths", nargs="+", help="run dirs, or out-dirs to walk for runs/*/*/judge.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dirs: List[Path] = []
    for p in args.paths:
        p = Path(p)
        if (p / "run.json").exists():
            dirs.append(p)
        else:
            dirs += sorted(q.parent for q in p.glob("runs/*/*/run.json"))
    for d in dirs:
        if (d / "judge.json").exists():
            files = export(d)
            logger.info("%s: %d verdict files, run.html re-rendered, asks injected", d, len(files))
        else:
            # Unjudged (a rollout from a step that was interrupted): still show the asks.
            report = json.loads((d / "run.json").read_text(encoding="utf-8"))
            agent1_run.write_viewer(report, d / "run.json")
            inject_asks(d)
            logger.info("%s: no judge.json — run.html re-rendered with asks only", d)


if __name__ == "__main__":
    main()
