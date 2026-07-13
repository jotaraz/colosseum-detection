"""Regenerate ONLY the closing summaries (v6 prompt) on top of frozen v5 rollouts.

Why this is sound (verified against the code):
  * The closing-summary turn is STATELESS. ``agent_survey_turn`` builds a fresh
    system+user prompt every call (``BaseAgent.generate_response`` →
    ``client.init_context(system, user)`` = a two-message context, no accumulated
    history). The discussion transcript reaches the model ONLY by being rendered
    into the user prompt via ``blackboard_context``. No planning/execution message
    history is ever replayed.
  * Therefore the entire summary input is reconstructable from frozen artifacts:
    the deterministic scenario (regenerated from the saved seed — the generator
    yields byte-identical instances), the frozen board (``blackboards.json``), and
    the final assignment (``tool_events.json`` assign_task calls).

What this produces:
  A NEW output tree that is a *verbatim copy of each v5 leaf* with only the
  summary-derived artifacts regenerated using the v6 prompt:
      - summaries.json                     (new v6 summary text + reasoning)
      - agent_turns.json                   (summary rows replaced; planning/exec kept)
      - agent_prompts.json / .md           (summary entries replaced with the v6 prompt)
      - run_config.json                    (prompt_version -> 6 + `resummary` provenance)
  Everything else (scenario, blackboards, tool_events, votes, reasoning,
  trajectories, metrics, final_summary, png) is byte-identical to the frozen
  rollout. The judge is NOT run here and stale v5 judge_results are dropped —
  judging the new tree is a separate offline step.

  The source (v5) tree is never modified.

Usage (cluster): python -m experiments.social_jira3.resummary \
    --src-root experiments/social_jira3/outputs/<v5_tag>/<timestamp> \
    --out-tag  social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v6_confsweep

Offline check (no model): add --validate [--limit N]. Reconstructs each summary
prompt and diffs it against the logged v5 prompt, asserting that ONLY the
discussion transcript and the summary-instruction block changed.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import difflib
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments.common.run_utils import write_json as _write_json, ensure_dir as _ensure_dir
from experiments.common.local_protocol import LocalCommunicationProtocol
from experiments.social_jira3.prompts import SocialJiraPrompts, PROMPT_VERSION
from experiments.social_jira3.run import (
    _make_client,
    _resolve_model_name,
    _install_reasoning_capture,
    _take_reasoning,
)
from experiments.collusion.run import _resolve_environment_class
from terrarium.networks import build_communication_network
from terrarium.utils import build_vllm_runtime, get_generation_params
from terrarium.agents.base import BaseAgent
from terrarium.logger import PromptLogger, AgentTrajectoryLogger

logger = logging.getLogger("social_jira3.resummary")


# --------------------------------------------------------------------------- io

def _iter_leaves(src_root: Path) -> List[Path]:
    """Every completed run leaf under the source timestamp dir (has run_config.json)."""
    return sorted(p.parent for p in src_root.glob("runs/**/run_config.json"))


def _load_assignment(src_leaf: Path) -> Dict[str, Any]:
    """Rebuild env.assignment from the recorded assign_task tool calls (final state)."""
    events = json.loads((src_leaf / "tool_events.json").read_text())
    assignment: Dict[str, Any] = {}
    for ev in events:
        if ev.get("tool_name") != "assign_task":
            continue
        res = ev.get("result") or {}
        if res.get("status") != "success":
            continue
        updates = ((res.get("result") or {}).get("state_updates") or {}).get("assignment") or {}
        assignment.update(updates)
    return assignment


def _load_board(protocol: LocalCommunicationProtocol, src_leaf: Path) -> None:
    """Replace each megaboard's event log with the frozen events so the rendered
    transcript is byte-identical to the source run."""
    saved = json.loads((src_leaf / "blackboards.json").read_text())
    for board in saved:
        idx = int(board["blackboard_id"])
        participants = list(board.get("participants") or [])
        while len(protocol.megaboard.blackboards) <= idx:
            protocol.megaboard.add_blackboard(participants or ["_"])
        bb = protocol.megaboard.blackboards[idx]
        bb.logs = list(board.get("events") or [])
        if participants:
            bb.agents = set(participants)
            bb.participants = list(participants)


# ------------------------------------------------------------------ prompt md

def _render_prompts_md(entries: List[Dict[str, Any]], env_name: str, seed: int) -> str:
    """Reproduce PromptLogger's agent_prompts.md format exactly, from a full entry list."""
    out = [f"# Agent Prompts Log - {env_name} (Seed: {seed})\n\n"]
    for e in entries:
        meta = [f"**Phase:** {e.get('phase')}"]
        if e.get("iteration") is not None:
            meta.append(f"**Iteration:** {e.get('iteration')}")
        if e.get("round") is not None:
            meta.append(f"**Round:** {e.get('round')}")
        out.append(
            f"## {e.get('agent_name')} - {' | '.join(meta)}\n"
            f"**Timestamp:** {e.get('timestamp')}\n\n"
            f"### System Prompt\n```\n{e.get('system_prompt')}\n```\n\n"
            f"### User Prompt\n```\n{e.get('user_prompt')}\n```\n\n---\n\n"
        )
    return "".join(out)


