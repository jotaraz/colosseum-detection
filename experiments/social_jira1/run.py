from __future__ import annotations

# ruff: noqa: E402

"""social-jira1 experiment runner.

Each agent is the personal assistant of an employee. Employees must be paired up to do
tasks; assistants privately know their own employee's (directional) feelings about
colleagues and must coordinate good pairings on a shared channel - while (in the
``discreet`` framing) keeping those feelings confidential. The headline question is about
*how they communicate without leaking feelings*; v1 is log-only (clean transcripts +
structured ground truth), with the leakage judge to be built later.

Sweep axes:
  * models       - llm_models in the config (vLLM local).
  * scenario_type- resolvable | conflict (the instance axis; the "profile" slot).
  * framing      - discreet | control (the discretion axis).
  * seeds        - surface roster + scenario draw.
  * samples      - repeats per cell (temperature variance).
"""

import sys
import argparse
import copy
import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from tqdm import tqdm

if sys.version_info < (3, 11):
    raise RuntimeError("Terrarium requires Python >= 3.11. Activate the repo .venv.")

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.common.run_utils import (
    configure_experiment_logging as _configure_experiment_logging_impl,
    ensure_dir as _ensure_dir,
    load_yaml as _load_yaml,
    normalize_seeds as _normalize_seeds,
    write_json as _write_json,
    write_progress as _write_progress,
)
from experiments.common.blackboard_logger import ExperimentBlackboardLogger
from experiments.common.local_protocol import LocalCommunicationProtocol
from experiments.social_jira1.prompts import SocialJiraPrompts, ALL_FRAMINGS
from experiments.social_jira1.metrics import compute_social_jira_metrics
from experiments.collusion.run import _resolve_environment_class
from terrarium.networks import build_communication_network
from terrarium.logger import AgentTrajectoryLogger, PromptLogger
from terrarium.utils import (
    build_vllm_runtime,
    get_client_instance,
    get_generation_params,
    get_model_name,
)
from terrarium.agents.base import BaseAgent

LOGGER_NAME = "experiments.social_jira1"
logger = logging.getLogger(LOGGER_NAME)

SCENARIO_TYPES = ("resolvable", "conflict")


def _configure_experiment_logging(root: Path, *, verbose: bool = True) -> None:
    _configure_experiment_logging_impl(logger, root, verbose=verbose)


def _reasoning_message(data: Any) -> Dict[str, Any]:
    """Pull the chat-completion ``message`` out of a generate_response result.

    The bundled vLLM client returns the raw response as a dict ``{"choices": [{"message":
    {...}}]}`` (this is what self_sacrifice relies on), but we also tolerate an OpenAI-style
    object in case the client is swapped. Returns ``{}`` if no message is found.
    """
    try:
        choices = data.get("choices") if isinstance(data, dict) else getattr(data, "choices", None)
        first = (choices or [None])[0]
        if first is None:
            return {}
        msg = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
        if msg is None:
            return {}
        if isinstance(msg, dict):
            return msg
        extra = getattr(msg, "model_extra", None) or {}  # vLLM puts reasoning_content here
        return {
            "reasoning_content": getattr(msg, "reasoning_content", None) or extra.get("reasoning_content"),
            "content": getattr(msg, "content", None),
        }
    except Exception:
        return {}


def _install_reasoning_capture() -> bool:
    """Capture the model's chain-of-thought channel so it can be saved per run.

    gpt-oss with vLLM's ``openai_gptoss`` reasoning_parser returns its analysis/CoT in
    ``message["reasoning_content"]`` (separate from the visible ``message["content"]``).
    The bundled vLLM client keeps only ``content`` downstream, so the reasoning never
    reaches agent_trajectories.json. We monkeypatch ``generate_response`` to stash each
    call's reasoning_content (and content) on the client instance; :func:`_drain_reasoning`
    then writes it to ``agent_reasoning.json``. Best-effort; no-op if unavailable.
    """
    try:
        from llm_server.clients.vllm_client import VLLMClient
    except Exception:
        return False
    if getattr(VLLMClient, "_reasoning_capture_installed", False):
        return True

    original_generate = VLLMClient.generate_response
    warned: List[bool] = []

    def generate_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        data, response_str = original_generate(self, *args, **kwargs)
        try:
            steps = getattr(self, "_reasoning_steps", None)
            if steps is None:
                steps = self._reasoning_steps = []
            msg = _reasoning_message(data)
            steps.append({
                "reasoning_content": msg.get("reasoning_content"),
                "content": msg.get("content"),
            })
        except Exception as exc:  # never let logging break a run, but surface it once
            if not warned:
                warned.append(True)
                logger.warning("reasoning capture failed (will not retry-warn): %s", exc)
        return data, response_str

    generate_response.__wrapped__ = original_generate
    VLLMClient.generate_response = generate_response  # type: ignore[method-assign]
    VLLMClient._reasoning_capture_installed = True
    return True


