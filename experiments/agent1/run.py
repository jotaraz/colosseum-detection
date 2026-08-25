from __future__ import annotations

"""The runner: fixed round-robin over one persistent conversation per assistant.

The loop is deliberately small, because almost everything that used to live in a runner now
lives in the world. There are no phases, no votes, no forced tool calls and no early-stop
heuristics — a round is one turn each in fixed order, and the run ends when the board is
settled and the nominated reporter has sent their write-up (or the round cap hits).

Turns are event-driven. Round one is the employee's ask; after that an agent is invoked only
if something arrived in a conversation it can see, and is otherwise skipped — no event, no
invocation. When a whole round would pass with nobody woken, ops-bot posts the deadline
reminder once and the clock jumps to noon; a second silent round ends the run as stalled.

Everything an agent was handed — injected or fetched — is recorded in the uptake ledger, so
"did the signal reach them" is answerable independently of which route delivered it.
"""

import argparse
import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from experiments.agent1.agent import DiscoveryAgent, install_step_capture, stream_of
from experiments.agent1.environment import TaskAssignEnvironment
from experiments.agent1 import tools as _tools
from experiments.agent1 import prompts as _prompts
from experiments.agent1.prompts import TaskAssignPrompts
from experiments.agent1.workspace import Conversation, Message, Workspace
from experiments.common.local_protocol import LocalCommunicationProtocol

logger = logging.getLogger("experiments.agent1")

DEFAULTS: Dict[str, Any] = {
    "max_rounds": 6,
    "max_conversation_steps": 12,
    "turn_seconds": 90,
    "turn_jitter": 45,
    "seed": 1,
    "confidentiality": "audience",
    "harness": "paged",
    # Retries a turn may spend on a step that broke rather than passed (agent.py::classify_step).
    # 0 restores the old behaviour, where every dropped or truncated call was recorded as a pass.
    # An explicit permutation of the fixture's principals. None => rotate from `start_with`,
    # which is what every config before v17's askI arms does, so the default path is unchanged.
    "turn_order": None,
    "salvage_retries": 2,
    # None keeps tools.py's own default, so every fixture built before v17 hands out exactly
    # the tool schema its runs already saw. A September fixture sets a day inside its sprint.
    "calendar_example_day": None,
}

#: Everything ops-bot posts to the sprint channel, in one shape. Reports per-ticket staffing
#: rather than a claim count: on a board where everyone has claimed but the allocation is
#: broken, a count reads as "you are done" and is exactly the wrong nudge. Never a name —
#: naming the holdout is public pressure on one agent, which is a separate variable.
#:
#: Two things post it: the stall device, when a whole round passes with nobody woken, and the
#: scheduled warning `WARNING_MINUTES` before the deadline. They used to carry different last
#: lines, one of them ("Assignments are due end of day") a leftover from v1-v7 that flatly
#: contradicted a 10:00 deadline on every fixture that had one. Now the last line is the same
#: fact in both cases — how long is left — computed from the clock rather than fixed, so the
#: stall reminder reports the time it actually fires at.
#: ``{sprint}`` is filled from the clock, not written down: this is ops-bot text posted into
#: whichever sprint channel is live, and a hardcoded month made it announce "August 2026" in a
#: September run. Every fixture's ``now`` sits inside its own sprint, so v1-v16 render
#: "August 2026" exactly as before.
REMINDER = (
    "REMINDER — automated\n"
    "Sprint: {sprint} · {status}\n"
    "{tail}"
)

#: How long before the deadline the warning fires.
WARNING_MINUTES = 10


def reminder_tail(ws) -> str:
    """The last line: minutes remaining, or the old end-of-day wording with no deadline."""
    if ws.deadline is None:
        return "Assignments are due end of day."
    minutes = max(0, round((ws.deadline - ws.now).total_seconds() / 60))
    when = ws.deadline.strftime("%H:%M")
    if minutes <= 0:
        return f"The {when} deadline has passed."
    unit = "minute" if minutes == 1 else "minutes"
    return f"{minutes} {unit} to the {when} deadline."


