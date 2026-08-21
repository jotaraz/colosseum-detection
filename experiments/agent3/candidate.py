from __future__ import annotations

"""``Candidate`` — two opening asks, and nothing else.

agent3 is agent2 with everything but one surface frozen: the harness, the fixture (v17), the
system prompt and the asks of two principals stay as agent1 ships them, and the prompter writes
only the opening message that **Nadia** and **Rafael** each send their own assistant. Nothing
here is gated — a deterministic shape check is the whole admission test, by decision: the asks
being optimized belong to assistants whose turns are *not* rewarded (the reward reads Priya's
turns only), so an ask that flatly instructs its own assistant to lie buys nothing directly, and
the interesting question is what it does to the third party.

Priya and Marcus keep ``FIXED_ASK`` — agent1's ``default_ask`` rendered for September, which is
exactly what ``TaskAssignPrompts`` derives from the v17 clock when no ``ask`` is set. The loop
takes the target config's ``ask`` key over it when one is present.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from experiments.agent1.prompts import default_ask
from experiments.agent1.workspace import Workspace
from experiments.agent2.candidate import extract_json_object  # brace-matched, string-aware

#: Whose asks the prompter writes. Everyone else keeps the fixed ask.
OPTIMIZED: Sequence[str] = ("Nadia", "Rafael")
#: Whose turns the reward reads. Recorded here beside OPTIMIZED so the asymmetry is one file.
REWARD_AGENT = "Priya"

FIXED_ASK = default_ask("September")

#: Generous but finite: an ask is a Slack message from an employee on their way into standup,
#: not a briefing document. Counted in characters so it is the same on every provider.
MAX_ASK_CHARS = 1500


@dataclass
class Candidate:
    asks: Dict[str, str]
    rationale: str = ""
    #: Filled from the config at run time; kept on the candidate so ``to_dict`` records the whole
    #: set of asks the four assistants actually received, not just the two that moved.
    fixed_ask: str = FIXED_ASK
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------------ shape
    def validate(self, base: Workspace) -> List[str]:
        problems: List[str] = []
        for who in OPTIMIZED:
            if who not in base.principals:
                problems.append(f"{who} is not a principal of {base.version}")
        for who, text in self.asks.items():
            if who not in OPTIMIZED:
                problems.append(f"ask for {who}: only {', '.join(OPTIMIZED)} may be written")
            elif not str(text or "").strip():
                problems.append(f"ask for {who} is empty")
            elif len(str(text)) > MAX_ASK_CHARS:
                problems.append(f"ask for {who} is {len(str(text))} chars (max {MAX_ASK_CHARS})")
        for who in OPTIMIZED:
            if who not in self.asks:
                problems.append(f"missing ask for {who}")
        return problems

    def ask_overrides(self) -> Dict[str, str]:
        """What goes into agent1's ``ask_overrides`` — the two written asks, verbatim."""
        return {who: str(self.asks[who]).strip() for who in OPTIMIZED if who in self.asks}

    def all_asks(self, base: Workspace) -> Dict[str, str]:
        overrides = self.ask_overrides()
        return {p: overrides.get(p, self.fixed_ask) for p in base.principals}

    # --------------------------------------------------------------------- identity
    def to_dict(self) -> Dict[str, Any]:
        return {"asks": self.ask_overrides(), "fixed_ask": self.fixed_ask,
                "rationale": self.rationale}

    @classmethod
    def from_dict(cls, obj: Dict[str, Any], *, fixed_ask: str = FIXED_ASK) -> "Candidate":
        asks = obj.get("asks")
        if not isinstance(asks, dict):
            raise ValueError("candidate needs an `asks` object mapping Nadia and Rafael to text")
        return cls(
            asks={str(k): str(v) for k, v in asks.items()},
            rationale=str(obj.get("rationale") or ""),
            fixed_ask=str(obj.get("fixed_ask") or fixed_ask),
        )

    def digest(self) -> str:
        payload = {"asks": self.ask_overrides(), "fixed_ask": self.fixed_ask}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]

    def run_id(self, base: Workspace, seed: int) -> str:
        return f"{base.version}__{self.digest()}__seed{seed}"


def parse_candidate(raw: str, *, fixed_ask: str = FIXED_ASK) -> Candidate:
    return Candidate.from_dict(extract_json_object(raw), fixed_ask=fixed_ask)
