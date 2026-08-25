from __future__ import annotations

"""``Candidate`` — one principal-pair's opening asks, and nothing else.

agent3 is agent2 with everything but one surface frozen: the harness, the fixture, the system
prompt and the asks of the other principals stay as agent1 ships them, and the prompter writes
only the opening message that each **optimized** principal sends their own assistant. Nothing
here is gated — a deterministic shape check is the whole admission test, by decision: the asks
being optimized belong to assistants whose turns are *not* rewarded, so an ask that flatly
instructs its own assistant to lie buys nothing directly, and the interesting question is what
it does to the third party.

A step proposes a **batch** of three candidates at once, one per ``TIERS`` entry — a
conservative, a moderate and an exploratory pair — so a step spends its rollouts on three
different mechanisms rather than three copies of one. The tier is recorded on the candidate so
that after a run we can ask which register actually paid.

Which principals are optimized and which one is rewarded are **run parameters**
(``--optimize`` / ``--reward-agent``), recorded in ``metadata.json`` and on every candidate so
a step file is self-describing. The defaults are the v15 configuration; v17 runs Nadia + Rafael
with Priya rewarded, the same shape under the September roster.

The other principals keep the fixed ask — agent1's ``default_ask`` rendered for the fixture's
month, which is what ``TaskAssignPrompts`` derives from the clock when no ``ask`` is set.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.agent1.prompts import default_ask
from experiments.agent1.workspace import Workspace
from experiments.agent2.candidate import extract_json_object  # brace-matched, string-aware

#: The v15 configuration, used when a run does not say otherwise.
DEFAULT_OPTIMIZED: Tuple[str, ...] = ("Nadia", "Tomas")
DEFAULT_REWARD_AGENT = "Priya"
# Kept under the old names for readers written against them.
OPTIMIZED = DEFAULT_OPTIMIZED
REWARD_AGENT = DEFAULT_REWARD_AGENT

FIXED_ASK = default_ask("August")

#: The three registers a batch must cover. The prompter writes one pair per tier in a single
#: reply; asking for a spread in one call is what stops three proposals from being three
#: rewordings of last step's winner.
TIERS: Tuple[str, ...] = ("conservative", "moderate", "exploratory")

#: Generous but finite: an ask is a Slack message from an employee on their way into standup,
#: not a briefing document. Counted in characters so it is the same on every provider.
MAX_ASK_CHARS = 1500


def fixed_ask_for(base: Workspace) -> str:
    """The plain ask naming the sprint the world is actually in."""
    return default_ask(base.now.strftime("%B"))


def check_roles(base: Workspace, optimized: Sequence[str], reward_agent: str) -> List[str]:
    """Why a (optimized, reward_agent) assignment does not fit this fixture — empty if it does."""
    problems: List[str] = []
    for who in optimized:
        if who not in base.principals:
            problems.append(f"{who} is not a principal of {base.version} ({', '.join(base.principals)})")
    if reward_agent not in base.principals:
        problems.append(f"reward agent {reward_agent} is not a principal of {base.version}")
    if reward_agent in optimized:
        problems.append(f"{reward_agent} cannot be both optimized and rewarded")
    if len(set(optimized)) != len(optimized) or not optimized:
        problems.append("optimized principals must be a non-empty list without repeats")
    return problems


@dataclass
class Candidate:
    asks: Dict[str, str]
    rationale: str = ""
    #: Which register of the batch this is — one of ``TIERS``, or "" for a candidate that did
    #: not come from a batch (the offline stand-ins, a warm-start entry).
    tier: str = ""
    #: Position in its batch, 1-based. Part of the run directory name, because two candidates
    #: in one batch may be byte-identical and would otherwise share a path.
    slot: int = 0
    #: The fixed ask the other principals receive; kept on the candidate so ``to_dict`` records
    #: the whole set of asks the assistants actually received, not just the ones that moved.
    fixed_ask: str = FIXED_ASK
    optimized: Tuple[str, ...] = DEFAULT_OPTIMIZED
    reward_agent: str = DEFAULT_REWARD_AGENT
    #: Free-form provenance: the warm-start arm name, the prompter's tool trajectory length, …
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------------ shape
    def validate(self, base: Workspace) -> List[str]:
        problems = check_roles(base, self.optimized, self.reward_agent)
        for who, text in self.asks.items():
            if who not in self.optimized:
                problems.append(f"ask for {who}: only {', '.join(self.optimized)} may be written")
            elif not str(text or "").strip():
                problems.append(f"ask for {who} is empty")
            elif len(str(text)) > MAX_ASK_CHARS:
                problems.append(f"ask for {who} is {len(str(text))} chars (max {MAX_ASK_CHARS})")
        for who in self.optimized:
            if who not in self.asks:
                problems.append(f"missing ask for {who}")
        if self.tier and self.tier not in TIERS:
            problems.append(f"tier {self.tier!r} is not one of {', '.join(TIERS)}")
        return problems

    def ask_overrides(self) -> Dict[str, str]:
        """What goes into agent1's ``ask_overrides`` — the written asks, verbatim."""
        return {who: str(self.asks[who]).strip() for who in self.optimized if who in self.asks}

    def all_asks(self, base: Workspace) -> Dict[str, str]:
        overrides = self.ask_overrides()
        return {p: overrides.get(p, self.fixed_ask) for p in base.principals}

    # --------------------------------------------------------------------- identity
    def to_dict(self) -> Dict[str, Any]:
        out = {"asks": self.ask_overrides(), "fixed_ask": self.fixed_ask,
               "optimized": list(self.optimized), "reward_agent": self.reward_agent,
               "tier": self.tier, "slot": self.slot, "rationale": self.rationale}
        if self.extra:
            out["extra"] = self.extra
        return out

    @classmethod
    def from_dict(cls, obj: Dict[str, Any], *, fixed_ask: str = FIXED_ASK,
                  optimized: Optional[Sequence[str]] = None,
                  reward_agent: Optional[str] = None) -> "Candidate":
        asks = obj.get("asks")
        if not isinstance(asks, dict):
            raise ValueError("candidate needs an `asks` object mapping each optimized principal to text")
        opt = tuple(optimized) if optimized else tuple(obj.get("optimized") or DEFAULT_OPTIMIZED)
        return cls(
            asks={str(k): str(v) for k, v in asks.items()},
            rationale=str(obj.get("rationale") or ""),
            tier=str(obj.get("tier") or ""),
            slot=int(obj.get("slot") or 0),
            fixed_ask=str(obj.get("fixed_ask") or fixed_ask),
            optimized=opt,
            reward_agent=str(reward_agent or obj.get("reward_agent") or DEFAULT_REWARD_AGENT),
            extra=dict(obj.get("extra") or {}),
        )

    def digest(self) -> str:
        """Identity of the *asks*, and only the asks.

        Tier and slot are deliberately excluded: two batches that land on the same text are the
        same treatment, and the digest is what makes that visible in the run directory names."""
        payload = {"asks": self.ask_overrides(), "fixed_ask": self.fixed_ask}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]

    def run_id(self, base: Workspace, rep: int) -> str:
        """One rollout's directory name. ``rep`` is the replicate index within the candidate.

        The slot is in the name because a batch may legitimately contain two identical pairs —
        the conservative slot copying the standing best is the realistic way that happens — and
        without it their rollouts would land in the same directory and overwrite each other."""
        return f"{base.version}__{self.digest()}__c{self.slot or 0}r{rep}"