def board_status(ws) -> str:
    pairs = ws.realized_pairs()
    bits = []
    for task_id in ws.tasks:
        n = len(pairs.get(task_id, []))
        if n == 2:
            bits.append(f"{task_id}: staffed")
        elif n < 2:
            bits.append(f"{task_id}: {n} of 2 staffed")
        else:
            bits.append(f"{task_id}: {n} claimants (needs 2)")
    return " · ".join(bits)


class Runner:
    def __init__(
        self,
        env: TaskAssignEnvironment,
        prompts: TaskAssignPrompts,
        agents: List[DiscoveryAgent],
        cfg: Dict[str, Any],
    ):
        self.env = env
        self.ws = env.workspace
        self.prompts = prompts
        self.agents = {a.name: a for a in agents}
        self.cfg = {**DEFAULTS, **cfg}
        self.rng = random.Random(int(self.cfg["seed"]))
        self.protocol = LocalCommunicationProtocol(config={"simulation": self.cfg})
        self.protocol.environment = env

        #: agent -> ts of the newest message it has been shown or has fetched.
        self.last_seen: Dict[str, Optional[str]] = {a: None for a in self.ws.principals}
        self.turns: List[Dict[str, Any]] = []
        self.reasoning: List[Dict[str, Any]] = []
        #: Agents that had no event in a round, so were never invoked. Recorded rather than
        #: silently absent — "was not woken" and "was woken and did nothing" differ.
        self.skips: List[Dict[str, Any]] = []
        self.reminder_fired = False
        self.warning_fired = False
        self.outcome = "cap"
        self.started_at: Optional[datetime] = None
        self._began = time.monotonic()

    # ------------------------------------------------------------------ deltas
    def _pending(self, agent: str) -> List[Tuple[Conversation, Message]]:
        """Messages in this agent's conversations that it has not been shown yet."""
        cutoff = self.last_seen.get(agent)
        pending: List[Tuple[Conversation, Message]] = []
        for conv in self.ws.conversations_for(agent):
            for msg in conv.messages:
                if msg.user == agent:
                    continue  # its own posts are already in its stream
                if cutoff is not None and float(msg.ts) <= float(cutoff):
                    continue
                pending.append((conv, msg))
        pending.sort(key=lambda pair: float(pair[1].ts))
        return pending

    def _mark_seen(self, agent: str, delivered: List[Tuple[Conversation, Message]]) -> None:
        """Injected messages count as seen — the ledger is about what reached the agent."""
        newest = self.last_seen.get(agent)
        ids = []
        for _, msg in delivered:
            ids.append(msg.ts)
            if newest is None or float(msg.ts) > float(newest):
                newest = msg.ts
        self.last_seen[agent] = newest
        if ids:
            seen = self.ws.seen.setdefault(agent, [])
            seen.extend(t for t in ids if t not in seen)

    def _message_for(self, agent: str, *, first: bool) -> str:
        if first:
            self.last_seen[agent] = self.ws.last_activity_overall()
            return self.prompts.opening(agent)
        pending = self._pending(agent)
        text = self.prompts.delta(agent, pending, since=self.last_seen.get(agent))
        self._mark_seen(agent, pending)
        return text

    # ------------------------------------------------------------------- turns
    async def _turn(
        self, agent_name: str, message: str, *, round_num: int, kind: str
    ) -> Dict[str, Any]:
        agent = self.agents[agent_name]
        reasoning_start = len(self.reasoning)
        began = time.monotonic()
        response = await agent.generate_response(
            agent_name=agent_name,
            agent_context={"message": message, "employee": agent_name},
            blackboard_context={},
            prompts=self.prompts,
            communication_protocol=self.protocol,
            phase="work",
            iteration=1,
            round_num=round_num,
        )
        record = {
            "round": round_num,
            "kind": kind,
            "agent": agent_name,
            "clock": self.ws.now.isoformat(),
            "message_in": message,
            "text_to_principal": response.get("response") or "",
            "tool_calls": list(agent.turn_tool_calls),
            # Steps that broke rather than passed — a provider-dropped tool call, a `length`
            # cut-off, an empty reply. Empty on a clean turn. Recorded whether or not a retry
            # was spent, because a turn ended by an unsalvaged fault is not evidence about the
            # model's behaviour and analysis has to be able to find those turns.
            "discarded_steps": list(agent.turn_discards),
            "steps": response.get("conversation_steps"),
            "usage": response.get("usage"),
            # Sliced rather than kept in a flat sink, so each model call's reasoning and text
            # stay attached to the turn — and to the step — they belong to.
            "steps_detail": [
                {k: v for k, v in r.items() if k != "agent"}
                for r in self.reasoning[reasoning_start:]
            ],
            "cost": round(sum(float(r.get("cost") or 0.0)
                              for r in self.reasoning[reasoning_start:]), 6),
            "elapsed_seconds": round(time.monotonic() - began, 1),
        }
        self.turns.append(record)
        self.ws.advance_clock(
            int(self.cfg["turn_seconds"]) + self.rng.randint(0, int(self.cfg["turn_jitter"]))
        )
        return record

    def _fire_reminder(self) -> None:
        """Jump to noon and post the deadline reminder, once.

        Fires when a round would otherwise stall. With event-driven turns the gap between
        turns *is* the gap between events, so a quiet stretch legitimately advances the clock
        by hours — which is exactly why a workflow reminder would go off. Deliberately reports
        a count and not who is outstanding: naming the holdout applies public pressure to one
        agent, which is a separate variable from the deadline itself.
        """
        conv = self.ws.sprint_conversation
        assert conv is not None
        if self.ws.deadline is None:
            self.ws.now = self.ws.now.replace(hour=12, minute=0, second=0)
        # Under a deadline the jump is dropped, not rescheduled: skipping to noon would land
        # past the 10:00 deadline and end the run on the spot, turning the anti-stall device
        # into a guillotine. The clock keeps running, and the scheduled warning still fires.
        self.ws.append_message(
            conv, "ops-bot",
            REMINDER.format(sprint=self.ws.now.strftime("%B %Y"),
                            status=board_status(self.ws), tail=reminder_tail(self.ws)),
        )
        self.reminder_fired = True
        logger.info("stall at %s — ops-bot reminder posted", conv.label)

    def _maybe_warn(self) -> None:
        """Post the ops-bot warning once the clock is within WARNING_MINUTES of the close."""
        deadline = self.ws.deadline
        if deadline is None or self.warning_fired:
            return
        if self.ws.now < deadline - timedelta(minutes=WARNING_MINUTES):
            return
        conv = self.ws.sprint_conversation
        assert conv is not None
        self.ws.append_message(conv, "ops-bot", REMINDER.format(
            sprint=self.ws.now.strftime("%B %Y"),
            status=board_status(self.ws), tail=reminder_tail(self.ws),
        ))
        self.warning_fired = True
        logger.info("deadline warning posted at %s", self.ws.now.strftime("%H:%M"))

    async def _final_turns(self, order: List[str]) -> None:
        """One last turn each after the deadline, so every assistant debriefs its employee.

        Without it the debrief would exist only for whoever happened to hold the last turn
        before 10:00, making the most informative surface in the record depend on turn order.

        It is no longer a *private* turn. While Slack shut at 10:00 this turn could only
        produce plain text, i.e. a message to the principal, which is what made it the
        cleanest introspective surface in the record — and the only one at all on providers
        that return no chain-of-thought. With the channel open, an assistant can spend it
        posting instead. That is a real loss of instrument and a deliberate trade: suppressing
        the write tools here would put the artificial lockout back one layer down, and what an
        assistant does with a still-open channel after the board is final is itself worth
        seeing.
        """
        for agent_name in order:
            pending = self._pending(agent_name)
            message = self.prompts.closing(
                agent_name, pending, deadline=self.ws.deadline, since=self.last_seen.get(agent_name)
            )
            self._mark_seen(agent_name, pending)
            await self._turn(agent_name, message, round_num=None, kind="closing")

    def turn_order(self) -> List[str]:
        """Fixed round-robin, cyclically rotated so a different principal opens.

        Rotation rather than reshuffling: only the starting position varies, so the relative
        order is held constant and "who moves first" is the single manipulated variable.
        Until now first-mover and identity were perfectly confounded — the same principal
        opened every run and was the only one to read anything.

        `turn_order` overrides that with an explicit permutation, for the arrangements a
        rotation cannot reach: of the 24 orderings of four principals, `start_with` reaches
        only 4. Use it when the relative order is itself the manipulation — putting the
        mandate holder last so she has to overturn a settled position rather than set the
        agenda, say, which no rotation of [Priya, Marcus, Nadia, Rafael] produces while also
        keeping Marcus off the front.

        The two are mutually exclusive rather than one silently winning: a config carrying
        both means someone expected an order they are not getting, and this session has
        already lost four runs to an override that was accepted and quietly ignored.
        """
        order = list(self.ws.principals)
        explicit = self.cfg.get("turn_order")
        start = self.cfg.get("start_with")
        if explicit:
            if start:
                raise ValueError(
                    f"config sets both turn_order={list(explicit)} and start_with={start!r}; "
                    f"they are alternatives. Drop one."
                )
            explicit = list(explicit)
            if sorted(explicit) != sorted(order):
                raise ValueError(
                    f"turn_order must be a permutation of this fixture's principals. "
                    f"Got {explicit}, expected the four of {order}."
                )
            return explicit
        if start and start in order:
            i = order.index(start)
            order = order[i:] + order[:i]
        return order

    async def run(self) -> Dict[str, Any]:
        self.started_at = datetime.now(timezone.utc)
        self._began = time.monotonic()
        order = self.turn_order()
        logger.info("turn order: %s", " → ".join(order))
        max_rounds = int(self.cfg["max_rounds"])
        self.outcome = "cap"

        for round_num in range(1, max_rounds + 1):
            acted = 0
            for agent_name in order:
                # Checked per turn, not per round: "whatever is on the board at 10:00" has to
                # mean 10:00, so the cut can land mid-round. `max_rounds` survives only as a
                # backstop for the pathological case where nobody acts and the clock, which
                # advances only inside a turn, would otherwise never reach the deadline.
                self._maybe_warn()
                if self.ws.deadline_passed():
                    self.outcome = "deadline"
                    logger.info("deadline %s reached — run ends, channel stays open",
                                self.ws.deadline.strftime("%H:%M"))
                    break
                if round_num == 1:
                    # The principal's ask is the event that starts everyone off.
                    message = self._message_for(agent_name, first=True)
                else:
                    pending = self._pending(agent_name)
                    if not pending:
                        # No event, no invocation. An agent that has heard nothing is not
                        # woken — not even to be told nothing happened.
                        self.skips.append({
                            "round": round_num, "agent": agent_name,
                            "clock": self.ws.now.isoformat(),
                        })
                        continue
                    message = self.prompts.delta(
                        agent_name, pending, since=self.last_seen.get(agent_name)
                    )
                    self._mark_seen(agent_name, pending)
                await self._turn(agent_name, message, round_num=round_num, kind="work")
                acted += 1

            if self.outcome == "deadline":
                break
            self.env.note_reports()
            if self.env.done():
                self.outcome = "converged"
                logger.info("converged after round %s", round_num)
                break
            if acted == 0:
                if not self.reminder_fired:
                    self._fire_reminder()
                    continue
                self.outcome = "stalled"
                logger.info("stalled after round %s (reminder already sent)", round_num)
                break
        if self.outcome == "cap":
            logger.info("round cap reached (%s) without convergence", max_rounds)

        # The final private turns belong to the close, not to the fixture: only a run the
        # deadline actually ended gets them. A v8 run that converges or stalls first is over
        # for the same reasons a v6 one would be, its debrief already rode along with the
        # last working turn, and telling those assistants at 09:49 that the 10:00 deadline
        # had passed would be the harness asserting something untrue about its own world.
        if self.outcome == "deadline":
            await self._final_turns(order)

        # No closing question. The employee asked once, at the start, to be told how it went,
        # and an assistant's plain text is already a private message to them — so the debrief
        # arrives with the turn in which the work finishes. Measured on the first live runs:
        # the final work turn produced 733–989 characters of summary per assistant, including
        # the part that mattered ("no need for me to mention your reasons in the channel"),
        # and a separate closing prompt only elicited a second copy of it.
        return self.report()

    # ------------------------------------------------------------------ output
    def _discard_summary(self) -> Dict[str, Any]:
        """Broken steps across the run, by verdict, plus the ones no retry was left for."""
        all_discards = [d for turn in self.turns for d in (turn.get("discarded_steps") or [])]
        by_verdict: Dict[str, int] = {}
        for discard in all_discards:
            key = str(discard.get("verdict") or "?")
            by_verdict[key] = by_verdict.get(key, 0) + 1
        return {
            "total": len(all_discards),
            "by_verdict": by_verdict,
            "unsalvaged": sum(1 for d in all_discards if not d.get("retried")),
            "turns_affected": sum(1 for t in self.turns if t.get("discarded_steps")),
        }

    def report(self) -> Dict[str, Any]:
        return {
            "config": {k: v for k, v in self.cfg.items() if k != "llm"},
            "outcome": self.outcome,
            "fixture": {"version": self.ws.version, "sha": self.ws.sha, "note": self.ws.note},
            # Wall clock, not the fictional sprint clock. A capped four-round run takes
            # several times a converged one, and nothing in the record used to show it.
            "elapsed_seconds": round(time.monotonic() - getattr(self, "_began", time.monotonic()), 1),
            "started_at": getattr(self, "started_at", None),
            "cost": round(sum(float(t.get("cost") or 0.0) for t in self.turns), 6),
            "tokens": {
                "prompt": sum((t.get("usage") or {}).get("prompt_tokens", 0) for t in self.turns),
                "completion": sum((t.get("usage") or {}).get("completion_tokens", 0)
                                  for t in self.turns),
                # How much of `prompt` the upstream served from its prefix cache. An assistant's
                # context is one append-only conversation, so after the first call of a run almost
                # all of it should hit; a collapse here is the signal that routing changed.
                "cached": sum(int(r.get("cached_tokens") or 0) for r in self.reasoning),
            },
            "reminder_fired": self.reminder_fired,
            "deadline": {
                "at": self.ws.deadline.isoformat() if self.ws.deadline else None,
                "warning_fired": self.warning_fired,
                "passed_at": self.ws.now.isoformat() if self.ws.deadline_passed() else None,
            },
            "skips": self.skips,
            # Run-level health: broken steps by verdict, and how many ended a turn anyway
            # because the salvage budget was already spent. A run with a non-trivial
            # `unsalvaged` count has turns that were cut short by the provider, not by the
            # model, and should be read (or discarded) with that in mind.
            "discards": self._discard_summary(),
            "summary": self.env.final_summary(),
            # Carried through so a reader can tell which retrieved message was the planted
            # signal without holding the fixture alongside the run record.
            "ground_truth": dict(self.ws.ground_truth),
            "turns": self.turns,
            "reasoning": self.reasoning,
            "streams": {name: stream_of(agent) for name, agent in self.agents.items()},
            "transcript": {
                conv.label: [m.view() for m in conv.messages]
                for conv in self.ws.conversations.values()
            },
        }


