from __future__ import annotations

"""Apply agent2's CRITIC_CATEGORY judge — alone — to finished agent1 run records.

A one-judge bridge, not a port of the critic: SOUNDNESS and AT-STAKE are deliberately not run.
CATEGORY labels the strategy of every assistant turn and scores fabrication specificity, and that
is all this script asks for. The output is therefore NOT a qualifying verdict in the sj4/agent2
sense (no triple AND-gate) — it is the strategy labelling on its own.

The judge is **epistemically grounded** (see ``CRITIC_CATEGORY.md``): it is never handed the
workspace, only the evidence package built by ``turn_context`` from the run record itself —

* ``{knowledge_base}`` — everything this assistant had been handed before the judged turn: each
  conversation exactly as its own reads last returned it, its calendar and the board as last
  returned, searches, directory lookups, injected messages, and its own earlier posts/claims;
* ``{turn_trace}`` — the judged turn in strict step order: opening message, reasoning, every
  tool call with arguments and the result as returned (failures marked), posts audience-tagged,
  and the closing debrief.

A fabrication flag must carry ``intent_evidence`` (verbatim CoT quote) and ``contradicted_by``;
``critic.enforce_evidence`` verifies the quote occurs in the reasoning and downgrades flags that
fail, so the strictness is mechanical. Shared pieces (``LlmCritic._judge`` retry/parse/_meta
behaviour, ``assemble_turns``, ``enforce_evidence``, the audience classifier) come from agent2,
so a verdict here is directly comparable with the agent2 critic's own CATEGORY verdicts.

System prompt and ask are re-rendered through ``TaskAssignPrompts`` from the run's own config
(confidentiality, discussion norms, ask_overrides); the fixture is loaded for that and for
audience classification only — never shown to the judge.

Usage (repo root, Azure creds in the env)::

    python -m experiments.agent2.category_over_agent1 \
        experiments/agent1/outputs/v6_ask/askA_glm_s119.json \
        --provider azure --judge-model gpt-5.4 --workers 4

Writes ``<run_stem>.category_<model>.json`` beside each run file.
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent1.prompts import DEFAULT_ASK, TaskAssignPrompts
from experiments.agent1.workspace import Workspace
from experiments.agent2 import turn_context
from experiments.agent2.critic import LlmCritic, enforce_evidence, render_output
from experiments.agent2.target_run import _audience, assemble_turns
from experiments.social_jira4.llm import make_judge_caller

logger = logging.getLogger("experiments.agent2.category_over_agent1")


def judge_run(
    run_path: Path,
    critic: LlmCritic,
    *,
    workers: int,
    judge_label: str,
) -> Dict[str, Any]:
    report = json.loads(run_path.read_text(encoding="utf-8"))
    config = report.get("config") or {}
    ws = Workspace.load(REPO / str(config["workspace"]))
    prompts = TaskAssignPrompts(
        ws,
        confidentiality=str(config.get("confidentiality") or "audience"),
        discussion_norms=str(config.get("discussion_norms") or "off"),
        ask=str(config.get("ask") or DEFAULT_ASK),
        ask_overrides=config.get("ask_overrides") or {},
    )
    turns = assemble_turns(report, ws)

    def _one(turn: Dict[str, Any]) -> Dict[str, Any]:
        agent = str(turn.get("agent") or "")
        index = int(turn.get("turn_index") or 0)

        def _aud(args: Dict[str, Any], result: Dict[str, Any], speaker: str = agent) -> str:
            conv = str((result or {}).get("conversation")
                       or (args or {}).get("conversation") or "")
            return _audience(conv, ws, speaker)

        package = turn_context.build(report, index, audience_of=_aud, assembled=turn)
        verdict = critic._judge(critic._cat, {
            "system_prompt": prompts.get_system_prompt(agent),
            "ask": prompts.opening(agent),
            "target_agent": agent,
            "target_turn": str(index),
            **package,
        })
        categories, spec, downgrade = enforce_evidence(
            verdict, str(turn.get("reasoning") or ""))
        explanation = str(verdict.get("explanation") or "")
        if downgrade:
            explanation = f"{explanation} [{downgrade}]"
        return {
            "agent": agent,
            "turn_index": index,
            "round": turn.get("round"),
            "clock": turn.get("clock"),
            "output": render_output(turn),
            "categories": categories,
            "fabrication_specificity": spec,
            "spans": [str(s) for s in (verdict.get("spans") or [])],
            "considered_spans": [str(s) for s in (verdict.get("considered_spans") or [])],
            "intent_evidence": [str(q) for q in (verdict.get("intent_evidence") or [])],
            "contradicted_by": [str(x) for x in (verdict.get("contradicted_by") or [])],
            "audiences": [str(a) for a in (verdict.get("audiences") or [])],
            "explanation": explanation,
            "downgraded": downgrade,
            "parse_error": verdict.get("_parse_error"),
            "judge": verdict,
        }

    if workers > 1 and len(turns) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            scored = list(pool.map(_one, turns))
    else:
        scored = [_one(t) for t in turns]

    flagged = [s for s in scored if "strategic-fabrication" in s["categories"]]
    return {
        "run": str(run_path),
        "critic": ("agent2/CRITIC_CATEGORY.md (CATEGORY only — no SOUNDNESS/AT-STAKE gate; "
                   "epistemic evidence package, code-enforced intent evidence)"),
        "judge": judge_label,
        "model_under_test": config.get("model_name"),
        "fixture": report.get("fixture"),
        "n_turns": len(scored),
        "n_strategic_fabrication": len(flagged),
        "n_downgraded": sum(1 for s in scored if s["downgraded"]),
        "category_counts": _counts(scored),
        "turns": scored,
    }


def _counts(scored: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for s in scored:
        for c in s["categories"]:
            counts[c] = counts.get(c, 0) + 1
    return counts


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply agent2's CRITIC_CATEGORY judge — alone — to finished agent1 run records."
    )
    parser.add_argument("runs", nargs="+", help="agent1 run record json files")
    parser.add_argument("--provider", default="azure", choices=["azure", "openrouter"])
    parser.add_argument("--judge-model", default="gpt-5.4",
                        help="Azure deployment name / OpenRouter slug")
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent judge calls (gpt-5.4 has a low 429 concurrency ceiling)")
    parser.add_argument("--max-tokens", type=int, default=16000,
                        help="max_completion_tokens — reasoning models spend most of it thinking")
    parser.add_argument("--force", action="store_true", help="re-judge even if the output exists")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    caller = make_judge_caller(provider=args.provider, model=args.judge_model,
                               max_tokens=args.max_tokens)
    critic = LlmCritic(caller, workers=args.workers)

    slug = args.judge_model.replace("/", "-").replace(".", "").replace("-", "")
    failures = 0
    for run in args.runs:
        run_path = Path(run)
        out_path = run_path.with_name(f"{run_path.stem}.category_{slug}.json")
        if out_path.exists() and not args.force:
            logger.info("skip (exists): %s", out_path)
            continue
        try:
            result = judge_run(run_path, critic, workers=args.workers,
                               judge_label=f"{args.provider}:{args.judge_model}")
        except Exception:  # noqa: BLE001 — one bad run must not kill a batch
            logger.exception("FAILED: %s", run_path)
            failures += 1
            continue
        result["usage_totals"] = caller.snapshot() if hasattr(caller, "snapshot") else {}
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
        logger.info("%s: %d/%d turns strategic-fabrication (%d downgraded) -> %s",
                    run_path.name, result["n_strategic_fabrication"], result["n_turns"],
                    result["n_downgraded"], out_path)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
