from __future__ import annotations

"""The agent: one persistent conversation per assistant, and the turn boundary.

Two independent pieces.

**One stream.** ``BaseAgent._multi_step_response_generation`` opens every turn with
``context = self.client.init_context(system_prompt, user_prompt)`` — a fresh two-message
list, which is the entire cause of per-turn amnesia. Rather than copy that method (and own
a divergent copy of terrarium's loop forever), :func:`install_stream` shadows two methods on
the agent's own client instance:

* ``init_context`` — builds the list once, then *appends* each turn's message to the stored
  stream. Safe to shadow because it is a ``@staticmethod`` and instance attributes win on
  lookup, and safe to keep a reference to because ``process_tool_calls`` mutates the list in
  place and returns the same object (``llm_server/clients/vllm_client.py:138-163``).
* ``process_tool_calls`` — strips reasoning from the assistant message before it persists.
  ``cluster/patch_vllm_client.py`` already does this for ``reasoning_content``, but vLLM
  0.23 renamed the field to ``reasoning`` and the patch does not cover the new spelling. It
  mattered little when history only had to survive the steps of one turn; with a persistent
  stream it would accumulate over every round, so both spellings are stripped here.
  Reasoning is still captured for the logs off the *response*, which this never touches.

**The turn boundary.** No forcing: ``tool_choice`` is never set, so an agent that has
nothing to add can simply stop calling tools and pass. A turn ends when the agent posts to
the sprint channel, when it stops calling tools, or when the step budget runs out. DMs and
board claims are deliberately neutral — an assistant can look things up, claim its ticket
and message its manager in one turn, and only a channel post closes it out.

**Salvage.** "Stops calling tools" is the pass, which makes every *broken* step look like a
considered decision to say nothing. Two faults were found masquerading as passes in the
existing runs, and neither left a mark in the record:

* ``dropped_call`` — the model emitted a tool call the provider failed to parse. On
  gpt-oss-120b over unpinned OpenRouter routing this is 8/87 steps: ``content`` null, no
  ``tool_calls``, and the call's *arguments* glued onto the end of the chain-of-thought
  (``"Need to read new messages.{\\n \\"conversation\\": \\"#aug-2026-sprint\\", ...}"``) with
  the function name gone. In ``gptoss120b_unread_priya_s43`` all four round-3 turns died this
  way, nobody posted, and the run recorded ``stalled``.
* ``truncated`` — ``finish_reason == "length"``: the whole ``max_tokens`` budget went into
  reasoning and the call was never reached. 29 DeepSeek and 2 Kimi-K3 steps across v11/v13,
  each with a 30k-character chain-of-thought cut off mid-word.

:func:`classify_step` separates those from a real pass, and a bounded number of salvage
retries per turn re-runs the step (the dud assistant message is dropped from the context
first, so the retry is a genuine resample rather than a continuation). Every discard is
recorded on the turn whether or not it was retried, so the failure rate is measurable
instead of invisible. Deliberately narrow: a blank step whose reasoning does *not* end in an
arguments blob is left alone, because that is what a real pass looks like.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from terrarium.agents.base import BaseAgent

logger = logging.getLogger(__name__)

REASONING_KEYS = ("reasoning", "reasoning_content")

#: Verdicts from :func:`classify_step` on a step that executed no tools.
PASS = "pass"                    #: the agent had nothing to add — end the turn, as designed
DROPPED_CALL = "dropped_call"    #: a tool call the provider failed to parse
TRUNCATED = "truncated"          #: finish_reason == "length"; the budget went to reasoning
EMPTY = "empty"                  #: no text, no reasoning, no call — nothing came back at all

#: Verdicts worth spending a retry on. A `PASS` is not a fault and is never retried.
SALVAGEABLE = (DROPPED_CALL, TRUNCATED, EMPTY)

#: How many salvage retries a turn may spend, unless the config overrides it. Two is enough
#: for the observed faults (both are transient at temperature > 0) and cheap on a genuine
#: fault; the step budget caps the total cost regardless.
DEFAULT_SALVAGE_RETRIES = 2


def _unwrapped(func: Any) -> Any:
    """The pristine base method, stepping past any patch installed over it."""
    return getattr(func, "__wrapped__", func)


def _reasoning_of(message: Dict[str, Any]) -> str:
    """The message's chain-of-thought, whichever of the three spellings carries it.

    OpenRouter sends structured ``reasoning_details``; vLLM 0.12 ``reasoning_content``; vLLM
    0.23 and OpenRouter's flat field ``reasoning``. Azure sends none of them, which is why an
    absent CoT can never on its own be read as a fault.
    """
    for key in REASONING_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    details = message.get("reasoning_details")
    if isinstance(details, list):
        return "\n".join(
            str(part.get("text") or "") for part in details if isinstance(part, dict)
        )
    return ""


def classify_step(message: Dict[str, Any], finish_reason: Optional[str] = None) -> str:
    """Why a step executed no tools: a considered pass, or one of three ways of breaking.

    Order matters. ``length`` is checked first and regardless of content, because a truncated
    step's text is a half-written message, not a decision. Then text present is the ordinary
    close-out. What remains is blank, and is only called a dropped call on the positive
    evidence of an arguments blob at the end of the reasoning — a blank step that merely
    *thought* before saying nothing is exactly the designed pass and is left alone.
    """
    if str(finish_reason or "").lower() == "length":
        return TRUNCATED
    if str(message.get("content") or "").strip():
        return PASS
    reasoning = _reasoning_of(message).strip()
    if not reasoning:
        return EMPTY
    # The leaked-arguments signature: the CoT ends on a closed JSON object.
    if reasoning.endswith("}") and "{" in reasoning:
        return DROPPED_CALL
    return PASS


# --------------------------------------------------------------------- one stream
def install_stream(client: Any) -> Any:
    """Give one client a conversation that survives across turns. Idempotent."""
    if getattr(client, "_agent1_stream_installed", False):
        return client

    original_init = client.init_context
    original_process = client.process_tool_calls
    client._stream: Optional[List[Dict[str, Any]]] = None

    def init_context(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
        if client._stream is None:
            client._stream = original_init(system_prompt, user_prompt)
        else:
            client._stream.append({"role": "user", "content": user_prompt})
        return client._stream

    async def process_tool_calls(response, context, execute_tool_callback):
        # Classify FIRST. The reply's message object is the very one the client appends to the
        # context, so the reasoning-stripping loop below would erase the evidence the verdict
        # is read from (on vLLM, where the CoT arrives as `reasoning_content`) before it could
        # be read. `reasoning_chars` is snapshotted here for the same reason.
        choice = ((response or {}).get("choices") or [{}])[0] or {}
        reply = choice.get("message") or {}
        verdict = classify_step(reply, choice.get("finish_reason"))
        reasoning_chars = len(_reasoning_of(reply))

        executed, context, step_tools = await original_process(
            response, context, execute_tool_callback
        )
        for message in context:
            if isinstance(message, dict) and message.get("role") != "tool":
                for key in REASONING_KEYS:
                    message.pop(key, None)
        client._stream = context

        # A step that called no tools IS the pass — end the turn. The base loop only breaks
        # on `_env_state_committed` or the step cap, so without this a model that has said
        # its piece keeps being re-prompted until the budget runs out: in the first live run
        # every closing turn burned all 10 steps on trailing pleasantries, and because the
        # turn's recorded text is the LAST step's, the real answer was overwritten by them.
        # This is the only hook available — `_execute_tool_call` is never reached when there
        # are no tool calls to execute.
        #
        # ...unless the step is broken rather than done (see the module docstring). Then the
        # turn is NOT ended: leaving `_env_state_committed` False sends the base loop back for
        # another `generate_response` on the same context, which is the retry.
        owner = getattr(client, "_agent1_owner", None)
        if executed == 0 and owner is not None:
            budget = int(getattr(owner, "salvage_retries", DEFAULT_SALVAGE_RETRIES))
            spent = int(getattr(owner, "_turn_salvages", 0))
            # A step left in the budget as well as in the salvage allowance: on the last step
            # the base loop breaks whatever we decide here, and claiming a retry that cannot
            # happen would put a `retried: true` in the record that never ran.
            room = int(getattr(owner, "_turn_step", 0)) < int(
                getattr(owner, "max_conversation_steps", 0) or 0
            )
            retry = verdict in SALVAGEABLE and spent < budget and room
            if verdict != PASS:
                owner.turn_discards.append({
                    "step": int(getattr(owner, "_turn_step", 0)),
                    "verdict": verdict,
                    "finish_reason": choice.get("finish_reason"),
                    "provider": (response or {}).get("provider"),
                    "reasoning_chars": reasoning_chars,
                    "retried": retry,
                })
                logger.warning(
                    "%s step %s: %s (finish_reason=%s, provider=%s) — %s",
                    getattr(owner, "name", "?"), getattr(owner, "_turn_step", 0),
                    verdict, choice.get("finish_reason"), (response or {}).get("provider"),
                    "retrying" if retry else "accepted as the end of the turn",
                )
            if retry:
                # Drop the dud from the stream before regenerating. Leaving it would make the
                # retry a continuation of a broken message (and a truncated one drags its
                # 30k-character dead reasoning into every later prompt); popping makes it a
                # clean resample of the same state. Guarded on identity so a client that
                # appends differently can never lose a real message here.
                if context and context[-1] is reply:
                    context.pop()
                    client._stream = context
                owner._turn_salvages = spent + 1
            else:
                owner._env_state_committed = True
        return executed, context, step_tools

    client.init_context = init_context
    client.process_tool_calls = process_tool_calls
    client._agent1_stream_installed = True
    return client


def install_step_capture(
    client: Any, sink: List[Dict[str, Any]], *, agent_name: str
) -> Any:
    """Record what each model call produced, and number the steps.

    Two jobs. It reads chain-of-thought off the *raw response* — the "drop in context, keep
    in logs" split, since :func:`install_stream` strips it from the persisted conversation —
    handling both spellings (vLLM 0.12 emits ``reasoning_content``, 0.23 and OpenRouter emit
    ``reasoning``). And it advances the owning agent's step counter, which is what lets tool
    calls be attributed to the model call that requested them: one ``generate_response`` is
    exactly one step, and every tool executed afterwards belongs to it.
    """
    if getattr(client, "_agent1_step_capture_installed", False):
        return client
    original = client.generate_response

    def generate_response(input, params):  # noqa: A002 - matches the client signature
        owner = getattr(client, "_agent1_owner", None)
        step = 1
        if owner is not None:
            owner._turn_step = int(getattr(owner, "_turn_step", 0)) + 1
            step = owner._turn_step
        response, text = original(input=input, params=params)
        try:
            message = ((response or {}).get("choices") or [{}])[0].get("message") or {}
            thought = next((message.get(k) for k in REASONING_KEYS if message.get(k)), None)
            # OpenRouter returns the charged amount in `usage.cost` because the client asks
            # for `usage: {include: true}`; the bundled get_usage sums only token fields and
            # drops it. Captured here so a run record carries what it actually cost.
            usage = (response or {}).get("usage") or {}
            choice = ((response or {}).get("choices") or [{}])[0] or {}
            sink.append({
                "agent": agent_name, "step": step,
                "reasoning": thought or "", "text": text or "",
                "cost": float(usage.get("cost") or 0.0),
                # Provenance for the step, without which a routing-caused failure cannot be
                # diagnosed after the fact — you cannot tell a model that emitted no call from
                # an upstream that dropped one, or a considered stop from a `length` cut-off.
                # OpenRouter names the upstream that served the call in `provider`; Azure has
                # no equivalent and records None.
                "finish_reason": choice.get("finish_reason"),
                "provider": (response or {}).get("provider"),
                "tool_calls": len((choice.get("message") or {}).get("tool_calls") or []),
            })
        except Exception:
            logger.debug("step capture failed", exc_info=True)
        return response, text

    client.generate_response = generate_response
    client._agent1_step_capture_installed = True
    return client


#: Back-compat alias — the function grew a second job.
install_reasoning_capture = install_step_capture


def sanitize_arguments(arguments: Any) -> Dict[str, Any]:
    """Coerce a model's argument object into kwargs a tool handler can be called with.

    Defensive only: a well-formed call passes through untouched. The observed malformations,
    all of which reach ``_execute_tool_call`` as-is otherwise:

    * ``'{"task_id": "T1"}'`` — arguments still a JSON *string*. The OpenRouter client decodes
      them, but a client that doesn't (or a double-encoded reply) would hand a tool a string.
    * ``{"": {}}`` — gpt-oss-120b's spelling of "no arguments", seen on a real
      ``board_get_assignments`` call. Harmless on a tool that takes none; on
      ``slack_post_message`` it is either an unexpected-keyword ``TypeError`` or a silently
      dropped payload.
    * ``{"arguments": {...}}`` / ``{"parameters": {...}}`` — the real object wrapped in the
      envelope key. Unwrapped only when it is the *sole* key and its value is a dict, so a
      tool that genuinely takes a field by one of those names is never rewritten.

    Anything it cannot make sense of is returned as an empty dict, which the tools already
    handle: a call with a missing required field comes back as a recoverable ``retry``, which
    the model can see and correct, rather than an exception that kills the turn.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {}
    if not isinstance(arguments, dict):
        return {}
    cleaned = {k: v for k, v in arguments.items() if isinstance(k, str) and k.strip()}
    if len(cleaned) == 1:
        key, value = next(iter(cleaned.items()))
        if key in ("arguments", "parameters", "args", "kwargs") and isinstance(value, dict):
            return dict(value)
    return cleaned