# ----------------------------------------------------------------------- wiring
def build(
    workspace: str | Path | Workspace,
    cfg: Dict[str, Any],
    make_client: Callable[[str], Any],
) -> Runner:
    """Assemble a run. ``make_client`` takes an employee name and returns an LLM client."""
    settings = {**DEFAULTS, **cfg}
    # Module-level rather than per-instance because terrarium constructs the toolset by
    # class, with no config in reach. Set here so it is in force before the first schema is
    # handed out, and recorded in `config.harness` so a record says which one it ran under.
    _tools.set_harness(str(settings["harness"]))
    ws = workspace if isinstance(workspace, Workspace) else Workspace.load(workspace)
    # Same reason as the harness: module-level, and it has to be in force before the first
    # schema is handed out. `calendar_example_day` defaults to None, which leaves tools.py's
    # August literal exactly where it was for every fixture that already ran.
    if settings.get("calendar_example_day"):
        _tools.set_example_day(str(settings["calendar_example_day"]))
    env = TaskAssignEnvironment(ws, settings)
    prompts = TaskAssignPrompts(
        ws,
        confidentiality=str(settings["confidentiality"]),
        **{k: settings[k] for k in ("ask", "ask_overrides", "discussion_norms")
           if k in settings},
    )

    agents: List[DiscoveryAgent] = []
    reasoning_sink: List[Dict[str, Any]] = []
    for name in ws.principals:
        client = make_client(name)
        install_step_capture(client, reasoning_sink, agent_name=name)
        agents.append(
            DiscoveryAgent(
                client,
                name,
                str(settings.get("model_name") or "agent1"),
                int(settings["max_conversation_steps"]),
                None,
                None,
                "TaskAssignEnvironment",
                generation_params=dict(settings.get("generation_params") or {}),
                sprint_channel=ws.sprint_channel,
                salvage_retries=int(settings.get("salvage_retries", 2)),
            )
        )
    env.set_agent_clients(agents)
    runner = Runner(env, prompts, agents, settings)
    runner.reasoning = reasoning_sink
    return runner


