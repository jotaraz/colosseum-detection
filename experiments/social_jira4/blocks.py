from __future__ import annotations

"""The template representation: ``Blocks``.

``specs.md`` fixes the target prompt to a template of six blocks. Four are **free** — the
prompter writes them as prose — and two are **drawn from a fixed pool** (the prompter picks an
id; see ``pools.py``). ``Blocks`` is that structured value: it is what the prompter emits, what
the checks-and-balances model gates, what the target runner renders, and what a loop step
records. Keeping it a plain dataclass (not free-form text) is what makes the template
*strictly* followed — the prompter can only fill the slots, never restructure the prompt.

Free vs pooled matters for how each block reaches the target:
  * free blocks are substituted verbatim into the rendered prompt (``adaptive_prompts.py``);
  * pooled blocks are ids that map to *scenario/run config* (dislike strength, which decoy
    substrates are active) so the generated ground truth stays consistent with what the agent
    saw — a pool id is NOT rendered text, it selects how the environment and ``decoys.py`` build
    the inbox and the fabrication substrate.

``decoy_info_ids`` is a **list**: decoy substrates compose (an ops feed is a channel, not a fact
set, so it can sit alongside a calendar or an access register). ``[]`` — or the sentinel
``["none"]``, which normalises to ``[]`` — means no substrate at all.
"""

import json
import re
from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Dict, List, Optional

from experiments.social_jira4.decoys import NONE_ID, normalize_ids

# The four free (prompter-authored) blocks and the two pooled (prompter-selected) blocks.
FREE_FIELDS = (
    "general_task_info",
    "decoy_policies",
    "confidentiality_reason",
    "personality",
)
POOL_FIELDS = (
    "private_messages_id",
    "decoy_info_ids",
)
ALL_FIELDS = FREE_FIELDS + POOL_FIELDS

# Proposals and recorded seeds from before decoys composed carry a single ``decoy_info_id``;
# accept it on load so ``seeds/`` and older run artifacts still deserialise.
_LEGACY_DECOY_FIELD = "decoy_info_id"


# --- scenario-leak guard -------------------------------------------------------------------
# The roster (names, professions, seniority), the team size, the tasks, and the calendars are
# generated PER SEED and injected into the prompt at runtime. The free blocks wrap around that
# injected content, so a block that names individuals, fixes the team size, or states availability
# contradicts what the assistant actually sees — e.g. a block "You represent Pat; the team is Pat,
# Sam, Taylor, Jordan" while the injected roster is Layla/Pauline/... This guard rejects such blocks
# DETERMINISTICALLY (before a validator call is spent). It targets the *structure* (cast intro /
# size-fixing / availability), not specific names — the name pool is per-seed and not enumerable.
#
# Care: legitimate decoy policies say "pair the two people with most overlap" and "morning people",
# so the guard flags SIZE-FIXING ("team of four", "exactly four people"), not bare "two people".
_LEAK_PATTERNS = [
    # a list of 3+ capitalised names — "Pat, Sam, and Taylor" / "Pat, Sam, Taylor and Jordan"
    (re.compile(r"\b[A-Z][a-z]+,\s+[A-Z][a-z]+(?:,\s+[A-Z][a-z]+)*,?\s+(?:and|&)\s+[A-Z][a-z]+"),
     "introduces a named cast"),
    # a capitalised name right after a person-referent — "employee Pat", "represent Sam"
    (re.compile(r"\b(?:employee|colleague|coworker|teammate|represent|named|assistant to)\s+([A-Z][a-z]+)"),
     "names an individual"),
    # a fixed team size — "team of four", "team has four", "exactly three people", "four-person team"
    (re.compile(r"\b(?:team (?:of|has|is|consists of)|group of|consists of)\s+(?:exactly\s+|precisely\s+)?(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\b", re.I),
     "fixes the team size"),
    (re.compile(r"\b(?:exactly|precisely)\s+(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:people|persons?|employees|colleagues|members|teammates)\b", re.I),
     "fixes the team size"),
    (re.compile(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)[- ]person team\b", re.I),
     "fixes the team size"),
    # states specific availability / a schedule — that is the injected calendar, not the prompt's job
    (re.compile(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.I),
     "names a weekday (any weekday name is rejected)"),
    (re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?:morning|afternoon|AM|PM)\b"),
     "states a calendar slot"),
    (re.compile(r"\b(?:available|free)\s+(?:on|from|during|next week|this week|between)\b", re.I),
     "states availability"),
    (re.compile(r"\bfree slots?\b", re.I),
     "states availability"),
]
# capitalised words that legitimately follow a person-referent but are NOT names (avoid false positives)
_NOT_NAMES = frozenset({
    "Satisfaction", "Engagement", "Wellbeing", "Manager", "Engineer", "Scientist", "Lead",
    "Representative", "Analyst", "Designer", "Developer", "Product", "Sales", "Data", "Frontend",
    "Backend", "Marketing",
})


def _scenario_leak_problems(field: str, text: str) -> List[str]:
    """Flag a free block that bakes in a specific scenario (cast / team size / availability),
    which collides with the per-seed roster + calendar injected at runtime."""
    problems: List[str] = []
    for pat, desc in _LEAK_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        if desc == "names an individual" and m.group(1) in _NOT_NAMES:
            continue
        problems.append(
            f"free block {field!r} {desc} ({m.group(0)!r}); blocks must stay generic — refer to "
            '"your employee" / "a colleague"; the roster, team size and calendar are injected per seed'
        )
    return problems


