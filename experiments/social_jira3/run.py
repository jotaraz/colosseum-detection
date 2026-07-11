from __future__ import annotations

# ruff: noqa: E402

"""social-jira3 experiment runner.

Each agent is the personal assistant of an employee. Employees must be paired up to do
role-driven tasks; the assistant's only evidence of its employee's dislike is one or more
personal messages buried in the employee's last-day conversation threads (SPEC §2), and the only discretion
instruction is one confidentiality norm (the `confidentiality` axis, SPEC §3). After the execution
phase each assistant writes a short private closing summary (SPEC §4). Like jira2, the base
build is log-only; the adapted turn judge and the new summary judge run offline.

Sweep axes (SPEC §5):
  * models            - llm_models in the config (vLLM local / openrouter).
  * scenario_type     - resolvable | conflict (the instance axis).
  * feelings_channel  - inbox | briefing (§2.5); default [inbox].
  * dislike_strength  - mild | strong | quit | quit2 | quit3 (§2.2); default [quit].
  * confidentiality   - none | audience | duty | self (§3.2); default [audience].
  * hint              - none | small | big | noconstraint (§3.2, escalate 'avoid inference'); default [none].
  * summary_audience  - employee | manager (§4.1); default [employee].
  * decoys            - on | off (§2.5a, private per-employee equal-overlap calendar); default [on].
  * personality       - jira2 registry (mixed stays deferred); default [none].
  * seeds, samples    - as in jira2. n_distractors / n_signal_threads / signal_recipient
                        are config scalars.
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
from experiments.social_jira3.prompts import (
    ALL_PERSONALITIES,
    PROMPT_VERSION,
    FEELINGS_CHANNELS,
    CONFIDENTIALITY_MODES,
    HINT_MODES,
    SUMMARY_AUDIENCES,
    SocialJiraPrompts,
)
from experiments.social_jira3.availability import DECOY_MODES
from experiments.social_jira3.inbox import ALL_STRENGTHS
from experiments.social_jira3.metrics import compute_social_jira_metrics
from experiments.social_jira2.openrouter_client import OpenRouterClient
from experiments.social_jira3.scenario import SETUP_NAMES, normalize_setup
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

LOGGER_NAME = "experiments.social_jira3"
logger = logging.getLogger(LOGGER_NAME)

SCENARIO_TYPES = ("resolvable", "conflict")
SETUP_CHOICES = ("base",) + SETUP_NAMES  # base = default single-matching roster


def _resolve_model_name(provider: str, llm_cfg: Dict[str, Any]) -> str:
    """Model name for the run, with repo-local support for the ``openrouter`` provider."""
    if provider == "openrouter":
        return str((llm_cfg.get("openrouter") or {}).get("model") or "unknown")
    return get_model_name(provider, llm_cfg)


def _make_client(llm_cfg: Dict[str, Any], *, agent_name: str, vllm_runtime: Any):
    """Build a client, routing the (repo-local) ``openrouter`` provider to OpenRouterClient."""
    if (llm_cfg.get("provider") or "").lower() == "openrouter":
        or_cfg = llm_cfg.get("openrouter") or {}
        return OpenRouterClient(
            base_url=str(or_cfg.get("base_url") or "https://openrouter.ai/api/v1"),
            api_key=or_cfg.get("api_key"),
            request_timeout=int(or_cfg.get("request_timeout", 120)),
            connect_timeout=int(or_cfg.get("connect_timeout", 30)),
            total_timeout=or_cfg.get("total_timeout"),
            extra_headers=or_cfg.get("extra_headers") or None,
        )
    return get_client_instance(llm_cfg, agent_name=agent_name, vllm_runtime=vllm_runtime)


def _configure_experiment_logging(root: Path, *, verbose: bool = True) -> None:
    _configure_experiment_logging_impl(logger, root, verbose=verbose)


def _reasoning_message(data: Any) -> Dict[str, Any]:
    """Pull the chat-completion ``message`` out of a generate_response result."""
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
        extra = getattr(msg, "model_extra", None) or {}  # vLLM puts reasoning here
        return {
            # vLLM 0.23 renamed the response field reasoning_content -> reasoning (0.12 still
            # uses reasoning_content, e.g. gpt-oss). Read both so CoT is captured on either.
            "reasoning_content": (
                getattr(msg, "reasoning_content", None) or extra.get("reasoning_content")
                or getattr(msg, "reasoning", None) or extra.get("reasoning")
            ),
            "content": getattr(msg, "content", None),
        }
    except Exception:
        return {}


def _install_reasoning_capture() -> bool:
    """Capture the model's chain-of-thought channel so it can be saved per run (gpt-oss)."""
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
                # 0.23 returns the CoT under "reasoning"; 0.12 under "reasoning_content".
                "reasoning_content": msg.get("reasoning_content") or msg.get("reasoning"),
                "content": msg.get("content"),
            })
        except Exception as exc:
            if not warned:
                warned.append(True)
                logger.warning("reasoning capture failed (will not retry-warn): %s", exc)
        return data, response_str

    generate_response.__wrapped__ = original_generate
    VLLMClient.generate_response = generate_response  # type: ignore[method-assign]
    VLLMClient._reasoning_capture_installed = True
    return True


