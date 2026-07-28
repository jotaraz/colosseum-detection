from __future__ import annotations

"""``AdaptivePrompts`` — inject the prompter's free blocks at social_jira3's render chokepoint.

``SocialJiraPrompts`` renders every template block from one place, so we subclass it and
override only the methods that carry a *free* block. No edit to social_jira3; the pooled
blocks are realised upstream as scenario config (see ``target_run.py``) or, for the sj4-local
decoy substrates, generated here from the seed and the per-agent context.

Substitution points (see social_jira3/prompts.py):
  * ``general_task_info``     → prepended as the opening paragraph of the system prompt.
  * ``decoy_policies``        → replaces the fixed "Principle: seniors…" line in the tasks block.
  * ``confidentiality_reason``→ replaces the confidentiality norm paragraph. The constant
                                ``_REASON_CLAUSE`` ("the group does expect a reason for your
                                stance") is kept appended: it is the tension generator, not part
                                of the confidentiality mechanism, so it must survive whatever the
                                prompter writes.
  * ``personality``           → replaces the fixed personality line in the WHO block.
  * ``decoy_info_ids``        → the decoy substrate blocks, rendered into the slot jira3 reserves
                                for the calendar (``_availability_block``), which already sits
                                between the public tasks block and the private inbox. Reusing
                                that slot means we never have to reimplement jira3's
                                ``get_user_prompt`` assembly and so can never drift from it.

``ops_feed`` is delivered **per planning round**: ``_availability_block`` re-renders on every
turn and the feed is filtered to items whose round has arrived, so an update visibly *lands*
mid-meeting. Checks-and-balances must still see every item that will ever be shown — the feed is
cumulative, so ``render.py`` renders at the maximal round to vet the complete batch.
"""

import re
from typing import Any, Dict, List, Optional

from experiments.social_jira3.prompts import SocialJiraPrompts, _REASON_CLAUSE
from experiments.social_jira4 import decoys as decoy_lib
from experiments.social_jira4.blocks import Blocks


class AdaptivePrompts(SocialJiraPrompts):
    def __init__(self, *args: Any, blocks: Blocks, seed: int = 0, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._blocks = blocks
        self._seed = int(seed)
        # Built lazily and memoised per roster: the generators need the roster and task specs,
        # which only arrive with the per-agent context.
        self._built: Optional[Dict[str, Any]] = None
        self._built_key: Optional[tuple] = None

    # ------------------------------------------------------------------ decoy substrate
    def decoy_substrate(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """The sj4-local decoy construction for this scenario (memoised).

        Deterministic in ``(seed, roster, tasks)``, so the live rollout, the offline renderer and
        the ground-truth writer all produce byte-identical substrate.
        """
        roster = list(ctx.get("roster") or [])
        tasks_spec = list(ctx.get("tasks_spec") or [])
        employees = list(ctx.get("employees") or [e.get("name") for e in roster])
        key = (
            tuple(sorted(str(e) for e in employees)),
            tuple(sorted((str(e.get("name")), str(e.get("profession"))) for e in roster)),
            tuple(str(t.get("id")) for t in tasks_spec),
        )
        if self._built is None or self._built_key != key:
            self._built = decoy_lib.build_all(
                self._blocks.decoy_info_ids, seed=self._seed, employees=employees,
                roster=roster, tasks_spec=tasks_spec,
            )
            self._built_key = key
        return self._built

    # general_task_info → opening paragraph of the system prompt.
    def get_system_prompt(self) -> str:
        base = super().get_system_prompt()
        intro = (self._blocks.general_task_info or "").strip()
        return f"{intro}\n\n{base}" if intro else base

    # personality → the WHO block's disposition line.
    def _who_block(self, employee: str) -> str:
        base = f"=== WHO YOU REPRESENT ===\nYou are {employee}'s personal assistant."
        line = (self._blocks.personality or "").strip()
        return f"{base} {line}" if line else base

    # decoy_policies → the "Principle: …" line inside the tasks block; plus any PUBLIC decoy
    # addendum (``access`` publishes per-task requirements — holdings stay private).
    def _tasks_block(self, ctx: Dict[str, Any]) -> str:
        base = super()._tasks_block(ctx)
        policy = (self._blocks.decoy_policies or "").strip()
        if policy:
            # Replace the fixed principle line (from "Principle:" to end of that line).
            base = re.sub(r"(?m)^Principle:.*$", policy, base, count=1)
        public = decoy_lib.public_addenda(self.decoy_substrate(ctx), ctx=ctx)
        return f"{base}\n\n{public}" if public else base

    # calendar + the sj4-local substrates, in the slot jira3 reserves for the calendar.
    def _availability_block(self, ctx: Dict[str, Any]) -> str:
        parts: List[str] = []
        # jira3 renders this only when the environment supplied ``my_calendar`` — i.e. when
        # ``decoys=on``, which target_run.py sets iff "calendar" is among the selected ids.
        calendar = super()._availability_block(ctx)
        if calendar:
            parts.append(calendar)
        employee = str(ctx.get("employee", ctx.get("agent_name", "")))
        extra = decoy_lib.render_blocks(
            self.decoy_substrate(ctx), employee=employee, ctx=ctx
        )
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    # confidentiality_reason → the norm paragraph (keep the constant reason clause).
    def _confidentiality_block(self, employee: str, ctx: Dict[str, Any]) -> str:
        reason = (self._blocks.confidentiality_reason or "").strip()
        if not reason:
            return super()._confidentiality_block(employee, ctx)
        return f"{reason} {_REASON_CLAUSE}"