@dataclass
class Blocks:
    """One fully-specified target prompt, as template slots.

    Free blocks hold prose; pooled blocks hold a pool id (validated against ``pools.py``)."""

    # --- free: prompter-authored prose ---
    general_task_info: str
    decoy_policies: str
    confidentiality_reason: str
    personality: str
    # --- pooled: prompter-selected ids ---
    private_messages_id: str
    decoy_info_ids: List[str] = dc_field(default_factory=list)

    def __post_init__(self) -> None:
        # Canonicalise on construction so ``to_dict``, the run_id and the rendered prompt can
        # never disagree about which substrates are active or in what order.
        self.decoy_info_ids = normalize_ids(self.decoy_info_ids)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any], *, defaults: Optional[Dict[str, Any]] = None) -> "Blocks":
        """Parse a block-set. ``defaults`` supplies POOLED fields the caller does not expect to be
        present — with a fixed environment (``pools.Environment``) those are struck from the
        prompter's output schema, so a well-behaved reply legitimately arrives without them. The
        four FREE blocks stay strictly required either way: a missing one is a real malformation."""
        d = dict(d)
        if "decoy_info_ids" not in d and _LEGACY_DECOY_FIELD in d:
            d["decoy_info_ids"] = [d.pop(_LEGACY_DECOY_FIELD)]
        for f, v in (defaults or {}).items():
            if f in POOL_FIELDS and d.get(f) is None:
                d[f] = list(v) if isinstance(v, list) else v
        missing = [f for f in ALL_FIELDS if f not in d]
        if missing:
            raise ValueError(f"Blocks missing fields: {missing}")
        return cls(**{f: d[f] for f in ALL_FIELDS})

    def with_environment(self, env: Any) -> "Blocks":
        """A copy with the pinned environment axes forced onto it.

        ``env`` is duck-typed (a ``pools.Environment``) to keep this module free of a ``pools``
        import — ``pools`` already imports this one. An axis whose value is ``None`` is the
        prompter's to choose and is left untouched.

        Forcing rather than rejecting is deliberate: a prompter that emits a pooled id it was told
        not to send — a stale scaffold, a warm-start record from a different condition — then costs
        nothing, instead of burning a repair attempt on a field that is not its decision anyway."""
        d = self.to_dict()
        pid = getattr(env, "private_messages_id", None)
        dids = getattr(env, "decoy_info_ids", None)
        if pid is not None:
            d["private_messages_id"] = pid
        if dids is not None:
            d["decoy_info_ids"] = list(dids)
        return Blocks(**d)

    def validate(
        self,
        private_pool: Dict[str, Any],
        decoy_pool: Dict[str, Any],
        *,
        leak_guard: bool = True,
    ) -> List[str]:
        """Return a list of human-readable problems ([] == valid). The loop uses this to
        reject a malformed proposal before spending a target run on it.

        ``leak_guard=False`` turns off the scenario-leak regexes above (``loop --meta-gate`` does
        this: that mode drops every coherence check on purpose, so blocks MAY then contradict the
        injected roster). The structural checks — empty free block, unknown pool id — always run:
        they are not a policy, they are what ``render`` needs to work at all."""
        problems: List[str] = []
        for f in FREE_FIELDS:
            v = getattr(self, f)
            if not isinstance(v, str) or not v.strip():
                problems.append(f"free block {f!r} is empty")
                continue
            if leak_guard:
                problems.extend(_scenario_leak_problems(f, v))  # deterministic scenario-leak guard
        if self.private_messages_id not in private_pool:
            problems.append(
                f"private_messages_id {self.private_messages_id!r} not in pool "
                f"{sorted(private_pool)}"
            )
        if not isinstance(self.decoy_info_ids, list):
            problems.append(
                f"decoy_info_ids must be a list of ids, got {type(self.decoy_info_ids).__name__}"
            )
        else:
            # ``none`` survives normalisation only when it was the *sole* entry (it normalises to
            # []), so an unknown id here is a genuine typo, not the sentinel.
            for did in self.decoy_info_ids:
                if did not in decoy_pool or did == NONE_ID:
                    problems.append(
                        f"decoy_info_ids entry {did!r} not in pool "
                        f"{sorted(k for k in decoy_pool if k != NONE_ID)}"
                    )
        return problems

    def decoy_slug(self) -> str:
        """Compact, canonical label for run ids and logs — e.g. ``calendar+ops_feed``."""
        return "+".join(self.decoy_info_ids) if self.decoy_info_ids else NONE_ID


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Pull the first top-level JSON object out of an LLM response (tolerates code fences and
    surrounding prose). Raises ``ValueError`` if none parses."""
    s = (text or "").strip()
    if s.startswith("```"):
        # strip a leading ```json / ``` fence and the trailing fence
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    # find the outermost braces
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in prompter response")
    return json.loads(s[start : end + 1])


def parse_blocks(text: str) -> Blocks:
    """Parse a prompter LLM response (JSON, possibly fenced) into ``Blocks``."""
    return Blocks.from_dict(_extract_json_object(text))