def _install_single_post_per_planning_turn() -> bool:
    """Make each planning turn post exactly one blackboard message."""
    try:
        from terrarium.agents.base import BaseAgent
    except Exception:
        return False
    if getattr(BaseAgent, "_single_post_patch_installed", False):
        return True

    original = BaseAgent._execute_tool_call

    async def _execute_tool_call(self, tool_name, tool_arguments):  # type: ignore[no-untyped-def]
        result = await original(self, tool_name, tool_arguments)
        try:
            phase = str(getattr(self, "current_phase", ""))
            bb_tools = self.toolset_discovery.get_blackboard_tool_names()
            ok = not (isinstance(result, dict) and result.get("error"))
            if phase == "planning":
                if tool_name in bb_tools and ok:
                    self._env_state_committed = True  # end the turn after one post
            elif phase == "execution" and getattr(self, "_force_execution_commit", False):
                # Robust-assignment execution = two forced sub-turns: announce (post_message)
                # FIRST, then commit (assign_task). See jira2 run.py for the mechanism.
                if (
                    tool_name in bb_tools
                    and ok
                    and getattr(self, "_exec_forced_stage", "post") == "post"
                ):
                    self._exec_forced_stage = "assign"
                    ref = getattr(self, "_exec_params_ref", None)
                    if isinstance(ref, dict):
                        ref["tool_choice"] = {
                            "type": "function",
                            "function": {"name": "assign_task"},
                        }
        except Exception:
            pass
        return result

    _execute_tool_call.__wrapped__ = original
    BaseAgent._execute_tool_call = _execute_tool_call  # type: ignore[method-assign]
    BaseAgent._single_post_patch_installed = True
    return True


def _install_forced_post_in_planning() -> bool:
    """Force every planning turn to post exactly one message — structurally, not by luck."""
    try:
        from terrarium.agents.base import BaseAgent
    except Exception:
        return False
    if getattr(BaseAgent, "_forced_post_patch_installed", False):
        return True

    original = BaseAgent._build_generation_params

    def _build_generation_params(self, tool_set):  # type: ignore[no-untyped-def]
        params = original(self, tool_set)
        try:
            phase = str(getattr(self, "current_phase", ""))
            names = {
                ((t or {}).get("function") or {}).get("name")
                for t in (tool_set or [])
            }
            if phase == "planning":
                if "post_message" in names:
                    params["tool_choice"] = {
                        "type": "function",
                        "function": {"name": "post_message"},
                    }
            elif phase == "execution" and getattr(self, "_force_execution_commit", False):
                self._exec_forced_stage = "post" if "post_message" in names else "assign"
                self._exec_params_ref = params
                target = "post_message" if self._exec_forced_stage == "post" else "assign_task"
                if target in names:
                    params["tool_choice"] = {
                        "type": "function",
                        "function": {"name": target},
                    }
        except Exception:
            pass
        return params

    _build_generation_params.__wrapped__ = original
    BaseAgent._build_generation_params = _build_generation_params  # type: ignore[method-assign]
    BaseAgent._forced_post_patch_installed = True
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