# ---------------------------------------------------------------- reconstruct

class _StubClient:
    """No-op client for --validate: prompts are built without any model call."""


def _build_cfg(base_cfg: Dict[str, Any], rc: Dict[str, Any], model_llm_cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("simulation", {})["seed"] = int(rc["seed"])
    cfg["simulation"]["max_iterations"] = 1
    cfg.setdefault("communication_network", {})["topology"] = str(rc.get("topology", "complete"))
    cfg["communication_network"]["num_agents"] = int(rc.get("num_agents", 4))
    cfg["environment"] = copy.deepcopy(rc["environment_cfg"])  # exact resolved env axes
    cfg["llm"] = copy.deepcopy(model_llm_cfg)
    return cfg


def _assert_scenario_matches(env: Any, src_leaf: Path) -> None:
    """Guard determinism: the regenerated scenario must equal the frozen one on the
    fields the summary prompt reads (roster, tasks, per-agent inbox/signal)."""
    saved = json.loads((src_leaf / "scenario.json").read_text())
    got = env.get_ground_truth()

    def _roster(d: Dict[str, Any]) -> Any:
        r = d.get("roster") or d.get("employees") or []
        return [(e.get("name"), e.get("profession"), e.get("seniority")) for e in r]

    if _roster(got) != _roster(saved):
        raise RuntimeError(f"roster mismatch on reconstruct: {src_leaf}")
    if json.dumps(got.get("tasks") or got.get("tasks_spec"), sort_keys=True) != \
       json.dumps(saved.get("tasks") or saved.get("tasks_spec"), sort_keys=True):
        raise RuntimeError(f"tasks mismatch on reconstruct: {src_leaf}")
    if json.dumps(got.get("inboxes"), sort_keys=True) != json.dumps(saved.get("inboxes"), sort_keys=True):
        raise RuntimeError(f"inbox mismatch on reconstruct: {src_leaf}")


async def _reconstruct(
    *,
    src_leaf: Path,
    tmp_dir: Path,
    base_cfg: Dict[str, Any],
    model_llm_cfg: Dict[str, Any],
    vllm_runtime: Any,
    validate: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rebuild env+board+assignment for one leaf and run only the summary loop.

    Returns (run_config, summaries_log, summary_turns, v6_prompt_entries).
    In validate mode: no model call; summaries_log/summary_turns are empty and the
    v6 prompt entries are still captured (get_user_prompt logs them).
    """
    rc = json.loads((src_leaf / "run_config.json").read_text())
    cfg = _build_cfg(base_cfg, rc, model_llm_cfg)

    _ensure_dir(tmp_dir)
    protocol = LocalCommunicationProtocol(config=cfg)
    env_cls = _resolve_environment_class(cfg["environment"])
    env = env_cls(protocol, cfg, tool_logger=type("TL", (), {"log_dir": tmp_dir})())

    prompt_logger = PromptLogger(
        environment_name=env.__class__.__name__, seed=int(rc["seed"]),
        config=cfg, run_timestamp="resummary", log_dir=tmp_dir,
    )
    prompt_logger.reset_log()  # fresh: only this leaf's summary prompts land here
    traj_logger = AgentTrajectoryLogger(
        environment_name=env.__class__.__name__, seed=int(rc["seed"]),
        config=cfg, run_timestamp="resummary", log_dir=tmp_dir,
    )

    agent_names = env.get_agent_names()
    env.set_communication_network(build_communication_network(agent_names, cfg))
    provider = (cfg.get("llm", {}).get("provider") or "").lower()
    model_name = _resolve_model_name(provider, cfg["llm"])
    gen_params = get_generation_params(cfg["llm"])
    max_steps = int(cfg["simulation"].get("max_conversation_steps", 5))
    env_tool_name = str(getattr(env, "tools_environment_name", None) or env.__class__.__name__)

    agents: List[BaseAgent] = []
    for name in agent_names:
        client = _StubClient() if validate else _make_client(
            cfg["llm"], agent_name=name, vllm_runtime=vllm_runtime
        )
        agents.append(
            BaseAgent(client, name, model_name, max_steps, None, traj_logger,
                      env_tool_name, generation_params=gen_params)
        )
    env.set_agent_clients(agents)
    await env.async_init()

    env.feelings_channel = rc["feelings_channel"]
    env.dislike_strength = rc["dislike_strength"]
    env.confidentiality = rc["confidentiality"]
    env.hint = rc["hint"]
    env.summary_audience = rc["summary_audience"]
    env.decoys = rc["decoys"]
    env.personality = rc["personality"]
    env.robust_assignment = bool(rc.get("robust_assignment", False))
    env.prompts = SocialJiraPrompts(
        env, cfg,
        feelings_channel=rc["feelings_channel"], confidentiality=rc["confidentiality"],
        hint=rc["hint"], summary_audience=rc["summary_audience"], personality=rc["personality"],
        robust_assignment=bool(rc.get("robust_assignment", False)),
        experiment_prompt_logger=prompt_logger, log_prompts=True,
    )

    _assert_scenario_matches(env, src_leaf)
    _load_board(protocol, src_leaf)
    env.assignment = _load_assignment(src_leaf)

    if not validate:
        _install_reasoning_capture()

    summaries_log: Dict[str, Any] = {}
    summary_turns: List[Dict[str, Any]] = []
    for agent in env.agents:
        sum_ctx = env.build_agent_context(agent.name, phase="summary", iteration=1)
        if validate:
            bb = await protocol._prefetch_blackboard_events(
                agent.name, phase="summary", iteration=1
            )
            # Logs the v6 summary prompt to tmp via the prompt_logger (no model call).
            env.prompts.get_user_prompt(
                agent_name=agent.name, agent_context=dict(sum_ctx), blackboard_context=bb
            )
            continue
        response = await protocol.agent_survey_turn(
            agent, agent.name, dict(sum_ctx), env, iteration=1
        )
        reasoning = _take_reasoning(agent)
        summaries_log[agent.name] = {
            "audience": env.summary_audience,
            "text": response.get("response"),
            "reasoning": [s.get("reasoning_content") for s in reasoning],
        }
        summary_turns.append({
            "phase": "summary", "agent": agent.name,
            "response": response.get("response"), "usage": response.get("usage"),
            "model": response.get("model"),
        })

    v6_entries = []
    ap_path = tmp_dir / "agent_prompts.json"
    if ap_path.exists():
        v6_entries = [e for e in json.loads(ap_path.read_text())
                      if isinstance(e, dict) and e.get("phase") == "summary"]
    return rc, summaries_log, summary_turns, v6_entries


# -------------------------------------------------------------------- writing

def _write_leaf(
    *, src_leaf: Path, dst_leaf: Path, src_root: Path, rc: Dict[str, Any],
    summaries_log: Dict[str, Any], summary_turns: List[Dict[str, Any]],
    v6_entries: List[Dict[str, Any]],
) -> None:
    if dst_leaf.exists():
        shutil.rmtree(dst_leaf)
    shutil.copytree(src_leaf, dst_leaf)

    _write_json(dst_leaf / "summaries.json", summaries_log)

    turns = json.loads((dst_leaf / "agent_turns.json").read_text())
    turns = [t for t in turns if not (isinstance(t, dict) and t.get("phase") == "summary")]
    turns.extend(summary_turns)
    _write_json(dst_leaf / "agent_turns.json", turns)

    new_rc = dict(rc)
    new_rc["prompt_version"] = PROMPT_VERSION
    new_rc["resummary"] = {
        "rollout_prompt_version": rc.get("prompt_version"),
        "summary_prompt_version": PROMPT_VERSION,
        "source_leaf": str(src_leaf.relative_to(src_root)),
        "note": ("closing summaries regenerated with the v6 prompt on the frozen rollout; "
                 "planning/execution artifacts are byte-identical to the source"),
    }
    _write_json(dst_leaf / "run_config.json", new_rc)

    ap = json.loads((dst_leaf / "agent_prompts.json").read_text())
    ap = [e for e in ap if not (isinstance(e, dict) and e.get("phase") == "summary")]
    ap.extend(v6_entries)
    _write_json(dst_leaf / "agent_prompts.json", ap)
    (dst_leaf / "agent_prompts.md").write_text(
        _render_prompts_md(ap, rc.get("environment_name", "SocialJiraEnvironment"), int(rc["seed"])),
        encoding="utf-8",
    )

    # Judging the new tree is a separate offline step; drop stale v5 judgments.
    for name in ("judge_results.json",):
        p = dst_leaf / name
        if p.exists():
            p.unlink()


def _aggregate(src_root: Path, dst_root: Path, n_runs: int) -> None:
    cfg = json.loads((src_root / "config.json").read_text())
    cfg["_resummary"] = {
        "source_root": str(src_root),
        "summary_prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now().isoformat(),
        "note": ("v5 rollout with closing summaries regenerated by the v6 prompt; "
                 "per-run summary text is in each leaf's summaries.json"),
    }
    _write_json(dst_root / "config.json", cfg)
    # Top-level summary.* are metrics rows (assignment-based, summary-independent) — copy as-is.
    for name in ("summary.json", "summary.jsonl", "summary.csv"):
        if (src_root / name).exists():
            shutil.copy2(src_root / name, dst_root / name)
    _write_json(dst_root / "progress.json", {
        "status": "completed", "total_runs": n_runs, "completed_runs": n_runs,
        "failed_runs": 0, "updated_at": datetime.now().isoformat(),
        "resummary_of": str(src_root),
    })


# ------------------------------------------------------------------ validate

def _diff_against_v5(src_leaf: Path, v6_entries: List[Dict[str, Any]]) -> List[str]:
    """Compare the reconstructed v6 summary prompts to the logged v5 ones. Returns a
    short report; asserts nothing beyond the discussion/instruction blocks changed."""
    v5 = [e for e in json.loads((src_leaf / "agent_prompts.json").read_text())
          if isinstance(e, dict) and e.get("phase") == "summary"]
    v5_by_agent = {e["agent_name"]: e for e in v5}
    report: List[str] = []
    for e in v6_entries:
        agent = e["agent_name"]
        old = v5_by_agent.get(agent)
        if not old:
            report.append(f"[{agent}] no v5 summary entry to compare")
            continue
        if old["system_prompt"] != e["system_prompt"]:
            report.append(f"[{agent}] SYSTEM prompt changed (unexpected!)")
        # user prompt: only the discussion + instruction blocks should differ.
        changed_blocks = _changed_blocks(old["user_prompt"], e["user_prompt"])
        report.append(f"[{agent}] user-prompt changed blocks: {sorted(changed_blocks)}")
    return report


def _split_blocks(prompt: str) -> Dict[str, str]:
    """Split a user prompt into its '=== HEADER ===' sections."""
    blocks: Dict[str, str] = {}
    cur = "PREAMBLE"
    buf: List[str] = []
    for line in prompt.splitlines():
        if line.startswith("=== ") and line.rstrip().endswith(" ==="):
            blocks[cur] = "\n".join(buf)
            cur = line.strip().strip("= ").strip()
            buf = []
        else:
            buf.append(line)
    blocks[cur] = "\n".join(buf)
    return blocks


def _changed_blocks(a: str, b: str) -> set:
    ba, bb = _split_blocks(a), _split_blocks(b)
    changed = set()
    for k in set(ba) | set(bb):
        if ba.get(k) != bb.get(k):
            changed.add(k)
    return changed


# ---------------------------------------------------------------------- main

async def _amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src_root = Path(args.src_root).resolve()
    if not (src_root / "config.json").exists():
        logger.error("no config.json under --src-root %s", src_root)
        return 2

    base_cfg = json.loads((src_root / "config.json").read_text())
    models = base_cfg.get("llm_models") or []
    if not models:
        logger.error("source config.json has no llm_models")
        return 2
    model = models[0]
    model_llm_cfg = model.get("llm") or {}
    provider = (model_llm_cfg.get("provider") or "").lower()

    leaves = _iter_leaves(src_root)
    if args.limit:
        leaves = leaves[: args.limit]
    logger.info("leaves to process: %d (provider=%s)", len(leaves), provider)

    if args.validate:
        tmp_root = Path(args.tmp or (src_root.parent / ".resummary_validate"))
        ok = True
        for i, leaf in enumerate(leaves):
            tmp = tmp_root / f"leaf{i}"
            try:
                _rc, _s, _t, v6 = await _reconstruct(
                    src_leaf=leaf, tmp_dir=tmp, base_cfg=base_cfg,
                    model_llm_cfg=model_llm_cfg, vllm_runtime=None, validate=True,
                )
                report = _diff_against_v5(leaf, v6)
                logger.info("VALIDATE %s", leaf.name)
                for line in report:
                    logger.info("   %s", line)
                    if "SYSTEM prompt changed" in line or "no v5 summary" in line:
                        ok = False
                    if "changed blocks:" in line:
                        blocks = line.split("changed blocks:")[1]
                        for b in eval(blocks):  # noqa: S307 - our own formatted list
                            if b not in ("SHARED DISCUSSION SO FAR", "SHARED DISCUSSION (each post tagged by the assistant who wrote it)", "CLOSING SUMMARY"):
                                logger.error("   UNEXPECTED changed block: %s", b)
                                ok = False
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
        logger.info("VALIDATE %s", "OK" if ok else "FAILED")
        return 0 if ok else 1

    # ---- real run: needs the model server ----
    if provider != "vllm":
        logger.error("only vllm provider supported for regeneration; got %r", provider)
        return 2
    vllm_runtime = build_vllm_runtime(model_llm_cfg)

    out_root = Path(args.out_root).resolve() if args.out_root else (
        Path("experiments/social_jira3/outputs") / args.out_tag /
        datetime.now().strftime("%Y%m%d-%H%M%S")
    ).resolve()
    _ensure_dir(out_root)
    logger.info("output root: %s", out_root)

    tmp_root = out_root / ".tmp"
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    done = {"n": 0}

    async def _one(i: int, leaf: Path) -> None:
        async with sem:
            tmp = tmp_root / f"leaf{i}"
            try:
                rc, summaries, turns, v6 = await _reconstruct(
                    src_leaf=leaf, tmp_dir=tmp, base_cfg=base_cfg,
                    model_llm_cfg=model_llm_cfg, vllm_runtime=vllm_runtime, validate=False,
                )
                dst_leaf = out_root / leaf.relative_to(src_root)
                _write_leaf(src_leaf=leaf, dst_leaf=dst_leaf, src_root=src_root, rc=rc,
                            summaries_log=summaries, summary_turns=turns, v6_entries=v6)
                done["n"] += 1
                logger.info("[%d/%d] %s", done["n"], len(leaves), dst_leaf.name)
            except Exception:
                logger.exception("FAILED leaf %s", leaf)
                raise
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)

    await asyncio.gather(*(_one(i, leaf) for i, leaf in enumerate(leaves)))
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    _aggregate(src_root, out_root, len(leaves))
    logger.info("DONE — %d leaves regenerated into %s", len(leaves), out_root)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-root", required=True, help="v5 timestamp dir (…/<tag>/<timestamp>)")
    ap.add_argument("--out-tag", default="social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v6_confsweep",
                    help="output dir name under experiments/social_jira3/outputs/")
    ap.add_argument("--out-root", default=None, help="explicit output timestamp dir (overrides --out-tag)")
    ap.add_argument("--concurrency", default=4, type=int, help="leaves processed concurrently")
    ap.add_argument("--limit", default=0, type=int, help="process only the first N leaves (testing)")
    ap.add_argument("--validate", action="store_true", help="model-free: diff reconstructed v6 prompts vs v5")
    ap.add_argument("--tmp", default=None, help="scratch dir for --validate")
    args = ap.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