def _drain_reasoning(
    reasoning_log: Dict[str, Any], agent: Any, phase: str, iteration: int, round_num: int
) -> None:
    client = getattr(agent, "client", None)
    captured = list(getattr(client, "_reasoning_steps", []) or []) if client is not None else []
    if client is not None:
        client._reasoning_steps = []
    steps = {f"step_{i + 1}": s for i, s in enumerate(captured)}
    (reasoning_log
        .setdefault(agent.name, {})
        .setdefault(f"iteration_{iteration}", {})
        .setdefault(phase, {}))[f"round_{round_num}"] = steps


# Convergence detection via a private per-round "preliminary vote": after each agent posts to
# the shared board it is asked, on a private channel hidden from everyone else, for the full
# hypothetical matching it would commit to right now. Parsing that structured ballot is far more
# reliable than scraping agreement out of free-text debate, and the ballots double as a record
# of how each agent's intended allocation evolves round by round.
def _ballot_key(ballot: Dict[str, Any]):
    """Hashable canonical form of a {task: frozenset(pair) | None} ballot."""
    return tuple(sorted(
        (t, tuple(sorted(p)) if p else None) for t, p in (ballot or {}).items()
    ))


def _parse_ballot(text: str, employees: List[str], tasks: List[str]):
    """Parse a vote into ({task: frozenset(pair) | None}, {task: "A & B" | "none"}).

    Robust to formatting: for each task we take the first line mentioning that task id and pull
    out the (word-boundary) employee names on it; a task with fewer than two distinct names is
    recorded as unassigned (None).
    """
    lines = (text or "").splitlines()
    ballot: Dict[str, Any] = {}
    display: Dict[str, str] = {}
    for task in tasks:
        task_re = re.compile(r"\b" + re.escape(task) + r"\b", re.IGNORECASE)
        line = next((ln for ln in lines if task_re.search(ln)), "")
        low = line.lower()
        names: List[str] = []
        for emp in employees:
            if re.search(r"\b" + re.escape(emp.lower()) + r"\b", low) and emp not in names:
                names.append(emp)
        if len(names) >= 2:
            ballot[task] = frozenset(names[:2])
            display[task] = " & ".join(sorted(names[:2]))
        else:
            ballot[task] = None
            display[task] = "none"
    return ballot, display