# Convergence detection via a private per-round "preliminary vote" (the vote itself is
# unchanged from jira2 and matters MORE in v3 — it is the judge-free signal-uptake proxy,
# SPEC §6). The STOP RULE is revised (2026-07-07): the jira2 ~2/3-consensus trigger is
# REMOVED; planning always runs at least ``min_planning_rounds`` (default 3 — everyone gets
# to address the others at least three times), then stops early only when every agent's
# vote is identical to its own previous-round vote, and never exceeds max_planning_rounds.
def _ballot_key(ballot: Dict[str, Any]):
    """Hashable canonical form of a {task: frozenset(pair) | None} ballot."""
    return tuple(sorted(
        (t, tuple(sorted(p)) if p else None) for t, p in (ballot or {}).items()
    ))


_THINK_SPAN_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _parse_ballot(text: str, employees: List[str], tasks: List[str]):
    """Parse a vote into ({task: frozenset(pair) | None}, {task: "A & B" | "none"})."""
    cleaned = _THINK_SPAN_RE.sub(" ", text or "")
    lines = cleaned.splitlines()
    ballot: Dict[str, Any] = {}
    display: Dict[str, str] = {}
    for task in tasks:
        task_re = re.compile(r"\b" + re.escape(task) + r"\b", re.IGNORECASE)
        chosen: List[str] = []
        for ln in reversed(lines):
            if not task_re.search(ln):
                continue
            low = ln.lower()
            names: List[str] = []
            for emp in employees:
                if re.search(r"\b" + re.escape(emp.lower()) + r"\b", low) and emp not in names:
                    names.append(emp)
            if len(names) >= 2:
                chosen = names
                break
        if len(chosen) >= 2:
            ballot[task] = frozenset(chosen[:2])
            display[task] = " & ".join(sorted(chosen[:2]))
        else:
            ballot[task] = None
            display[task] = "none"
    return ballot, display


def _ballot_is_empty(ballot: Dict[str, Any]) -> bool:
    """True when no task got a parseable pair (every entry is None)."""
    return not any(v for v in (ballot or {}).values())


def _reasoning_text(reasoning_steps: List[Dict[str, Any]]) -> str:
    """Flatten captured CoT steps into a single string for fallback parsing."""
    return "\n".join(
        str(s.get("reasoning_content") or "") for s in (reasoning_steps or [])
    )


def _extract_ballot(
    raw: str,
    reasoning_steps: List[Dict[str, Any]],
    employees: List[str],
    tasks: List[str],
):
    """Parse the ballot from the model's answer, falling back to its reasoning channel."""
    ballot, display = _parse_ballot(raw, employees, tasks)
    if not _ballot_is_empty(ballot):
        return ballot, display, "content"
    reasoning_text = _reasoning_text(reasoning_steps)
    if reasoning_text.strip():
        r_ballot, r_display = _parse_ballot(reasoning_text, employees, tasks)
        if not _ballot_is_empty(r_ballot):
            return r_ballot, r_display, "reasoning"
    return ballot, display, "none"


def _votes_stable(round_ballots: List[Dict[str, Any]], prev_ballots) -> bool:
    """True when every agent's ballot is identical to its own previous-round ballot."""
    if prev_ballots is None or len(prev_ballots) != len(round_ballots):
        return False
    return all(_ballot_key(a) == _ballot_key(b) for a, b in zip(prev_ballots, round_ballots))


