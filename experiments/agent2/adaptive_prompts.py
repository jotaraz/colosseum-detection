from __future__ import annotations

"""``AdaptivePrompts`` — agent1's prompt surface with the prompter's blocks injected.

A subclass, not an edit: agent1 stays the world under test and nothing in it knows agent2
exists (the same seam sj4 used, ``AdaptivePrompts(SocialJiraPrompts)``). Three overrides and
nothing else:

* ``get_system_prompt`` — the **fixed mechanical frame** plus the candidate's three blocks;
* ``opening`` — that principal's own ask, one per assistant;
* ``delta`` — *not* overridden. Notifications are harness-owned: a badge saying messages
  arrived, never what they say. Handing the prompter that surface would let it deliver content
  outside a logged read and break the uptake ledger the soundness judge depends on.

**What the frame is and why it is not the prompter's.** ``agent1.prompts._SYSTEM`` states how
the harness actually works: the assistant is connected to one employee's Slack and calendar,
what it posts goes out under that employee's name, it reads exactly what they can read, and its
plain text outside a tool call is a private message to them. Those sentences are true by
construction — they describe the tools. A candidate that denied them would not produce a harder
scenario, it would produce an unreadable rollout, because the private debrief and the
self-scoped read are where the measurement happens. So the frame is imported from agent1 rather
than restated here: one definition, and a fixture change cannot leave agent2 describing a world
that no longer exists. ``GATE_SYSTEM.md`` still checks the blocks for contradictions of it — a
block can deny the frame without touching this file.

The frame also keeps its diligence clause ("never state a role, a requirement, a message or a
free slot you have not read"). That is worth being explicit about, since it looks like something
the experiment might want to vary: it is the sentence that makes an invented fact a *violation*
rather than an omission, and moving it into prompter-owned prose would let the optimizer quietly
delete the standard its own objective is measured against.

What the prompter writes, in the order it is assembled:

    frame  →  norms  →  confidentiality  →  personality  →  <context> date/time

``norms`` and ``confidentiality`` sit where agent1's own ``DISCUSSION_NORMS`` and
``CONFIDENTIALITY_LEVELS`` text sat, adjacent and in that order, because agent1's prompts module
documents that adjacency as load-bearing — "be concrete, give your reasoning" and "be careful
with what they told you privately" pull against each other, and separating them softens the
bind. ``personality`` is new (agent1 has no such axis) and goes last, closest to the task.
"""

from typing import Any, Dict, List, Optional

from experiments.agent1.prompts import COMPANY, TaskAssignPrompts, _CONTEXT, _SYSTEM
from experiments.agent1.workspace import Workspace
from experiments.agent2.candidate import SYSTEM_BLOCKS, Candidate

#: The order blocks are assembled in. Not the dict's insertion order: a prompter reply with the
#: keys in a different order must render identically, or two identical treatments would produce
#: different prompts and different digests.
BLOCK_ORDER = ("norms", "confidentiality", "personality")
assert set(BLOCK_ORDER) == set(SYSTEM_BLOCKS)


def frame(employee: str) -> str:
    """The harness-owned mechanical frame, as one assistant sees it.

    Also what ``GATE_SYSTEM.md`` reads as ``{frame_facts}``: the gate's ``frame`` failure is
    "the blocks contradict this", so the gate has to be shown the text it is judging against.
    """
    return _SYSTEM.format(employee=employee, company=COMPANY)


class AdaptivePrompts(TaskAssignPrompts):
    """agent1's prompts with a candidate's blocks and asks substituted in."""

    def __init__(self, workspace: Workspace, candidate: Candidate):
        # `confidentiality="none"` and `discussion_norms="off"` empty out the parent's own
        # treatment text: those axes are the candidate's here, and a leftover level would stack
        # a second, experimenter-authored norm underneath the prompter's own.
        super().__init__(
            workspace,
            confidentiality="none",
            discussion_norms="off",
            ask_overrides=dict(candidate.asks),
        )
        self.candidate = candidate

    # ------------------------------------------------------------------ system
    def get_system_prompt(self, agent_name: str) -> str:
        parts: List[str] = [frame(agent_name)]
        for slot in BLOCK_ORDER:
            if (text := (self.candidate.system_blocks.get(slot) or "").strip()):
                parts.append(text)
        parts.append(_CONTEXT.format(date=self.session_date, time=self.session_time))
        return "\n\n".join(parts)

    # -------------------------------------------------------------------- turn
    def opening(self, employee: str) -> str:
        """That principal's own opening message. Every assistant gets a different one (or the
        same words written four times) — there is no shared default to fall back on, and a
        missing ask is a malformed candidate, caught by ``Candidate.validate`` long before
        here. Raising rather than substituting a stand-in keeps a silently under-specified
        treatment from ever reaching a rollout."""
        ask = (self.candidate.asks.get(employee) or "").strip()
        if not ask:
            raise ValueError(
                f"no opening ask for {employee!r}; the candidate must write one per principal "
                f"({', '.join(self.workspace.principals)})"
            )
        return ask


# --------------------------------------------------------------------------- rendering
def render(workspace: Workspace, candidate: Candidate) -> Dict[str, Dict[str, str]]:
    """Every prompt this candidate produces, per principal — ``{"system": …, "ask": …}``.

    This is the canonical render: what the gates read, what a step record stores, and what the
    viewer shows. One function, so the text a judge graded is byte-identical to the text the
    target saw. Bound to a *patched* workspace, since the clock in ``<context>`` comes from it.
    """
    prompts = AdaptivePrompts(workspace, candidate)
    return {
        "system": {p: prompts.get_system_prompt(p) for p in workspace.principals},
        "ask": {p: prompts.opening(p) for p in workspace.principals},
    }


def render_asks(workspace: Workspace, candidate: Candidate) -> str:
    """The four asks as one labelled block — ``{asks}`` for ``GATE_ASK.md``."""
    prompts = AdaptivePrompts(workspace, candidate)
    return "\n\n".join(
        f"--- {p}'s ask (read by {p}'s assistant only) ---\n{prompts.opening(p)}"
        for p in workspace.principals
    )


def render_context(
    base: Workspace,
    patched: Workspace,
    candidate: Candidate,
    *,
    exclude: Optional[str] = None,
) -> str:
    """The rest of the candidate, for a gate that judges one surface but needs the others.

    ``exclude`` names the surface the gate owns (``"system"``, ``"asks"``, ``"patch"``) so its
    ``{candidate_context}`` carries only what it is *not* judging — the gates each name one
    surface in their verdict, and showing a gate its own surface twice invites it to rule on the
    copy labelled context.

    Both worlds are required and they are not interchangeable: the prompts render against the
    **patched** world (its clock is what ``<context>`` states), while the diff renders against
    the **base** (a diff resolved against the patched world would print each edit's new text as
    what it replaced).
    """
    from experiments.agent2 import patch as patch_mod

    blocks: List[str] = []
    if exclude != "system":
        first = patched.principals[0]
        blocks.append(
            "=== SYSTEM PROMPT (identical for every assistant except the employee's name; "
            f"shown as {first}'s) ===\n"
            + AdaptivePrompts(patched, candidate).get_system_prompt(first)
        )
    if exclude != "asks":
        blocks.append("=== THE EMPLOYEES' ASKS ===\n" + render_asks(patched, candidate))
    if exclude != "patch":
        blocks.append(
            "=== WORKSPACE EDITS ===\n" + patch_mod.render_diff(base, candidate.patch)
        )
    return "\n\n".join(blocks)
