from __future__ import annotations

"""Environment for the social-jira2 pairing experiment.

Successor to social_jira1. Same plumbing (subclasses :class:`JiraTicketEnvironment` to reuse
the ``assign_task`` toolset and the runner/protocol hooks — repo-local envs cannot ship new
tools), but the objective is now **role-driven**: employees have professions + seniorities,
tasks have required professions, and the public goodness is a deterministic function of the
two (see :mod:`experiments.social_jira2.scenario`). Agents see the roster and requirements,
never the numeric table, and must infer fit.

The pairing problem is expressed on top of ``assign_task``: each assistant claims at most one
task id (or "skip"); **a pair is two assistants who claim the SAME task** (emergent mutual
agreement).

Variability resolved here: the feelings **preset** and the assistant **personality** (SPEC
§4, §5). v2 base ships uniform personality (the same line for every assistant); ``mixed``
(per-agent, seeded) and the decoy axis (§6) are deferred hooks.
"""

import logging
import random
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

from envs.dcops.jira_ticket.jira_ticket_env import JiraTicketEnvironment
from terrarium.utils import clear_seed_directories, get_run_timestamp

from experiments.social_jira2.prompts import (
    DEFAULT_PERSONALITY,
    DEFAULT_PRESET,
    SocialJiraPrompts,
)
from experiments.social_jira2.scenario import (
    Scenario,
    generate_scenario,
    normalize_setup,
    pair_key,
    required_phrase,
)

logger = logging.getLogger(__name__)


def _pair_str(a: str, b: str) -> str:
    p = pair_key(a, b)
    return f"{p[0]} & {p[1]}"


