from __future__ import annotations

# ruff: noqa: E402

"""social-jira2 experiment runner.

Each agent is the personal assistant of an employee. Employees must be paired up to do
role-driven tasks; assistants privately know their own employee's (directional) feelings
about colleagues and must coordinate good pairings on a shared channel - while (under the
``default`` confidentiality preset) keeping those feelings confidential. The headline
question is about *how they communicate without leaking feelings*; v2 stays log-only (clean
transcripts + structured ground truth), with the leakage judge to be built later.

Sweep axes (SPEC §7):
  * models           - llm_models in the config (vLLM local).
  * scenario_type    - resolvable | conflict (the instance axis).
  * feelings_variant - preset names (§4); default [default, control] mirrors jira1.
  * personality      - personality names (§5); default [none]. (mixed is deferred.)
  * decoys           - off | on (§6); default [off]. (on is deferred.)
  * seeds            - roster + scenario draw.
  * samples          - repeats per cell (temperature variance).
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
from experiments.social_jira2.prompts import (
    ALL_PERSONALITIES,
    ALL_PRESETS,
    SocialJiraPrompts,
)
from experiments.social_jira2.metrics import compute_social_jira_metrics
from experiments.social_jira2.openrouter_client import OpenRouterClient
from experiments.social_jira2.scenario import SETUP_NAMES, normalize_setup
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

LOGGER_NAME = "experiments.social_jira2"
logger = logging.getLogger(LOGGER_NAME)

SCENARIO_TYPES = ("resolvable", "conflict")
DECOY_MODES = ("off", "on")  # `on` is a deferred hook (SPEC §6)
SETUP_CHOICES = ("base",) + SETUP_NAMES  # base = default single-matching roster (SPEC §2)


def _resolve_model_name(provider: str, llm_cfg: Dict[str, Any]) -> str:
    """Model name for the run, with repo-local support for the ``openrouter`` provider."""
    if provider == "openrouter":
        return str((llm_cfg.get("openrouter") or {}).get("model") or "unknown")
    return get_model_name(provider, llm_cfg)


def _make_client(llm_cfg: Dict[str, Any], *, agent_name: str, vllm_runtime: Any):
    """Build a client, routing the (repo-local) ``openrouter`` provider to OpenRouterClient.

    Every other provider is delegated to terrarium's :func:`get_client_instance`.
    """
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
        extra = getattr(msg, "model_extra", None) or {}  # vLLM puts reasoning_content here
        return {
            "reasoning_content": getattr(msg, "reasoning_content", None) or extra.get("reasoning_content"),
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
                "reasoning_content": msg.get("reasoning_content"),
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
                # FIRST, then commit (assign_task). Once the announcement lands, flip the
                # turn's (reused) generation params to force assign_task on the NEXT step, and
                # do NOT end the turn — let the forced commit step run. assign_task itself sets
                # `_env_state_committed` (state_updates) in the base handler, ending the turn.
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
                # Build once per turn (called before the step loop): start by forcing the
                # announcement post, stash a ref to THIS params dict so `_execute_tool_call`
                # can flip tool_choice -> assign_task after the post lands. Resets the stage
                # each turn. If there's no post_message tool, force assign_task immediately.
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


# Convergence detection via a private per-round "preliminary vote" (see jira1).
def _ballot_key(ballot: Dict[str, Any]):
    """Hashable canonical form of a {task: frozenset(pair) | None} ballot."""
    return tuple(sorted(
        (t, tuple(sorted(p)) if p else None) for t, p in (ballot or {}).items()
    ))


_THINK_SPAN_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _parse_ballot(text: str, employees: List[str], tasks: List[str]):
    """Parse a vote into ({task: frozenset(pair) | None}, {task: "A & B" | "none"}).

    Robust to reasoning-style output: closed ``<think>...</think>`` spans are dropped, and for
    each task we take the LAST line that both mentions it AND names a pair. Scanning from the
    end skips exploratory mentions earlier in a reasoning stream (the final pick comes last);
    requiring a pair on the line skips trailing caveats like "T1 could be none" that follow a
    clean answer (which is why a plain last-match would regress well-behaved models).
    """
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
    """Parse the ballot from the model's answer, falling back to its reasoning channel.

    Non-Harmony reasoning models (served via OpenRouter) often leave ``content`` empty and put
    the formatted ballot in their reasoning stream, so when ``content`` yields nothing usable we
    re-parse the captured CoT. Returns ``(ballot, display, source)`` with source in
    {"content", "reasoning", "none"}.
    """
    ballot, display = _parse_ballot(raw, employees, tasks)
    if not _ballot_is_empty(ballot):
        return ballot, display, "content"
    reasoning_text = _reasoning_text(reasoning_steps)
    if reasoning_text.strip():
        r_ballot, r_display = _parse_ballot(reasoning_text, employees, tasks)
        if not _ballot_is_empty(r_ballot):
            return r_ballot, r_display, "reasoning"
    return ballot, display, "none"


def _votes_converged(round_ballots: List[Dict[str, Any]], prev_ballots, num_agents: int):
    """(converged, reason). Stop on ~2/3 consensus or when nobody changed their vote."""
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
    """Pop the reasoning captured since the last drain (keep a vote turn's CoT separate)."""
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
            "run_id", "model_label", "model", "feelings_variant", "scenario_type",
            "personality", "decoys", "setup", "seed",
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
    feelings_variant: str,
    scenario_type: str,
    personality: str,
    decoys: str,
    setup: str,
    topology: str,
    num_agents: int,
    num_tasks: int,
    seed: int,
    sample: int = 0,
    resume: bool = False,
    out_dir: Path,
) -> Dict[str, Any]:
    feelings_variant = str(feelings_variant).strip().lower()
    scenario_type = str(scenario_type).strip().lower()
    personality = str(personality).strip().lower()
    decoys = str(decoys).strip().lower()
    setup_label = str(setup).strip().lower() or "base"
    setup_key = normalize_setup(setup_label)  # None for "base"
    if decoys != "off":
        raise NotImplementedError(
            f"decoys={decoys!r} is a deferred hook (SPEC §6); only 'off' is implemented."
        )

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
    cfg["environment"]["feelings_preset"] = feelings_variant
    cfg["environment"]["personality"] = personality
    cfg["environment"]["setup"] = setup_key  # None => default single-matching roster
    cfg["llm"] = copy.deepcopy(model_llm_cfg)

    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = (
        f"{model_label}__{feelings_variant}__{scenario_type}__{setup_label}__{personality}"
        f"__{topology}__n{num_agents}__t{num_tasks}__seed{seed}__s{sample}"
    )
    run_dir = out_dir.joinpath(
        "runs", model_label, setup_label, feelings_variant, scenario_type, personality, run_id
    )
    # Resume/glue: a run that already completed (has metrics.json) is reused, not re-run, so a
    # merged output dir can be filled in with only the missing runs.
    if resume and (run_dir / "metrics.json").exists():
        logger.info("RUN SKIP (cached) %s", run_id)
        return _cached_summary(run_dir)
    _ensure_dir(run_dir)
    logger.info("RUN START %s", run_id)

    cfg["simulation"]["run_timestamp"] = f"{run_timestamp}__{run_id}"
    cfg["simulation"]["tags"] = [str(exp.get("tag", "social_jira2"))]

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

    # Prompt variant hardened for weak tool-callers (first-round "you are first — propose" +
    # mandatory assign_task at execution). Off unless `experiment.robust_assignment: true`.
    robust_assignment = bool(exp.get("robust_assignment", False))

    env.feelings_preset = feelings_variant
    env.personality = personality
    env.robust_assignment = robust_assignment
    env.prompts = SocialJiraPrompts(
        env,
        cfg,
        feelings_preset=feelings_variant,
        personality=personality,
        robust_assignment=robust_assignment,
        experiment_prompt_logger=prompt_logger,
        log_prompts=log_prompts,
    )

    # Gate the execution-commit forcing (two forced sub-turns: announce -> assign_task) per
    # agent, so the global BaseAgent patches only force it when robust_assignment is on.
    for a in agents:
        a._force_execution_commit = robust_assignment

    planning_rounds = int(cfg["simulation"].get("max_planning_rounds", 3))
    preliminary_vote = bool(exp.get("preliminary_vote", True))
    early_stop_consensus = bool(exp.get("early_stop_consensus", True))
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
                # Parse content, falling back to the reasoning channel for models that leave
                # `content` empty (see _extract_ballot).
                ballot, display, vote_source = _extract_ballot(
                    raw, vote_reasoning, employees, task_ids
                )
                # If still unreadable, re-ask once with a hard "answer only" reminder.
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
        feelings_variant=feelings_variant,
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
        "feelings_variant": feelings_variant,
        "scenario_type": scenario_type,
        "personality": personality,
        "decoys": decoys,
        "setup": setup_label,
        "robust_assignment": robust_assignment,
        "environment_cfg": cfg.get("environment") or {},
        "environment_name": env.__class__.__name__,
        "topology": topology,
        "num_agents": num_agents,
        "num_tasks": num_tasks,
        "seed": seed,
        "agent_turn_order": [a.name for a in env.agents],
    })
    # Ground truth for the (future) leakage judge: roster roles, requirements, goodness table,
    # G*-set, comfortable matching, feelings, resolved preset + per-agent personality.
    _write_json(run_dir / "scenario.json", env.get_ground_truth())
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
        "RUN END %s (chose_optimal_goodness=%s chose_comfortable=%s goodness_ratio=%s)",
        run_id, metrics.get("chose_optimal_goodness"),
        metrics.get("chose_comfortable_matching"), metrics.get("goodness_ratio"),
    )
    return {
        "run_id": run_id, "model_label": model_label, "model": model_name,
        "feelings_variant": feelings_variant, "scenario_type": scenario_type,
        "personality": personality, "decoys": decoys, "setup": setup_label,
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


async def run_from_config(config_path: str, *, out_dir: Optional[str] = None) -> Path:
    cfg = _load_yaml(config_path)
    exp = cfg.get("experiment") or {}
    sj = exp.get("social_jira2", {}) or {}

    feelings_variants = [str(f).strip().lower() for f in (sj.get("feelings_variants") or ["default", "control"])]
    for f in feelings_variants:
        if f not in ALL_PRESETS:
            raise ValueError(f"unknown feelings_variant {f!r}; expected one of {ALL_PRESETS}")
    scenario_types = [str(s).strip().lower() for s in (sj.get("scenario_types") or list(SCENARIO_TYPES))]
    for s in scenario_types:
        if s not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario_type {s!r}; expected one of {SCENARIO_TYPES}")
    personalities = [str(p).strip().lower() for p in (sj.get("personalities") or ["none"])]
    for p in personalities:
        if p not in ALL_PERSONALITIES:
            raise ValueError(f"unknown personality {p!r}; expected one of {ALL_PERSONALITIES}")
    # YAML parses unquoted `off`/`on` as booleans, so coerce them back to mode strings.
    def _coerce_decoy(d: Any) -> str:
        if isinstance(d, bool):
            return "on" if d else "off"
        return str(d).strip().lower()

    decoy_modes = [_coerce_decoy(d) for d in (sj.get("decoys") or ["off"])]
    for d in decoy_modes:
        if d not in DECOY_MODES:
            raise ValueError(f"unknown decoys mode {d!r}; expected one of {DECOY_MODES}")
    setups = [str(s).strip().lower() for s in (sj.get("setups") or ["base"])]
    for s in setups:
        if s not in SETUP_CHOICES:
            raise ValueError(f"unknown setup {s!r}; expected one of {SETUP_CHOICES}")

    # `cells`: optional explicit list of {scenario_type, setup} pairs. When given it REPLACES
    # the scenario_types x setups cross-product, so one config can pair a setup with a specific
    # scenario type (e.g. conflict x {base,symmetric,pivot} + resolvable x {surplus}, since the
    # surplus roster can't satisfy the conflict invariant). Each pair still sweeps the other
    # axes (feelings/personality/seed/sample).
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
    # Resume mode writes into a FIXED dir (no timestamp) and skips already-completed runs, so an
    # interrupted/partial sweep can be filled in and glued into one directory per model.
    resume = bool(exp.get("resume", False))
    base_out = Path(out_dir or exp.get("output_dir") or "experiments/social_jira2/outputs/social_jira2")
    root = base_out if resume else base_out / timestamp
    _ensure_dir(root)
    _write_json(root / "config.json", cfg)
    _configure_experiment_logging(root)

    total_runs = (
        len(models) * len(cells) * len(feelings_variants)
        * len(personalities) * len(decoy_modes) * len(seeds) * samples_per_seed
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
                for feelings_variant in feelings_variants:
                    for personality in personalities:
                        for decoys in decoy_modes:
                            for sample in range(samples_per_seed):
                                jobs.append({
                                    "label": (
                                        f"{model_label}/{setup}/{feelings_variant}/{scenario_type}/"
                                        f"{personality}/seed{seed}/s{sample}"
                                    ),
                                    "kwargs": dict(
                                        base_cfg=cfg, model_label=model_label, model_llm_cfg=llm_cfg,
                                        feelings_variant=feelings_variant, scenario_type=scenario_type,
                                        personality=personality, decoys=decoys, setup=setup,
                                        topology=topology, num_agents=num_agents, num_tasks=num_tasks,
                                        seed=int(seed), sample=int(sample), resume=resume, out_dir=root,
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

    with tqdm(total=total_runs, desc="social_jira2", unit="run", dynamic_ncols=True) as pbar:
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
    parser = argparse.ArgumentParser(description="Run the social-jira2 pairing experiment.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    import asyncio

    out = asyncio.run(run_from_config(args.config, out_dir=args.out_dir))
    print(f"Wrote results to: {out}")


if __name__ == "__main__":
    main()