def _take_reasoning(agent: Any) -> List[Dict[str, Any]]:
    """Pop the reasoning captured since the last drain (keep a turn's CoT separate)."""
    client = getattr(agent, "client", None)
    steps = list(getattr(client, "_reasoning_steps", []) or []) if client is not None else []
    if client is not None:
        client._reasoning_steps = []
    return steps


def _cached_summary(run_dir: Path) -> Dict[str, Any]:
    """Rebuild a summary row from an already-completed run's on-disk artifacts (resume mode)."""
    m = json.loads((run_dir / "metrics.json").read_text())
    rc = json.loads((run_dir / "run_config.json").read_text())
    out: Dict[str, Any] = {
        k: rc.get(k) for k in (
            "run_id", "model_label", "model", "feelings_channel", "dislike_strength",
            "confidentiality", "hint", "summary_audience", "decoys", "scenario_type", "personality", "setup", "seed",
        )
    }
    out["sample"] = m.get("sample", rc.get("sample"))
    for k in (
        "status", "num_valid_pairs", "num_malformed_tasks", "chose_optimal_goodness",
        "chose_comfortable_matching", "realized_goodness", "optimal_goodness",
        "comfortable_goodness", "goodness_ratio", "system_regret", "realized_feeling_sum",
        "aversive_realized_pairs", "feelings_fallback",
    ):
        out[k] = m.get(k)
    return out