def _votes_converged(round_ballots: List[Dict[str, Any]], prev_ballots, num_agents: int):
    """(converged, reason). Stop when a ~2/3 majority share one matching (consensus) or when
    nobody changed their vote versus the previous round (stable agreement / deadlock)."""
    keys = [_ballot_key(b) for b in round_ballots if b]
    if keys:
        top = max(keys.count(k) for k in keys)
        if top >= max(2, (2 * num_agents + 2) // 3):
            return True, "consensus"
    if prev_ballots is not None and len(prev_ballots) == len(round_ballots):
        if all(_ballot_key(a) == _ballot_key(b) for a, b in zip(prev_ballots, round_ballots)):
            return True, "stable"
    return False, ""


def _take_reasoning(agent: Any) -> List[Dict[str, Any]]:
    """Pop the reasoning captured since the last drain, so a private vote turn's CoT is kept
    out of the next agent's planning-turn reasoning (and stored with the vote instead)."""
    client = getattr(agent, "client", None)
    steps = list(getattr(client, "_reasoning_steps", []) or []) if client is not None else []
    if client is not None:
        client._reasoning_steps = []
    return steps


async def _run_single(
    *,
    base_cfg: Dict[str, Any],
    model_label: str,
    model_llm_cfg: Dict[str, Any],
    framing: str,
    scenario_type: str,
    topology: str,
    num_agents: int,
    num_tasks: int,
    seed: int,
    sample: int = 0,
    out_dir: Path,
) -> Dict[str, Any]:
    framing = str(framing).strip().lower()
    scenario_type = str(scenario_type).strip().lower()

    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("simulation", {})["seed"] = int(seed)
    cfg["simulation"]["max_iterations"] = 1
    exp = cfg.get("experiment", {}) or {}
    cfg["simulation"]["max_planning_rounds"] = int(
        exp.get("planning_rounds", cfg["simulation"].get("max_planning_rounds", 3))
    )
    cfg["simulation"]["max_conversation_steps"] = int(
        exp.get("max_conversation_steps", cfg["simulation"].get("max_conversation_steps", 5))
    )
    cfg.setdefault("communication_network", {})["topology"] = str(topology)
    cfg["communication_network"]["num_agents"] = int(num_agents)
    cfg.setdefault("environment", {})["scenario_type"] = scenario_type
    cfg["environment"]["num_tasks"] = int(num_tasks)
    cfg["llm"] = copy.deepcopy(model_llm_cfg)

    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = (
        f"{model_label}__{framing}__{scenario_type}"
        f"__{topology}__n{num_agents}__t{num_tasks}__seed{seed}__s{sample}"
    )
    run_dir = out_dir.joinpath("runs", model_label, framing, scenario_type, run_id)
    _ensure_dir(run_dir)
    logger.info("RUN START %s", run_id)

    cfg["simulation"]["run_timestamp"] = f"{run_timestamp}__{run_id}"
    cfg["simulation"]["tags"] = [str(exp.get("tag", "social_jira1"))]

    protocol = LocalCommunicationProtocol(config=cfg)
    env_cls = _resolve_environment_class(cfg.get("environment") or {})
    env = env_cls(protocol, cfg, tool_logger=type("TL", (), {"log_dir": run_dir})())

    bb_logger = ExperimentBlackboardLogger(cfg, log_root=run_dir)
    bb_logger.clear_blackboard_logs()

    log_prompts_cfg = exp.get("log_prompts")
    log_prompts = True if log_prompts_cfg is None else bool(log_prompts_cfg)
    prompt_logger = (
        PromptLogger(
            environment_name=env.__class__.__name__,
            seed=int(seed),
            config=cfg,
            run_timestamp=cfg["simulation"].get("run_timestamp"),
            log_dir=run_dir,
        )
        if log_prompts
        else None
    )
    trajectory_logger = AgentTrajectoryLogger(
        environment_name=env.__class__.__name__,
        seed=int(seed),
        config=cfg,
        run_timestamp=cfg["simulation"].get("run_timestamp"),
        log_dir=run_dir,
    )

    agent_names = env.get_agent_names()
    communication_network = build_communication_network(agent_names, cfg)
    env.set_communication_network(communication_network)

    provider = (cfg.get("llm", {}).get("provider") or "").lower()
    model_name = get_model_name(provider, cfg["llm"])
    generation_params = get_generation_params(cfg["llm"])
    max_steps = int(cfg["simulation"].get("max_conversation_steps", 5))
    vllm_runtime = build_vllm_runtime(cfg["llm"]) if provider == "vllm" else None

    env_tool_name = str(getattr(env, "tools_environment_name", None) or env.__class__.__name__)
    agents: List[BaseAgent] = []
    for name in agent_names:
        client = get_client_instance(cfg["llm"], agent_name=name, vllm_runtime=vllm_runtime)
        agents.append(
            BaseAgent(
                client, name, model_name, max_steps, None,
                trajectory_logger, env_tool_name, generation_params=generation_params,
            )
        )
    env.set_agent_clients(agents)

    await env.async_init()

    env.prompts = SocialJiraPrompts(
        env,
        cfg,
        framing=framing,
        experiment_prompt_logger=prompt_logger,
        log_prompts=log_prompts,
    )

    planning_rounds = int(cfg["simulation"].get("max_planning_rounds", 3))
    preliminary_vote = bool(exp.get("preliminary_vote", True))
    early_stop_consensus = bool(exp.get("early_stop_consensus", True))
    employees = list(env.agent_names)
    task_ids = list(env.scenario.tasks)
    _install_reasoning_capture()
    reasoning_log: Dict[str, Any] = {}
    votes_log: Dict[str, Any] = {}
    turns: List[Dict[str, Any]] = []
    prev_ballots: Optional[List[Dict[str, Any]]] = None
    for planning_round in range(1, planning_rounds + 1):
        round_ballots: List[Dict[str, Any]] = []
        for agent in env.agents:
            agent_context = env.build_agent_context(
                agent.name, phase="planning", iteration=1, planning_round=planning_round
            )
            response = await protocol.agent_planning_turn(
                agent, agent.name, dict(agent_context), env,
                iteration=1, planning_round=planning_round,
            )
            _drain_reasoning(reasoning_log, agent, "planning", 1, planning_round)
            turns.append({
                "phase": "planning", "planning_round": planning_round,
                "agent": agent.name, "response": response.get("response"),
                "usage": response.get("usage"), "model": response.get("model"),
                "tools_executed": response.get("tools_executed"),
            })

            # Private, hypothetical "preliminary vote" right after the agent has posted — a
            # survey turn never reaches the shared board, so it can't influence or leak to
            # the others. Used only to detect convergence (and as an opinion-trajectory log).
            if preliminary_vote:
                vote_ctx = env.build_agent_context(
                    agent.name, phase="survey", iteration=1, planning_round=planning_round
                )
                vote = await protocol.agent_survey_turn(
                    agent, agent.name, dict(vote_ctx), env, iteration=1
                )
                vote_reasoning = _take_reasoning(agent)  # keep the vote's CoT out of planning
                raw = vote.get("response") or ""
                ballot, display = _parse_ballot(raw, employees, task_ids)
                round_ballots.append(ballot)
                votes_log.setdefault(agent.name, {})[f"round_{planning_round}"] = {
                    "assignment": display, "raw": raw,
                    "reasoning": [s.get("reasoning_content") for s in vote_reasoning],
                }
                turns.append({
                    "phase": "preliminary_vote", "planning_round": planning_round,
                    "agent": agent.name, "assignment": display,
                    "response": raw, "usage": vote.get("usage"), "model": vote.get("model"),
                })

        # Stop early once the private ballots show the group has converged (consensus) or
        # stopped moving (stable). Only from round 2 on — round 1 is the opening positions.
        if early_stop_consensus and preliminary_vote and planning_round >= 2:
            converged, reason = _votes_converged(round_ballots, prev_ballots, len(env.agents))
            if converged:
                logger.info(
                    "RUN %s planning converged after round %d/%d (%s by preliminary vote) — "
                    "ending planning early", run_id, planning_round, planning_rounds, reason
                )
                break
        prev_ballots = round_ballots

    for agent in env.agents:
        agent_context = env.build_agent_context(agent.name, phase="execution", iteration=1)
        response = await protocol.agent_execution_turn(
            agent, agent.name, dict(agent_context), env, iteration=1
        )
        _drain_reasoning(reasoning_log, agent, "execution", 1, 0)
        turns.append({
            "phase": "execution", "agent": agent.name,
            "response": response.get("response"), "usage": response.get("usage"),
            "model": response.get("model"), "tools_executed": response.get("tools_executed"),
        })

    final_summary = env.get_final_summary()
    metrics = compute_social_jira_metrics(
        env=env,
        framing=framing,
        scenario_type=scenario_type,
        seed=int(seed),
        final_summary=final_summary,
        env_cfg=cfg.get("environment") or {},
    )
    metrics["sample"] = int(sample)

    _write_json(run_dir / "run_config.json", {
        "sample": int(sample),
        "run_id": run_id,
        "model_label": model_label,
        "provider": provider,
        "model": model_name,
        "framing": framing,
        "scenario_type": scenario_type,
        "environment_cfg": cfg.get("environment") or {},
        "environment_name": env.__class__.__name__,
        "topology": topology,
        "num_agents": num_agents,
        "num_tasks": num_tasks,
        "seed": seed,
        "agent_turn_order": [a.name for a in env.agents],
    })
    # Ground truth for the (future) leakage judge: full feelings + goodness tables, prose,
    # and the optimal / comfortable matchings.
    _write_json(run_dir / "scenario.json", env.scenario.to_ground_truth())
    _write_json(run_dir / "final_summary.json", final_summary)
    _write_json(run_dir / "agent_turns.json", turns)
    _write_json(run_dir / "agent_reasoning.json", reasoning_log)
    _write_json(run_dir / "agent_votes.json", votes_log)
    _write_json(run_dir / "metrics.json", metrics)
    _write_json(run_dir / "tool_events.json", [
        {
            "tool_name": e.tool_name, "agent_name": e.agent_name,
            "arguments": e.arguments, "result": e.result, "phase": e.phase,
            "iteration": e.iteration, "planning_round": e.planning_round,
        }
        for e in protocol.tool_events
    ])
    _write_json(run_dir / "blackboards.json", [
        {
            "blackboard_id": bb.blackboard_id,
            "participants": sorted(list(bb.agents)),
            "events": bb.logs,
        }
        for bb in protocol.megaboard.blackboards
    ])

    logger.info(
        "RUN END %s (chose_optimal=%s chose_comfortable=%s goodness_ratio=%s)",
        run_id, metrics.get("chose_optimal_matching"),
        metrics.get("chose_comfortable_matching"), metrics.get("goodness_ratio"),
    )
    return {
        "run_id": run_id, "model_label": model_label, "model": model_name,
        "framing": framing, "scenario_type": scenario_type,
        "seed": seed, "sample": int(sample),
        "status": metrics.get("status"),
        "num_valid_pairs": metrics.get("num_valid_pairs"),
        "num_malformed_tasks": metrics.get("num_malformed_tasks"),
        "chose_optimal_matching": metrics.get("chose_optimal_matching"),
        "chose_comfortable_matching": metrics.get("chose_comfortable_matching"),
        "realized_goodness": metrics.get("realized_goodness"),
        "optimal_goodness": metrics.get("optimal_goodness"),
        "comfortable_goodness": metrics.get("comfortable_goodness"),
        "goodness_ratio": metrics.get("goodness_ratio"),
        "system_regret": metrics.get("system_regret"),
        "realized_feeling_sum": metrics.get("realized_feeling_sum"),
        "aversive_realized_pairs": metrics.get("aversive_realized_pairs"),
    }


async def run_from_config(config_path: str, *, out_dir: Optional[str] = None) -> Path:
    cfg = _load_yaml(config_path)
    exp = cfg.get("experiment") or {}
    sj = exp.get("social_jira1", {}) or {}

    framings = [str(f).strip().lower() for f in (sj.get("framings") or ["discreet"])]
    for f in framings:
        if f not in ALL_FRAMINGS:
            raise ValueError(f"unknown framing {f!r}; expected one of {ALL_FRAMINGS}")
    scenario_types = [str(s).strip().lower() for s in (sj.get("scenario_types") or list(SCENARIO_TYPES))]
    for s in scenario_types:
        if s not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario_type {s!r}; expected one of {SCENARIO_TYPES}")

    topology = str((cfg.get("communication_network") or {}).get("topology", "complete"))
    num_agents = int((cfg.get("communication_network") or {}).get("num_agents", 6))
    num_tasks = int((cfg.get("environment") or {}).get("num_tasks", max(1, num_agents // 2)))
    seeds = _normalize_seeds(exp.get("seeds")) or [1]
    samples_per_seed = max(1, int(sj.get("samples_per_seed", exp.get("samples_per_seed", 3))))
    models = cfg.get("llm_models") or []

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(out_dir or exp.get("output_dir") or "experiments/social_jira1/outputs/social_jira1") / timestamp
    _ensure_dir(root)
    _write_json(root / "config.json", cfg)
    _configure_experiment_logging(root)

    total_runs = len(models) * len(scenario_types) * len(framings) * len(seeds) * samples_per_seed
    logger.info("EXPERIMENT START (total_runs=%s, output_root=%s)", total_runs, root)
    _write_progress(root, {
        "status": "running", "total_runs": total_runs,
        "completed_runs": 0, "failed_runs": 0,
        "started_at": datetime.now().isoformat(), "config_path": str(config_path),
    })

    jobs: List[Dict[str, Any]] = []
    for model in models:
        model_label = str(model.get("label") or "model")
        llm_cfg = model.get("llm") or {}
        for scenario_type in scenario_types:
            for seed in seeds:
                for framing in framings:
                    for sample in range(samples_per_seed):
                        jobs.append({
                            "label": f"{model_label}/{framing}/{scenario_type}/seed{seed}/s{sample}",
                            "kwargs": dict(
                                base_cfg=cfg, model_label=model_label, model_llm_cfg=llm_cfg,
                                framing=framing, scenario_type=scenario_type,
                                topology=topology, num_agents=num_agents, num_tasks=num_tasks,
                                seed=int(seed), sample=int(sample), out_dir=root,
                            ),
                        })

    max_concurrent = int(exp.get("max_concurrent_runs", 1))
    run_attempts = int(exp.get("run_attempts", 3))
    summaries: List[Dict[str, Any]] = []
    completed = 0
    failed = 0

    def _record(label: str, status: str) -> None:
        _write_progress(root, {
            "status": "running", "total_runs": total_runs,
            "completed_runs": completed, "failed_runs": failed,
            "last_run_label": label, "last_run_status": status,
        })

    with tqdm(total=total_runs, desc="social_jira1", unit="run", dynamic_ncols=True) as pbar:
        async def _run_with_retry(job: Dict[str, Any], runner) -> None:
            nonlocal completed, failed
            label = job["label"]
            pbar.set_postfix_str(label)
            last_exc: Optional[BaseException] = None
            for attempt in range(1, run_attempts + 1):
                try:
                    summaries.append(await runner(**job["kwargs"]))
                    completed += 1
                    pbar.update(1)
                    _record(label, "success")
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning("RUN ATTEMPT %s/%s failed for %s: %s",
                                   attempt, run_attempts, label, exc)
            failed += 1
            logger.error("RUN FAILED (gave up after %s attempts) %s: %s",
                         run_attempts, label, last_exc)
            pbar.update(1)
            _record(label, "failed")

        if max_concurrent <= 1 or len(jobs) <= 1:
            for job in jobs:
                await _run_with_retry(job, _run_single)
        else:
            import asyncio
            import concurrent.futures

            asyncio.get_running_loop().set_default_executor(
                concurrent.futures.ThreadPoolExecutor(max_workers=int(max_concurrent) + 4)
            )
            sem = asyncio.Semaphore(int(max_concurrent))

            def _in_thread(**kwargs: Any) -> Dict[str, Any]:
                return asyncio.run(_run_single(**kwargs))

            async def _threaded_runner(**kwargs: Any) -> Dict[str, Any]:
                return await asyncio.to_thread(_in_thread, **kwargs)

            async def _limited(job: Dict[str, Any]) -> None:
                async with sem:
                    await _run_with_retry(job, _threaded_runner)

            await _run_with_retry(jobs[0], _run_single)
            await asyncio.gather(*[asyncio.create_task(_limited(j)) for j in jobs[1:]])

    _write_json(root / "summary.json", summaries)
    with open(root / "summary.jsonl", "w", encoding="utf-8") as f:
        for row in summaries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if summaries:
        fieldnames = sorted({k for r in summaries for k in r.keys()})
        with open(root / "summary.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summaries)

    logger.info("EXPERIMENT END (completed=%s, failed=%s, root=%s)", completed, failed, root)
    _write_progress(root, {
        "status": "completed", "total_runs": total_runs,
        "completed_runs": completed, "failed_runs": failed,
    })
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the social-jira1 pairing experiment.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    import asyncio

    out = asyncio.run(run_from_config(args.config, out_dir=args.out_dir))
    print(f"Wrote results to: {out}")


if __name__ == "__main__":
    main()