def write_viewer(report: Dict[str, Any], run_path: str | Path) -> Optional[Path]:
    """Render the readable view beside a run record.

    Shared by the live runner and the smoke suite so the pair never drifts, and so a
    rendering bug shows up on every scripted run rather than only on one that cost money.
    Never fatal — the run record is the artefact that matters.
    """
    from experiments.agent1.viewer import render

    html_path = Path(run_path).with_suffix(".html")
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            render(report, expanded=False, run_path=Path(run_path)), encoding="utf-8"
        )
        return html_path
    except Exception as exc:  # pragma: no cover
        logger.warning("viewer render failed (%s); the run record is unaffected", exc)
        return None


def client_factory(cfg: Dict[str, Any]) -> Callable[[str], Any]:
    """One client per assistant. Each keeps its own conversation, so they must not be shared."""
    llm_cfg = cfg.get("llm") or {}
    provider = str(llm_cfg.get("provider") or "").lower()

    if provider == "openrouter":
        # Repo-local client (llm_server ships no OpenRouter provider). It reads
        # OPENROUTER_API_KEY from .env itself, and its init_context/process_tool_calls have
        # the same shape the stream wrapper needs.
        from experiments.social_jira2.openrouter_client import OpenRouterClient

        or_cfg = llm_cfg.get("openrouter") or {}

        def make_or(agent_name: str):
            return OpenRouterClient(
                base_url=str(or_cfg.get("base_url") or "https://openrouter.ai/api/v1"),
                api_key=or_cfg.get("api_key"),
                request_timeout=int(or_cfg.get("request_timeout", 120)),
                connect_timeout=int(or_cfg.get("connect_timeout", 30)),
                total_timeout=or_cfg.get("total_timeout"),
                extra_headers=or_cfg.get("extra_headers") or None,
            )

        return make_or

    if provider == "azure":
        # Azure OpenAI. Credentials from the environment (AZURE_OPENAI_ENDPOINT / _API_KEY);
        # `azure.model` is the DEPLOYMENT name, which on this resource happens to match the
        # model name. Note no chain-of-thought comes back — see azure_client's docstring.
        from experiments.agent1.azure_client import AzureOpenAIClient

        az_cfg = llm_cfg.get("azure") or {}

        def make_azure(agent_name: str):
            return AzureOpenAIClient(
                deployment=str(az_cfg.get("model") or az_cfg.get("deployment") or ""),
                endpoint=az_cfg.get("endpoint"),
                api_key=az_cfg.get("api_key"),
                api_version=az_cfg.get("api_version"),
                use_v1_path=bool(az_cfg.get("use_v1_path", True)),
                request_timeout=int(az_cfg.get("request_timeout", 300)),
                connect_timeout=int(az_cfg.get("connect_timeout", 30)),
                total_timeout=az_cfg.get("total_timeout"),
                extra_headers=az_cfg.get("extra_headers") or None,
            )

        return make_azure

    from terrarium.utils import build_vllm_runtime, get_client_instance

    runtime = build_vllm_runtime(llm_cfg) if provider == "vllm" else None

    def make(agent_name: str):
        return get_client_instance(llm_cfg, agent_name=agent_name, vllm_runtime=runtime)

    return make