def stream_of(agent: Any) -> List[Dict[str, Any]]:
    """The accumulated conversation, for logging."""
    return list(getattr(getattr(agent, "client", None), "_stream", None) or [])


# ------------------------------------------------------------------------- agent
class DiscoveryAgent(BaseAgent):
    """A ``BaseAgent`` that keeps its conversation and ends its turn on a channel post."""

    def __init__(
        self,
        *args,
        sprint_channel: str = "",
        salvage_retries: int = DEFAULT_SALVAGE_RETRIES,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.sprint_channel = str(sprint_channel or "").lstrip("#")
        self.salvage_retries = int(salvage_retries)
        self._end_turn = False
        self._turn_step = 0
        self._turn_salvages = 0
        self.turn_tool_calls: List[Dict[str, Any]] = []
        self.turn_discards: List[Dict[str, Any]] = []
        if getattr(self, "client", None) is not None:
            install_stream(self.client)
            # Back-reference so the stream wrapper can end the turn on a no-tool step.
            self.client._agent1_owner = self

    # ------------------------------------------------------------------ params
    def _build_generation_params(self, tool_set) -> Dict[str, Any]:
        """Base params, minus any forcing. Also the per-turn reset point."""
        params = _unwrapped(BaseAgent._build_generation_params)(self, tool_set)
        params.pop("tool_choice", None)
        self._end_turn = False
        self._turn_step = 0
        self._turn_salvages = 0
        self.turn_tool_calls = []
        self.turn_discards = []
        return params

    # ------------------------------------------------------------------- tools
    def _is_sprint_post(self, tool_name: str, result: Any) -> bool:
        if tool_name != "slack_post_message" or not isinstance(result, dict):
            return False
        if not result.get("ok"):
            return False
        posted_to = str(result.get("conversation") or "").lstrip("#")
        return bool(self.sprint_channel) and posted_to == self.sprint_channel

    async def _execute_tool_call(
        self, tool_name: str, tool_arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        # What the model sent is what gets recorded; what the handler is called with is the
        # sanitized form. Keeping both means a malformation stays visible in the record
        # (`arguments_raw` appears only when they differ) instead of being tidied out of the
        # evidence — the same reason a discarded step is recorded rather than dropped.
        raw = tool_arguments
        tool_arguments = sanitize_arguments(tool_arguments)
        result = await _unwrapped(BaseAgent._execute_tool_call)(
            self, tool_name, tool_arguments
        )
        try:
            record = {
                # The model call that asked for this tool — lets a reader interleave
                # reasoning and calls in the order they actually happened.
                "step": int(getattr(self, "_turn_step", 0)),
                "tool": tool_name,
                "arguments": dict(tool_arguments or {}),
                "result": result,
            }
            if raw != tool_arguments:
                record["arguments_raw"] = raw
                logger.warning(
                    "%s step %s: malformed arguments to %s — %r sanitized to %r",
                    self.name, getattr(self, "_turn_step", 0), tool_name, raw, tool_arguments,
                )
            self.turn_tool_calls.append(record)
            if self._is_sprint_post(tool_name, result):
                self._end_turn = True
            # Assign rather than or-in: the base loop resets the flag each step, and a tool
            # returning `state_updates` must not be able to end the turn behind our back.
            self._env_state_committed = self._end_turn
        except Exception:
            logger.exception("DiscoveryAgent turn bookkeeping failed")
        return result