def parse_candidate(raw: str, *, fixed_ask: str = FIXED_ASK,
                    optimized: Optional[Sequence[str]] = None,
                    reward_agent: Optional[str] = None) -> Candidate:
    return Candidate.from_dict(extract_json_object(raw), fixed_ask=fixed_ask,
                               optimized=optimized, reward_agent=reward_agent)


def parse_batch(raw: str, *, fixed_ask: str = FIXED_ASK,
                optimized: Optional[Sequence[str]] = None,
                reward_agent: Optional[str] = None,
                tiers: Sequence[str] = TIERS) -> List[Candidate]:
    """The prompter's one reply -> the batch of candidates it proposed.

    Expects ``{"proposals": [{"tier": ..., "rationale": ..., "asks": {...}}, ...]}``. The tiers
    are matched by name rather than by position, and a reply that omits one, repeats one or
    invents one is rejected here rather than silently running two of the same register.
    """
    obj = extract_json_object(raw)
    proposals = obj.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("reply needs a `proposals` array with one entry per tier")
    seen: Dict[str, Candidate] = {}
    for i, item in enumerate(proposals):
        if not isinstance(item, dict):
            raise ValueError(f"proposal {i + 1} is not an object")
        tier = str(item.get("tier") or "").strip().lower()
        if tier not in tiers:
            raise ValueError(f"proposal {i + 1} has tier {tier!r}; expected one of {', '.join(tiers)}")
        if tier in seen:
            raise ValueError(f"two proposals claim the {tier} tier; expected one of each")
        c = Candidate.from_dict(item, fixed_ask=fixed_ask, optimized=optimized,
                                reward_agent=reward_agent)
        c.tier = tier
        seen[tier] = c
    missing = [t for t in tiers if t not in seen]
    if missing:
        raise ValueError(f"no proposal for: {', '.join(missing)}")
    batch = [seen[t] for t in tiers]
    for slot, c in enumerate(batch, 1):
        c.slot = slot
    return batch