async def _run_single(
    *,
    base_cfg: Dict[str, Any],
    model_label: str,
    model_llm_cfg: Dict[str, Any],
    feelings_channel: str,
    dislike_strength: str,
    confidentiality: str,
    hint: str,
    summary_audience: str,
    decoys: str,
    scenario_type: str,
    personality: str,
    setup: str,
    topology: str,
    num_agents: int,
    num_tasks: int,
    seed: int,
    sample: int = 0,
    resume: bool = False,
    out_dir: Path,
) -> Dict[str, Any]:
    feelings_channel = str(feelings_channel).strip().lower()
    dislike_strength = str(dislike_strength).strip().lower()
    confidentiality = str(confidentiality).strip().lower()
    hint = str(hint).strip().lower()
    summary_audience = str(summary_audience).strip().lower()
    decoys = _coerce_onoff(decoys)
    scenario_type = str(scenario_type).strip().lower()
    personality = str(personality).strip().lower()
    setup_label = str(setup).strip().lower() or "base"
    setup_key = normalize_setup(setup_label)  # None for "base"

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
    # Recorded into the env so ground truth carries the resolved axes.
    cfg["environment"]["feelings_channel"] = feelings_channel
    cfg["environment"]["dislike_strength"] = dislike_strength
    cfg["environment"]["confidentiality"] = confidentiality
    cfg["environment"]["hint"] = hint
    cfg["environment"]["summary_audience"] = summary_audience
    cfg["environment"]["decoys"] = decoys
    cfg["environment"]["personality"] = personality
    cfg["environment"]["setup"] = setup_key  # None => default single-matching roster
    cfg["llm"] = copy.deepcopy(model_llm_cfg)

    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cell = f"{feelings_channel}-{dislike_strength}-conf{confidentiality}-hint{hint}-{summary_audience}-dec{decoys}"
    run_id = (
        f"{model_label}__{cell}__{scenario_type}__{setup_label}__{personality}"
        f"__{topology}__n{num_agents}__t{num_tasks}__seed{seed}__s{sample}"
    )
    run_dir = out_dir.joinpath(
        "runs", model_label, setup_label, cell, scenario_type, personality, run_id
    )
    # Resume/glue: a run that already completed (has metrics.json) is reused, not re-run.
    if resume and (run_dir / "metrics.json").exists():
        logger.info("RUN SKIP (cached) %s", run_id)
        return _cached_summary(run_dir)
    _ensure_dir(run_dir)
    logger.info("RUN START %s", run_id)

    cfg["simulation"]["run_timestamp"] = f"{run_timestamp}__{run_id}"
    cfg["simulation"]["tags"] = [str(exp.get("tag", "social_jira3"))]

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
    model_name = _resolve_model_name(provider, cfg["llm"])
    generation_params = get_generation_params(cfg["llm"])
    max_steps = int(cfg["simulation"].get("max_conversation_steps", 5))
    vllm_runtime = build_vllm_runtime(cfg["llm"]) if provider == "vllm" else None

    env_tool_name = str(getattr(env, "tools_environment_name", None) or env.__class__.__name__)
    agents: List[BaseAgent] = []
    for name in agent_names:
        client = _make_client(cfg["llm"], agent_name=name, vllm_runtime=vllm_runtime)
        agents.append(
            BaseAgent(
                client, name, model_name, max_steps, None,
                trajectory_logger, env_tool_name, generation_params=generation_params,
            )
        )
    env.set_agent_clients(agents)

    await env.async_init()

    # Prompt variant hardened for weak tool-callers (carried over from jira2).
    robust_assignment = bool(exp.get("robust_assignment", False))

    env.feelings_channel = feelings_channel
    env.dislike_strength = dislike_strength
    env.confidentiality = confidentiality
    env.hint = hint
    env.summary_audience = summary_audience
    env.decoys = decoys
    env.personality = personality
    env.robust_assignment = robust_assignment
    env.prompts = SocialJiraPrompts(
        env,
        cfg,
        feelings_channel=feelings_channel,
        confidentiality=confidentiality,
        hint=hint,
        summary_audience=summary_audience,
        personality=personality,
        robust_assignment=robust_assignment,
        experiment_prompt_logger=prompt_logger,
        log_prompts=log_prompts,
    )

    for a in agents:
        a._force_execution_commit = robust_assignment

    planning_rounds = int(cfg["simulation"].get("max_planning_rounds", 3))
    # Everyone must get to address the others at least this many times before any early
    # stop (capped by planning_rounds: if min > max, max wins and no early stop can fire).
    min_planning_rounds = int(exp.get("min_planning_rounds", 3))
    preliminary_vote = bool(exp.get("preliminary_vote", True))
    # `early_stop_consensus` accepted as a legacy alias for configs copied from jira2.
    early_stop_stable = bool(exp.get("early_stop_stable", exp.get("early_stop_consensus", True)))
    employees = list(env.agent_names)
    task_ids = list(env.scenario.task_ids)
    _install_reasoning_capture()
    _install_single_post_per_planning_turn()
    _install_forced_post_in_planning()
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

            if preliminary_vote:
                vote_ctx = env.build_agent_context(
                    agent.name, phase="survey", iteration=1, planning_round=planning_round
                )
                vote = await protocol.agent_survey_turn(
                    agent, agent.name, dict(vote_ctx), env, iteration=1
                )
                vote_reasoning = _take_reasoning(agent)  # keep the vote's CoT out of planning
                raw = vote.get("response") or ""
                ballot, display, vote_source = _extract_ballot(
                    raw, vote_reasoning, employees, task_ids
                )
                # If unreadable, re-ask once with a hard "answer only" reminder.
                if vote_source == "none":
                    retry_ctx = dict(vote_ctx)
                    retry_ctx["vote_retry"] = True
                    vote = await protocol.agent_survey_turn(
                        agent, agent.name, retry_ctx, env, iteration=1
                    )
                    retry_reasoning = _take_reasoning(agent)
                    retry_raw = vote.get("response") or ""
                    r_ballot, r_display, r_source = _extract_ballot(
                        retry_raw, retry_reasoning, employees, task_ids
                    )
                    if r_source != "none":
                        ballot, display = r_ballot, r_display
                        raw, vote_reasoning = retry_raw, retry_reasoning
                        vote_source = f"retry-{r_source}"
                    else:
                        vote_source = "retry-none"
                round_ballots.append(ballot)
                votes_log.setdefault(agent.name, {})[f"round_{planning_round}"] = {
                    "assignment": display, "raw": raw, "source": vote_source,
                    "reasoning": [s.get("reasoning_content") for s in vote_reasoning],
                }
                turns.append({
                    "phase": "preliminary_vote", "planning_round": planning_round,
                    "agent": agent.name, "assignment": display, "source": vote_source,
                    "response": raw, "usage": vote.get("usage"), "model": vote.get("model"),
                })

        if (
            early_stop_stable
            and preliminary_vote
            and planning_round >= max(2, min_planning_rounds)
            and _votes_stable(round_ballots, prev_ballots)
        ):
            logger.info(
                "RUN %s planning votes stable after round %d/%d (min %d) — "
                "ending planning early", run_id, planning_round, planning_rounds,
                min_planning_rounds,
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

    # ----- closing summary phase (SPEC §4): one final private plain-text turn per agent.
    # Rides the survey-turn plumbing (no tools, nothing posted); the context's phase field
    # routes the prompt to the CLOSING SUMMARY block. Free text — no retry machinery.
    summaries_log: Dict[str, Any] = {}
    if bool(exp.get("closing_summary", True)):
        for agent in env.agents:
            sum_ctx = env.build_agent_context(agent.name, phase="summary", iteration=1)
            response = await protocol.agent_survey_turn(
                agent, agent.name, dict(sum_ctx), env, iteration=1
            )
            sum_reasoning = _take_reasoning(agent)
            summaries_log[agent.name] = {
                "audience": summary_audience,
                "text": response.get("response"),
                "reasoning": [s.get("reasoning_content") for s in sum_reasoning],
            }
            turns.append({
                "phase": "summary", "agent": agent.name,
                "response": response.get("response"), "usage": response.get("usage"),
                "model": response.get("model"),
            })

    final_summary = env.get_final_summary()
    metrics = compute_social_jira_metrics(
        env=env,
        feelings_channel=feelings_channel,
        dislike_strength=dislike_strength,
        confidentiality=confidentiality,
        hint=hint,
        summary_audience=summary_audience,
        decoys=decoys,
        scenario_type=scenario_type,
        personality=personality,
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
        "feelings_channel": feelings_channel,
        "dislike_strength": dislike_strength,
        "confidentiality": confidentiality,
        "hint": hint,
        "summary_audience": summary_audience,
        "decoys": decoys,
        "scenario_type": scenario_type,
        "personality": personality,
        "setup": setup_label,
        "robust_assignment": robust_assignment,
        "prompt_version": PROMPT_VERSION,
        "environment_cfg": cfg.get("environment") or {},
        "environment_name": env.__class__.__name__,
        "topology": topology,
        "num_agents": num_agents,
        "num_tasks": num_tasks,
        "seed": seed,
        "agent_turn_order": [a.name for a in env.agents],
    })
    # Ground truth for the offline judges: roster, requirements, goodness table, G*-set,
    # comfortable matching, feelings, per-agent inbox with the signal flagged, resolved axes.
    _write_json(run_dir / "scenario.json", env.get_ground_truth())
    _write_json(run_dir / "final_summary.json", final_summary)
    _write_json(run_dir / "agent_turns.json", turns)
    _write_json(run_dir / "agent_reasoning.json", reasoning_log)
    _write_json(run_dir / "agent_votes.json", votes_log)
    _write_json(run_dir / "summaries.json", summaries_log)
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
        "RUN END %s (chose_optimal_goodness=%s chose_comfortable=%s goodness_ratio=%s)",
        run_id, metrics.get("chose_optimal_goodness"),
        metrics.get("chose_comfortable_matching"), metrics.get("goodness_ratio"),
    )
    return {
        "run_id": run_id, "model_label": model_label, "model": model_name,
        "feelings_channel": feelings_channel, "dislike_strength": dislike_strength,
        "confidentiality": confidentiality, "hint": hint, "summary_audience": summary_audience, "decoys": decoys,
        "scenario_type": scenario_type, "personality": personality, "setup": setup_label,
        "seed": seed, "sample": int(sample),
        "status": metrics.get("status"),
        "num_valid_pairs": metrics.get("num_valid_pairs"),
        "num_malformed_tasks": metrics.get("num_malformed_tasks"),
        "chose_optimal_goodness": metrics.get("chose_optimal_goodness"),
        "chose_comfortable_matching": metrics.get("chose_comfortable_matching"),
        "realized_goodness": metrics.get("realized_goodness"),
        "optimal_goodness": metrics.get("optimal_goodness"),
        "comfortable_goodness": metrics.get("comfortable_goodness"),
        "goodness_ratio": metrics.get("goodness_ratio"),
        "system_regret": metrics.get("system_regret"),
        "realized_feeling_sum": metrics.get("realized_feeling_sum"),
        "aversive_realized_pairs": metrics.get("aversive_realized_pairs"),
        "feelings_fallback": metrics.get("feelings_fallback"),
    }


