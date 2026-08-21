from __future__ import annotations

"""The environment: a thin holder around the workspace.

Deliberately *not* a subclass of ``SocialJiraEnvironment`` or terrarium's
``AbstractEnvironment``. With phases gone, the blackboard unused and the toolset entirely
new, inheriting would drag in a dozen abstract methods agent1 never calls (network
initialisation, iteration bookkeeping, reward plumbing keyed to a different game). What the
stack actually requires of an environment object is small and explicit:

* ``get_serializable_state()`` — what ``LocalCommunicationProtocol`` hands to the toolset on
  every call (``experiments/common/local_protocol.py:184-188``).
* ``apply_state_updates()`` / ``post_tool_execution_callback()`` — optional hooks the
  protocol calls when a tool result carries ``state_updates``. Ours never do: the toolset
  mutates the live :class:`Workspace` directly, which is why turn boundaries stay entirely
  under :class:`DiscoveryAgent`'s control rather than being triggered by a dict key.

Everything else here is for the runner: whose turn it is, whether the run is over, and what
the outcome was.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments.agent1 import tools as _tools  # noqa: F401  (registers TaskAssignTools)
from experiments.agent1.workspace import Workspace

logger = logging.getLogger(__name__)


class TaskAssignEnvironment:
    """Holds the workspace and answers the runner's questions about run state."""

    # Resolved by name through the registry in `tools.register()`.
    tools_environment_name = None

    def __init__(self, workspace: Workspace, config: Optional[Dict[str, Any]] = None):
        self.workspace = workspace
        self.config = config or {}
        self.agent_names: List[str] = list(workspace.principals)
        self.agents: List[Any] = []
        #: Filled by the runner: employee -> ts of the DM that discharged the report duty.
        self.reports: Dict[str, str] = {}
        #: Anything at or before this is pre-existing history, not something the run produced.
        #: Without it the reporter's *old* messages to the manager count as the write-up — the
        #: fixture's July thread between Alice and Kira made the duty read as discharged before
        #: the run started, so the stop rule fired the moment the board filled.
        self.run_start_ts: float = workspace.now.timestamp()

    @classmethod
    def from_fixture(cls, path: str | Path, config: Optional[Dict[str, Any]] = None):
        return cls(Workspace.load(path), config)

    # --------------------------------------------------------------- tool state
    def get_serializable_state(self) -> Dict[str, Any]:
        """The whole world, including every employee's private data.

        Per-agent filtering is ``TaskAssignTools._visible``'s job and nowhere else — see the
        note there. Nothing in this method may pre-filter, because one state serves all four
        agents on every call.
        """
        return self.workspace.to_state()

    def apply_state_updates(self, state_updates: Dict[str, Any]) -> None:  # pragma: no cover
        """Unused: the toolset mutates the workspace in place. Kept for protocol contract."""
        return None

    # ------------------------------------------------------------------ run state
    def get_agent_names(self) -> List[str]:
        return list(self.agent_names)

    def set_agent_clients(self, agents: List[Any]) -> None:
        self.agents = list(agents)

    @property
    def reporter(self) -> Optional[str]:
        return self.workspace.reporter

    @property
    def report_to(self) -> Optional[str]:
        return self.workspace.report_to

    def report_outstanding(self) -> bool:
        """True while the nominated reporter still owes the manager a write-up."""
        reporter, manager = self.reporter, self.report_to
        if not reporter or not manager:
            return False
        return reporter not in self.reports

    def note_reports(self) -> None:
        """Scan the workspace for the reporter's DM to the manager (idempotent)."""
        reporter, manager = self.reporter, self.report_to
        if not reporter or not manager or reporter in self.reports:
            return
        for conv in self.workspace.conversations.values():
            if conv.type != "dm" or set(conv.members) != {reporter, manager}:
                continue
            for msg in conv.messages:
                if msg.user == reporter and float(msg.ts) >= self.run_start_ts:
                    self.reports[reporter] = msg.ts
                    logger.info("report discharged: %s -> %s (%s)", reporter, manager, msg.ts)
                    return

    def done(self) -> bool:
        """Over when the allocation actually works and the reporting duty is discharged.

        Validity, not mere completeness: a board where everyone has decided but the decisions
        do not compose is a failure, and stopping there would record it as a success. An
        allocation that stays broken because nobody tries to repair it terminates through the
        stall path instead, which is the honest label for it.
        """
        self.note_reports()
        ws = self.workspace
        return ws.board_complete() and ws.allocation_valid() and not self.report_outstanding()

    # -------------------------------------------------------------------- outcome
    def final_summary(self) -> Dict[str, Any]:
        self.note_reports()
        ws = self.workspace
        return {
            "score": ws.score(),
            "reports": dict(self.reports),
            "report_outstanding": self.report_outstanding(),
            "clock": ws.now.isoformat(),
            "seen": {agent: list(ids) for agent, ids in ws.seen.items()},
            "signal_uptake": self.signal_uptake(),
        }

    def signal_uptake(self) -> Dict[str, Any]:
        """Which agents were actually handed a message the fixture flags as signal-bearing."""
        flagged = set(self.workspace.ground_truth.get("signal_messages") or [])
        if not flagged:
            return {}
        return {
            agent: sorted(flagged.intersection(seen))
            for agent, seen in self.workspace.seen.items()
        }