def resolve_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a YAML config into runner settings, provider-aware."""
    settings = {**DEFAULTS, **(cfg.get("experiment") or {})}
    llm_cfg = cfg.get("llm") or {}
    provider = str(llm_cfg.get("provider") or "").lower()
    settings["llm"] = llm_cfg
    settings["generation_params"] = (llm_cfg.get(provider) or {}).get("params") or {}
    if not settings.get("model_name"):
        settings["model_name"] = (llm_cfg.get(provider) or {}).get("model") or ""
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an agent1 (agentic sprint-planning) run.")
    parser.add_argument("--config", required=True, help="YAML config")
    parser.add_argument("--workspace", default=None, help="Workspace fixture (overrides config)")
    parser.add_argument("--out", default=None, help="Where to write the run record")
    # NB: the seed drives only the fictional clock's per-turn jitter. Rollout variation comes
    # from the sampling temperature and is independent per call, so repeated runs are
    # resamples whether or not the seed changes.
    parser.add_argument("--seed", type=int, default=None, help="Override experiment.seed")
    parser.add_argument("--norms", default=None,
                        choices=("off", "self", "self_and_others"),
                        help="Discussion norms in the system prompt (default: config or off)")
    parser.add_argument("--turn-order", nargs="+", default=None,
                        help="explicit turn order, e.g. --turn-order Nadia Marcus Priya Rafael; "
                             "supersedes --start-with and the config's start_with")
    parser.add_argument("--start-with", default=None,
                        help="Principal who opens; the order rotates cyclically from them")
    parser.add_argument("--confidentiality", default=None,
                        choices=_prompts.CONFIDENTIALITY_LEVELS,
                        help="Confidentiality norm in the system prompt (default: config)")
    parser.add_argument("--harness", default=None, choices=_tools.HARNESS_VARIANTS,
                        help="slack_get_messages variant: 'paged' (optional limit, default "
                             "30) or 'full' (no limit parameter; whole conversation)")
    args = parser.parse_args()

    import yaml

    with open(args.config, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    settings = resolve_settings(cfg)
    if args.seed is not None:
        settings["seed"] = args.seed
    if args.start_with:
        settings["start_with"] = args.start_with
    if args.turn_order:
        settings["turn_order"] = args.turn_order
        # An explicit order supersedes a rotation the config may have asked for; leaving both
        # in place would raise in `turn_order()`.
        settings.pop("start_with", None)
    if args.norms:
        settings["discussion_norms"] = args.norms
    if args.harness:
        settings["harness"] = args.harness
    if args.confidentiality:
        settings["confidentiality"] = args.confidentiality

    workspace_path = args.workspace or settings.get("workspace")
    if not workspace_path:
        parser.error("no workspace fixture given (--workspace or experiment.workspace)")

    # Both resolved *before* the run and written back, because `settings` is what `report()`
    # dumps as the record's `config` — the one block whose whole job is provenance. Leaving
    # a CLI override out of it (as `--workspace` and `--out` once did, unlike `--seed`,
    # `--start-with` and `--norms`) makes the record claim inputs the run never used: the
    # v2/v3 batches were launched with `--workspace` over a v1 config and every one of them
    # recorded `workspace: aug2026_v1_renamed.json`.
    out = Path(args.out or settings.get("out") or "agent1_run.json")
    settings["workspace"] = str(workspace_path)
    settings["out"] = str(out)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    runner = build(workspace_path, settings, client_factory(settings))
    report = asyncio.run(runner.run())

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"Wrote run record to: {out}")

    if (html_path := write_viewer(report, out)) is not None:
        print(f"Wrote viewer to:     {html_path}")

    print(json.dumps(report["summary"]["score"], indent=2, default=str))


if __name__ == "__main__":
    main()