def _coerce_onoff(v: Any) -> str:
    """YAML parses unquoted on/off as booleans; coerce them back to mode strings."""
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v).strip().lower()


async def run_from_config(config_path: str, *, out_dir: Optional[str] = None) -> Path:
    cfg = _load_yaml(config_path)
    exp = cfg.get("experiment") or {}
    sj = exp.get("social_jira3", {}) or {}

    feelings_channels = [str(c).strip().lower() for c in (sj.get("feelings_channels") or ["inbox"])]
    for c in feelings_channels:
        if c not in FEELINGS_CHANNELS:
            raise ValueError(f"unknown feelings_channel {c!r}; expected one of {FEELINGS_CHANNELS}")
    dislike_strengths = [str(s).strip().lower() for s in (sj.get("dislike_strengths") or ["quit"])]
    for s in dislike_strengths:
        if s not in ALL_STRENGTHS:
            raise ValueError(f"unknown dislike_strength {s!r}; expected one of {ALL_STRENGTHS}")
    confidentialities = [str(c).strip().lower() for c in (sj.get("confidentiality") or ["audience"])]
    for c in confidentialities:
        if c not in CONFIDENTIALITY_MODES:
            raise ValueError(f"unknown confidentiality {c!r}; expected one of {CONFIDENTIALITY_MODES}")
    hints = [str(h).strip().lower() for h in (sj.get("hints") or ["none"])]
    for h in hints:
        if h not in HINT_MODES:
            raise ValueError(f"unknown hint {h!r}; expected one of {HINT_MODES}")
    summary_audiences = [str(a).strip().lower() for a in (sj.get("summary_audiences") or ["employee"])]
    for a in summary_audiences:
        if a not in SUMMARY_AUDIENCES:
            raise ValueError(f"unknown summary_audience {a!r}; expected one of {SUMMARY_AUDIENCES}")
    decoy_modes = [_coerce_onoff(d) for d in (sj.get("decoys") or ["on"])]
    for d in decoy_modes:
        if d not in DECOY_MODES:
            raise ValueError(f"unknown decoys mode {d!r}; expected one of {DECOY_MODES}")
    scenario_types = [str(s).strip().lower() for s in (sj.get("scenario_types") or list(SCENARIO_TYPES))]
    for s in scenario_types:
        if s not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario_type {s!r}; expected one of {SCENARIO_TYPES}")
    personalities = [str(p).strip().lower() for p in (sj.get("personalities") or ["none"])]
    for p in personalities:
        if p not in ALL_PERSONALITIES:
            raise ValueError(f"unknown personality {p!r}; expected one of {ALL_PERSONALITIES}")
    setups = [str(s).strip().lower() for s in (sj.get("setups") or ["base"])]
    for s in setups:
        if s not in SETUP_CHOICES:
            raise ValueError(f"unknown setup {s!r}; expected one of {SETUP_CHOICES}")

    # `cells`: optional explicit list of {scenario_type, setup} pairs, replacing the
    # scenario_types x setups cross-product (same semantics as jira2).
    cells_cfg = sj.get("cells")
    if cells_cfg:
        cells = []
        for c in cells_cfg:
            st = str((c or {}).get("scenario_type", "")).strip().lower()
            su = str((c or {}).get("setup", "base")).strip().lower()
            if st not in SCENARIO_TYPES:
                raise ValueError(f"cells: unknown scenario_type {st!r}; expected one of {SCENARIO_TYPES}")
            if su not in SETUP_CHOICES:
                raise ValueError(f"cells: unknown setup {su!r}; expected one of {SETUP_CHOICES}")
            cells.append((st, su))
    else:
        cells = [(st, su) for st in scenario_types for su in setups]

    topology = str((cfg.get("communication_network") or {}).get("topology", "complete"))
    num_agents = int((cfg.get("communication_network") or {}).get("num_agents", 4))
    num_tasks = int((cfg.get("environment") or {}).get("num_tasks", max(1, num_agents // 2)))
    seeds = _normalize_seeds(exp.get("seeds")) or [1]
    samples_per_seed = max(1, int(sj.get("samples_per_seed", exp.get("samples_per_seed", 3))))
    models = cfg.get("llm_models") or []

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    resume = bool(exp.get("resume", False))
    base_out = Path(out_dir or exp.get("output_dir") or "experiments/social_jira3/outputs/social_jira3")
    root = base_out if resume else base_out / timestamp
    _ensure_dir(root)
    _write_json(root / "config.json", cfg)
    _configure_experiment_logging(root)

    # Valid (confidentiality, hint) tuples: `none` only pairs with hint=none (no norm to hint).
    n_conf_hint = sum(
        1 for c in confidentialities for h in hints if not (c == "none" and h != "none")
    )
    total_runs = (
        len(models) * len(cells) * len(feelings_channels) * len(dislike_strengths)
        * n_conf_hint * len(summary_audiences) * len(decoy_modes) * len(personalities)
        * len(seeds) * samples_per_seed
    )
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
        for scenario_type, setup in cells:
            for seed in seeds:
                for channel in feelings_channels:
                    for strength in dislike_strengths:
                        for confidentiality in confidentialities:
                          for hint in hints:
                            # A hint only attaches to a real norm: `none` has no norm, so it
                            # only pairs with hint=none (skip none x small/big/noconstraint —
                            # this trims the 4x4 cross-product to the 13 valid (conf,hint) tuples).
                            if confidentiality == "none" and hint != "none":
                                continue
                            for audience in summary_audiences:
                                for decoys in decoy_modes:
                                    for personality in personalities:
                                        for sample in range(samples_per_seed):
                                            jobs.append({
                                                "label": (
                                                    f"{model_label}/{setup}/{channel}-{strength}-conf{confidentiality}-hint{hint}-"
                                                    f"{audience}-dec{decoys}/{scenario_type}/{personality}/seed{seed}/s{sample}"
                                                ),
                                                "kwargs": dict(
                                                    base_cfg=cfg, model_label=model_label,
                                                    model_llm_cfg=llm_cfg,
                                                    feelings_channel=channel,
                                                    dislike_strength=strength,
                                                    confidentiality=confidentiality,
                                                    hint=hint,
                                                    summary_audience=audience,
                                                    decoys=decoys,
                                                    scenario_type=scenario_type,
                                                    personality=personality, setup=setup,
                                                    topology=topology, num_agents=num_agents,
                                                    num_tasks=num_tasks,
                                                    seed=int(seed), sample=int(sample),
                                                    resume=resume, out_dir=root,
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

    with tqdm(total=total_runs, desc="social_jira3", unit="run", dynamic_ncols=True) as pbar:
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
    parser = argparse.ArgumentParser(description="Run the social-jira3 pairing experiment.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    import asyncio

    out = asyncio.run(run_from_config(args.config, out_dir=args.out_dir))
    print(f"Wrote results to: {out}")


if __name__ == "__main__":
    main()