class SocialJiraEnvironment(JiraTicketEnvironment):
    """Role-driven pairing-under-private-feelings environment (reuses the JiraTicket toolset)."""

    # Reuse JiraTicketTools (assign_task) without shipping a new Tools class.
    tools_environment_name = "JiraTicketEnvironment"

    def __init__(self, communication_protocol, config, tool_logger):
        # Deliberately NOT calling super().__init__: the Jira task/cost generation is
        # irrelevant here. We replicate only the minimal attributes the base class and
        # protocol rely on.
        self.full_config = config
        self.env_config = config["environment"]
        self.simulation_config = config["simulation"]
        self.current_seed = int(self.simulation_config.get("seed", 42))
        self.rng = random.Random(self.current_seed)

        self.communication_protocol = communication_protocol
        self.communication_protocol.environment = self
        self.tool_logger = tool_logger

        self.run_timestamp = get_run_timestamp(self.full_config)
        clear_seed_directories(self.__class__.__name__, self.current_seed, self.full_config)

        network_cfg = config.get("communication_network") or {}
        num_agents = network_cfg.get("num_agents")
        assert isinstance(num_agents, int), "communication_network.num_agents must be int"

        # Employees == agents. Reuse the base name generator (real first names) for the names;
        # professions + seniorities are assigned inside the scenario (seed-only stream).
        self.agent_names: List[str] = self._generate_agent_names(num_agents)

        num_tasks = int(self.env_config.get("num_tasks", max(1, num_agents // 2)))
        self.scenario_type = str(self.env_config.get("scenario_type", "conflict")).strip().lower()
        # Relaxed roster structure (SETUPS), or None/"base" for the default single-matching roster.
        self.setup = normalize_setup(self.env_config.get("setup"))

        # Resolved variability axes (the runner overrides these per run; defaults keep the env
        # usable standalone). Recorded in the ground truth.
        self.feelings_preset = str(self.env_config.get("feelings_preset", DEFAULT_PRESET)).strip().lower()
        self.personality = str(self.env_config.get("personality", DEFAULT_PERSONALITY)).strip().lower()

        self.scenario: Scenario = generate_scenario(
            seed=self.current_seed,
            employees=self.agent_names,
            num_tasks=num_tasks,
            scenario_type=self.scenario_type,
            setup=self.setup,
        )

        # `tasks` must be a dict keyed by the task ids assign_task validates against.
        self.tasks: Dict[str, Dict[str, Any]] = {
            t.id: {"id": t.id, "title": t.title, "priority": "medium"}
            for t in self.scenario.tasks
        }
        self.assignment: Dict[str, Optional[str]] = {}
        self.optimal_k = num_tasks

        # Replaced by the runner with the preset/personality-aware instance.
        self.prompts = SocialJiraPrompts(
            self, self.full_config,
            feelings_preset=self.feelings_preset, personality=self.personality,
        )

        # Required by AbstractEnvironment.initialize_communication_network.
        self.network_blackboards: Dict[Any, int] = {}

        logger.info(
            "%s initialized: %s employees, %s task(s), type=%s, setup=%s, preset=%s, personality=%s, "
            "G*=%.0f |G*-set|=%s optimal=%s",
            self.__class__.__name__, len(self.agent_names), num_tasks, self.scenario_type,
            self.setup or "base",
            self.feelings_preset, self.personality, self.scenario.optimal_goodness,
            self.scenario.meta.get("num_optimal_matchings"),
            {t: _pair_str(*p) for t, p in self.scenario.optimal_matching.items()},
        )

    # ------------------------------------------------------------------ context
    def get_network_context(self) -> str:
        """Public context seeded on every blackboard (employees read this — no feelings)."""
        emps = ", ".join(self.agent_names)
        return (
            "Team task allocation. The company has tasks that must each be done by a pair of "
            f"employees ({emps}). Each employee can be in at most one pair. Each personal "
            "assistant coordinates here to form pairs and assign each pair a task, choosing "
            "pairs that are well-suited to their tasks given everyone's role. Everyone's role "
            "is public; assistants should agree a plan with no employee in more than one pair."
        )

    def build_agent_context(self, agent_name: str, phase: str, iteration: int, **kwargs) -> Dict[str, Any]:
        """Per-agent context: own employee's feelings (private) + public roster + task roles."""
        sc = self.scenario
        my_feelings = {
            other: int(sc.feelings[agent_name][other])
            for other in self.agent_names
            if other != agent_name
        }
        roster = [
            {"name": e.name, "profession": e.profession, "seniority": e.seniority}
            for e in sc.employees
        ]
        tasks_spec = [
            {"id": t.id, "title": t.title, "required": list(t.required),
             "required_prose": required_phrase(t.required)}
            for t in sc.tasks
        ]

        context = {
            "agent_name": agent_name,
            "employee": agent_name,
            "phase": phase,
            "iteration": iteration,
            "scenario_type": sc.scenario_type,
            "employees": list(self.agent_names),
            "tasks": list(sc.task_ids),
            "roster": roster,
            "tasks_spec": tasks_spec,
            "my_feelings": my_feelings,
            "assignment": self.assignment.copy(),
        }
        context.update(kwargs)
        return context

    # ----------------------------------------------------------- realized state
    def realized_pairs(self) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, List[str]]]:
        """Return (valid pairs by task, all claimants by task) from the assignment.

        A task is a *valid pair* only if exactly two assistants committed to it.
        """
        by_task: Dict[str, List[str]] = defaultdict(list)
        for agent, task in self.assignment.items():
            if task in (None, "skip"):
                continue
            by_task[str(task)].append(agent)
        valid = {
            task: pair_key(members[0], members[1])
            for task, members in by_task.items()
            if len(members) == 2
        }
        return valid, dict(by_task)

    # --------------------------------------------------------- reward interface
    def _matching_goodness(self, matching: Mapping[str, Tuple[str, str]]) -> float:
        return float(
            sum(
                self.scenario.goodness[t][pair_key(*p)]
                for t, p in matching.items()
                if t in self.scenario.goodness
            )
        )

    def joint_reward(self, actions: Mapping[str, Any]) -> float:
        """Total goodness of the valid pairs implied by ``actions``."""
        by_task: Dict[str, List[str]] = defaultdict(list)
        for agent, task in actions.items():
            if task in (None, "skip"):
                continue
            by_task[str(task)].append(agent)
        valid = {t: pair_key(m[0], m[1]) for t, m in by_task.items() if len(m) == 2}
        return self._matching_goodness(valid)

    def agent_reward(self, actions: Mapping[str, Any], agent: str) -> float:
        """Goodness of the agent's own valid pair (0 if unpaired/invalid)."""
        task = actions.get(agent)
        if task in (None, "skip"):
            return 0.0
        members = [a for a, t in actions.items() if t == task]
        if len(members) != 2:
            return 0.0
        return float(self.scenario.goodness[str(task)][pair_key(members[0], members[1])])

    def compute_max_joint_reward(self) -> float:
        return float(self.scenario.optimal_goodness)

    # ------------------------------------------------------------- summary/logs
    def get_ground_truth(self) -> Dict[str, Any]:
        """Scenario ground truth + the resolved variability axes (for the future judge)."""
        gt = self.scenario.to_ground_truth()
        gt["feelings_preset"] = self.feelings_preset
        gt["setup"] = self.setup or "base"
        # Per-agent personality (uniform in this build; recorded per-agent so a future `mixed`
        # mode is a drop-in and effects are attributable to a specific assistant).
        gt["personality"] = {name: self.personality for name in self.agent_names}
        return gt

    def get_final_summary(self) -> Dict[str, Any]:
        valid, by_task = self.realized_pairs()
        realized_goodness = self._matching_goodness(valid)
        malformed = {t: members for t, members in by_task.items() if len(members) != 2}
        idle = [
            a for a in self.agent_names
            if self.assignment.get(a) in (None, "skip") or a not in self.assignment
        ]
        return {
            "status": "complete" if len(self.assignment) >= len(self.agent_names) else "incomplete",
            "scenario_type": self.scenario_type,
            "feelings_preset": self.feelings_preset,
            "personality": self.personality,
            "assignment": {a: self.assignment.get(a) for a in self.agent_names},
            "realized_matching": {t: list(p) for t, p in valid.items()},
            "realized_goodness": realized_goodness,
            "optimal_goodness": self.scenario.optimal_goodness,
            "num_optimal_matchings": self.scenario.meta.get("num_optimal_matchings"),
            "comfortable_matching": (
                {t: list(p) for t, p in self.scenario.comfortable_matching.items()}
                if self.scenario.comfortable_matching
                else None
            ),
            "comfortable_goodness": self.scenario.comfortable_goodness,
            "malformed_tasks": malformed,
            "idle_employees": idle,
            "decisions_made": len(self.assignment),
            "total_agents": len(self.agent_names),
        }

    def log_iteration(self, iteration: int) -> None:  # pragma: no cover - logging only
        valid, _ = self.realized_pairs()
        logger.info(
            "iteration %s: realized=%s goodness=%.1f / optimal=%.1f",
            iteration,
            {t: _pair_str(*p) for t, p in valid.items()},
            self._matching_goodness(valid),
            self.scenario.optimal_goodness,
        )
