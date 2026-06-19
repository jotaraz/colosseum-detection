from __future__ import annotations

"""Anonymization shim for the 'solver' framing (Option B).

Agents in the solver condition must never see Jira-flavoured identifiers: tasks are
shown as ``T1..Tk`` and agents as ``N1..Nm``. The environment, however, only
understands its real identifiers (e.g. ``ISSUE-0001::implement`` and the real agent
names). :class:`IdMapper` holds the two bijections; :class:`AnonymizingLocalProtocol`
applies them at the single chokepoint through which every tool call and every piece
of blackboard text flows (``experiments.common.local_protocol.LocalCommunicationProtocol``):

  * tool-call *arguments* coming FROM the agent  ->  realize (anon -> real)
  * tool *results* and prefetched blackboard text going TO the agent  ->  anonymize (real -> anon)

Agent-authored blackboard messages are already in anon-space, so they are stored
verbatim; the environment's own action traces (logged in real ids) get anonymized on
read. The recorded ``tool_events`` keep the REAL identifiers, so downstream analysis
(metrics, optimal solver) is unaffected.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

from experiments.common.local_protocol import LocalCommunicationProtocol


# Microtask-type -> single-letter code for the readable solver label. Codes must be
# UNIQUE (note triage -> 'g', not 't', to avoid colliding with test).
_TYPE_CODE: Dict[str, str] = {
    "implement": "i",
    "review": "r",
    "test": "t",
    "docs": "d",
    "triage": "g",
}
# Canonical order so the solver task list reads issue-by-issue, type-by-type.
_TYPE_ORDER: Dict[str, int] = {
    "implement": 0, "review": 1, "test": 2, "docs": 3, "triage": 4,
}


def _issue_num(real_id: str) -> int:
    issue = str(real_id).partition("::")[0]
    try:
        return int(issue.split("-")[-1])
    except Exception:
        return 0


def _task_label(real_id: str) -> str:
    """ISSUE-0001::implement -> 'T1.i' (1:1, readable, derived from the real id)."""
    issue, _, typ = str(real_id).partition("::")
    num = _issue_num(issue) if issue else 0
    code = _TYPE_CODE.get(typ, (typ[:2] if typ else "x"))
    return f"T{num}.{code}"


def _task_sort_key(real_id: str):
    issue, _, typ = str(real_id).partition("::")
    return (_issue_num(issue), _TYPE_ORDER.get(typ, 99), typ)


class IdMapper:
    """Bijective real<->anonymous maps for task ids and agent names."""

    def __init__(self, *, agent_names: Sequence[str], task_ids: Sequence[str]):
        # Agents in env order; tasks ordered issue-by-issue, type-by-type (readable).
        self.agent_names: List[str] = [str(a) for a in agent_names]
        self.task_ids: List[str] = sorted((str(t) for t in task_ids), key=_task_sort_key)

        self.agent_to_anon: Dict[str, str] = {
            real: f"N{i + 1}" for i, real in enumerate(self.agent_names)
        }
        self.anon_to_agent: Dict[str, str] = {
            v: k for k, v in self.agent_to_anon.items()
        }
        # Readable 1:1 labels: ISSUE-0001::implement <-> T1.i (no permutation).
        self.task_to_anon: Dict[str, str] = {
            real: _task_label(real) for real in self.task_ids
        }
        labels = list(self.task_to_anon.values())
        if len(set(labels)) != len(labels):
            dupes = sorted({lbl for lbl in labels if labels.count(lbl) > 1})
            raise ValueError(
                f"Non-unique solver task labels {dupes} — check _TYPE_CODE "
                "(every microtask type needs a distinct letter)."
            )
        self.anon_to_task: Dict[str, str] = {
            v: k for k, v in self.task_to_anon.items()
        }

        # real -> anon: match the longest real ids first (they contain '::' and '-').
        real_tokens = sorted(
            [*self.task_to_anon.keys(), *self.agent_to_anon.keys()],
            key=len,
            reverse=True,
        )
        self._real_re = (
            re.compile("|".join(re.escape(t) for t in real_tokens))
            if real_tokens
            else None
        )
        self._real_lookup = {**self.task_to_anon, **self.agent_to_anon}

        # anon -> real: whole tokens T<digits>.<letters> (tasks) or N<digits> (agents),
        # not glued to other alphanumerics.
        self._anon_re = re.compile(
            r"(?<![A-Za-z0-9])(T\d+\.[a-z]+|N\d+)(?![A-Za-z0-9])"
        )
        self._anon_lookup = {**self.anon_to_task, **self.anon_to_agent}

    # ----- string-level ----------------------------------------------------
    def anonymize_text(self, text: str) -> str:
        if not text or self._real_re is None:
            return text
        return self._real_re.sub(lambda m: self._real_lookup[m.group(0)], text)

    def realize_text(self, text: str) -> str:
        if not text:
            return text
        return self._anon_re.sub(
            lambda m: self._anon_lookup.get(m.group(1), m.group(1)), text
        )

    # ----- recursive over json-ish objects ---------------------------------
    def anonymize_obj(self, obj: Any) -> Any:
        return self._walk(obj, self.anonymize_text)

    def realize_obj(self, obj: Any) -> Any:
        return self._walk(obj, self.realize_text)

    def _walk(self, obj: Any, fn) -> Any:
        if isinstance(obj, str):
            return fn(obj)
        if isinstance(obj, dict):
            return {k: self._walk(v, fn) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self._walk(v, fn) for v in obj)
        return obj


class AnonymizingLocalProtocol(LocalCommunicationProtocol):
    """LocalCommunicationProtocol that hides real ids from the agent (solver framing).

    ``id_mapper`` is set by the runner *after* the environment is built (task ids are
    only known once the env has generated the instance). While it is ``None`` the
    protocol behaves exactly like its parent, so construction order is unconstrained.
    """

    def __init__(self, *, config: Dict[str, Any], megaboard=None):
        super().__init__(config=config, megaboard=megaboard)
        self.id_mapper: Optional[IdMapper] = None

    async def environment_handle_tool_call(
        self,
        tool_name: str,
        agent_name: str,
        arguments: Dict[str, Any],
        *,
        phase: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> Dict[str, Any]:
        mapper = self.id_mapper
        # Realize the agent's anon ids (e.g. assign_task task_id="T3") to real ids so
        # the environment can act on them. The recorded ToolEvent therefore stores the
        # REAL ids (ground truth for metrics).
        real_args = mapper.realize_obj(arguments) if mapper else arguments
        response = await super().environment_handle_tool_call(
            tool_name,
            agent_name,
            real_args,
            phase=phase,
            iteration=iteration,
        )
        # Hand the agent back an anonymized view of the result.
        return mapper.anonymize_obj(response) if mapper else response

    async def blackboard_handle_tool_call(
        self,
        tool_name: str,
        agent_name: str,
        arguments: Dict[str, Any],
        *,
        phase: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> Dict[str, Any]:
        # Do NOT realize blackboard arguments: agent-authored message content is already
        # in anon-space and must be stored verbatim so other agents keep seeing anon ids.
        response = await super().blackboard_handle_tool_call(
            tool_name,
            agent_name,
            arguments,
            phase=phase,
            iteration=iteration,
        )
        mapper = self.id_mapper
        return mapper.anonymize_obj(response) if mapper else response

    async def _prefetch_blackboard_events(
        self,
        agent_name: str,
        *,
        phase: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> Dict[str, str]:
        contexts = await super()._prefetch_blackboard_events(
            agent_name, phase=phase, iteration=iteration
        )
        mapper = self.id_mapper
        if mapper is None:
            return contexts
        # Anonymize env-authored action traces (real ids); agent messages are already anon.
        return {k: mapper.anonymize_text(v) for k, v in contexts.items()}
