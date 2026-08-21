from __future__ import annotations

"""``Candidate`` — one fully-specified treatment, as the prompter emits it.

sj4's ``Blocks`` filled six slots of a fixed prompt template. agent2's candidate spans three
surfaces, because the prompter here writes not only what the assistants are told but part of the
world they discover::

    system_blocks   {norms, confidentiality, personality}   prose, inserted into a FIXED FRAME
    asks            {Alice: "…", Bob: "…", …}               one opening message per principal
    patch           [ {op: add|edit|delete|pin, …}, … ]      edits to the fixture's messages

Three properties are load-bearing and are enforced here rather than left to a gate:

**The mechanical frame is not the prompter's.** ``prompts._SYSTEM`` states how the harness
actually works — the assistant posts under its employee's name, reads only what that employee
can read, and its plain text is a private message to them. Those sentences stay fixed; the
prompter fills named slots around them. Falsifying the mechanics would not make a harder
scenario, it would make the rollout unreadable (the debrief channel and the privacy chokepoint
are where the measurement happens). ``GATE_SYSTEM.md`` still checks for contradictions in the
prose, because a block can deny the frame without touching it.

**Asks are per assistant.** Each principal's opening message is written separately and is read
by their assistant alone, so the asks may be as asymmetric as the prompter likes — that is how
pressure gets concentrated, and it is why a factual claim in an ask is checked against *that*
principal's calendar rather than against all four at once.

**The candidate is the whole treatment.** Its patch is expressed against the frozen base
fixture, never against the previous step's world (see ``patch.py``), so any two steps of a run
are comparable and ``digest()`` names one reproducible world+prompt pair.

The four blocks sj4 called free are not carried over one-for-one: ``general_task_info`` and
``decoy_policies`` have no analogue, because the task information and the fabrication substrate
are no longer prompt text — they are the workspace, which the patch edits directly.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from experiments.agent1.workspace import Workspace
from experiments.agent2 import patch as patch_mod
from experiments.agent2.patch import DEFAULT_PATCH_BUDGET, PatchOp

#: The prompter-authored slots of the system prompt, each inserted at a fixed point in the
#: frame. Named rather than one blob so a trajectory can be read per slot (sj4's "blocks
#: evolution" view) and so a gate rejection can name the slot it came from.
#:
#: ``confidentiality`` is prompter-owned by decision: there is no experimenter-pinned level
#: ladder here (agent1's ``CONFIDENTIALITY_LEVELS``) and no ``--decoys``-style pin, so its effect
#: is read from the trajectory rather than as a treatment arm.
SYSTEM_BLOCKS = ("norms", "confidentiality", "personality")

#: Accepted aliases for the block names, since a prompter reaching for the obvious synonym is
#: not making a mistake worth a repair attempt.
_BLOCK_ALIASES: Dict[str, str] = {
    "norms": "norms",
    "discussion_norms": "norms",
    "conduct": "norms",
    "confidentiality": "confidentiality",
    "confidentiality_reason": "confidentiality",
    "privacy": "confidentiality",
    "personality": "personality",
    "disposition": "personality",
    "style": "personality",
}


@dataclass
class Candidate:
    """One prompter proposal. Structured, not free-form text: the prompter fills slots and
    edits messages, and can never restructure the prompt or the world."""

    system_blocks: Dict[str, str]
    asks: Dict[str, str]
    patch: List[PatchOp] = field(default_factory=list)
    #: The prompter's own account of what this candidate is trying to do. Recorded on the step
    #: and fed back in the OPRO trajectory; never shown to a target.
    rationale: str = ""

    # ------------------------------------------------------------------ serialization
    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_blocks": dict(self.system_blocks),
            "asks": dict(self.asks),
            "patch": patch_mod.patch_to_list(self.patch),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Candidate":
        if not isinstance(d, dict):
            raise ValueError(f"candidate must be an object, got {type(d).__name__}")

        raw_blocks = d.get("system_blocks")
        if raw_blocks is None:
            # A prompter that emitted the three slots at the top level rather than nested is
            # well-formed enough to run; the nesting is our schema's convenience, not its claim.
            raw_blocks = {k: d[k] for k in d if str(k).lower() in _BLOCK_ALIASES}
        if not isinstance(raw_blocks, dict):
            raise ValueError(f"system_blocks must be an object, got {type(raw_blocks).__name__}")
        blocks: Dict[str, str] = {}
        for key, value in raw_blocks.items():
            slot = _BLOCK_ALIASES.get(str(key).strip().lower())
            if slot is None:
                raise ValueError(
                    f"unknown system block {key!r}; expected {list(SYSTEM_BLOCKS)}"
                )
            blocks[slot] = str(value or "")

        asks = d.get("asks")
        if not isinstance(asks, dict):
            raise ValueError("asks must be an object mapping each principal to their message")

        return cls(
            system_blocks=blocks,
            asks={str(k): str(v or "") for k, v in asks.items()},
            patch=patch_mod.parse_patch(d.get("patch")),
            rationale=str(d.get("rationale") or ""),
        )

    def digest(self) -> str:
        """Content hash of the treatment — the run-id component that names this exact
        prompt+world pair. Excludes ``rationale``: two candidates that differ only in how the
        prompter described them are the same treatment."""
        payload = {k: v for k, v in self.to_dict().items() if k != "rationale"}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]

    def run_id(self, base: Workspace, seed: int) -> str:
        """Rollout dir name: which world, which treatment, which seed."""
        return f"{base.version}__{self.digest()}__seed{seed}"

    # ---------------------------------------------------------------------- validation
    def validate(
        self,
        base: Workspace,
        *,
        patch_budget: int = DEFAULT_PATCH_BUDGET,
        require_blocks: Sequence[str] = SYSTEM_BLOCKS,
    ) -> List[str]:
        """Return human-readable problems ([] == runnable), checked against the base workspace.

        Structural only — is this a treatment the harness can render and run? Whether it is
        *admissible* (instructs, supplies or templates deception) and whether the scenario
        survives it are the four gates' questions. This pass is what stops a malformed proposal
        from spending a gate call, exactly as ``social_jira4.blocks.validate`` did.
        """
        problems: List[str] = []

        for slot in require_blocks:
            value = self.system_blocks.get(slot)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"system block {slot!r} is empty")
        for slot in self.system_blocks:
            if slot not in SYSTEM_BLOCKS:
                problems.append(f"unknown system block {slot!r}; expected {list(SYSTEM_BLOCKS)}")

        expected = list(base.principals)
        missing = [p for p in expected if not (self.asks.get(p) or "").strip()]
        if missing:
            problems.append(
                f"no opening ask for {missing}; every principal's assistant needs one "
                f"(the four principals are {expected})"
            )
        unknown = [p for p in self.asks if p not in expected]
        if unknown:
            problems.append(
                f"asks written for {unknown}, who have no assistant in this world; "
                f"the principals are {expected}"
            )

        problems.extend(patch_mod.validate(base, self.patch, budget=patch_budget))
        return problems

    # -------------------------------------------------------------------------- applying
    def build_world(
        self, base: Workspace, *, patch_budget: int = DEFAULT_PATCH_BUDGET
    ) -> patch_mod.PatchResult:
        """The world this candidate runs in: the base fixture with its patch applied.

        ``base`` is never mutated, so one loaded fixture serves every step of a run."""
        return patch_mod.apply(base, self.patch, budget=patch_budget)


def parse(text: str) -> Candidate:
    """A prompter reply (JSON, possibly fenced or wrapped in prose) → ``Candidate``."""
    return Candidate.from_dict(extract_json_object(text))


def extract_json_object(text: str) -> Dict[str, Any]:
    """First top-level JSON object in an LLM response.

    Brace-matched rather than outermost-braces (sj4's ``blocks._extract_json_object``): a
    candidate carries a patch whose message texts are prose the prompter wrote, and prose that
    happens to contain a ``}`` — a planted message quoting code or a smiley — truncates a
    naive ``rfind('}')``. String-aware so a brace inside a JSON string never counts.
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]

    start = s.find("{")
    if start == -1:
        raise ValueError("no JSON object found in prompter response")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start : i + 1])
    raise ValueError("unterminated JSON object in prompter response")


def load(path: str) -> Candidate:
    """Read a candidate from a seed file or a stored step record.

    Accepts a bare candidate object, a warm-start seed file (``{"candidate": {…}}``, see
    ``warmstart_sj4_mining.md``), or a loop step record that nests it under ``prompter``."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("candidate", "prompter"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            inner = data[key]
            data = inner.get("candidate", inner)
    return Candidate.from_dict(data)
