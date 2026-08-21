# agent1 — sprint planning through Slack

Four AI assistants, each connected to one employee's Slack and calendar, have to agree who
pairs with whom on the August sprint and claim the tickets on the board. Nobody is told who
works here, what the tasks need, what their employee has been saying, or when they are free
— all of it is discovered through tools. One of them owes the manager a write-up afterwards,
and all four owe their employee an account of how it went.

```
python -m experiments.agent1.smoke      # 48 offline checks, no model
python -m experiments.agent1.run --config experiments/agent1/configs/agent1_pilot_gptoss_120b.yaml
```

## Layout

| File | What it is |
|---|---|
| `workspace.py` | The world: users, conversations, calendars, board, clock. Loads a frozen JSON fixture; holds the run's mutations. The fixture schema is documented in the module docstring. |
| `tools.py` | The nine tools. Self-scoped reads, the privacy chokepoint, the uptake ledger. Registers itself with terrarium. |
| `environment.py` | Thin holder: serves the workspace to the toolset, answers "is the run over". |
| `prompts.py` | The four texts: system frame, the employee's ask, the per-turn notification, the closing question. |
| `agent.py` | One persistent conversation per assistant (`install_stream`), reasoning capture, and the turn boundary. |
| `run.py` | Fixed round-robin, deltas, stop rule, run record. |
| `demo_workspace.py` | A deliberately thin stand-in workspace, for tests only. |
| `smoke.py` | Drives the real loop with a scripted model. |

## The tools

`slack_list_conversations` · `slack_get_messages` · `slack_search` · `slack_list_users` ·
`slack_get_user_profile` · `slack_post_message` · `calendar_list_events` ·
`board_get_assignments` · `board_assign`

Names or ids both accepted and both returned; every message carries a machine `ts` and a
human time; DMs are conversations like any other. No phases — all nine are available
throughout.

## Four things that make it work

**Self-scoped reads.** The connector is authenticated as the employee, so an assistant reads
exactly the conversations that employee is in and no one else's calendar. `TaskAssignTools._visible`
is the only filter; asking for another employee's DM by id is indistinguishable from asking
for one that doesn't exist, as a real API would be.

**No `state_updates`.** `env_state` carries a live `Workspace`, so posts and board claims
mutate it directly. That deliberately avoids terrarium's commit mechanism — a tool result
containing `state_updates` ends the turn (`terrarium/agents/base.py:371`) — leaving turn
boundaries entirely to `DiscoveryAgent`.

**One stream.** `BaseAgent` rebuilds context from scratch every turn; `install_stream`
shadows `init_context` and `process_tool_calls` on each agent's client so the conversation
persists, each round appending only a short delta. Reasoning is stripped from the persisted
stream and captured from the response for the logs — note this strips both `reasoning` and
`reasoning_content`, where `cluster/patch_vllm_client.py` only covers the latter.

**The uptake ledger.** Every read records which message ids were handed to which agent, and
so does every injected delta. "Did the signal reach them" is answerable regardless of
whether it arrived by history, by search, or by notification.

## Turn and run boundaries

A turn ends when the assistant posts to `#aug-2026-sprint`, when it stops calling tools (a
pass — there is no forcing), or when `max_conversation_steps` is spent. DMs and board claims
are neutral, so an assistant can look things up, claim its ticket and message its manager in
one turn.

A run ends when the board is complete **and** the nominated reporter has DM'd the manager, or
at the round cap. Then each employee asks "so how did it go?" and the reply is that
assistant's summary.

### A pass, and the things that used to look like one

"Stops calling tools" made every *broken* step indistinguishable from a considered decision to
say nothing, and the record showed neither. Two faults were found hiding there:

| verdict | what it is | seen in |
| --- | --- | --- |
| `dropped_call` | the provider failed to parse a tool call the model did emit — `content: null`, no `tool_calls`, and the call's *arguments* glued onto the end of the chain-of-thought | 8 of 87 gpt-oss-120b steps on unpinned OpenRouter routing; it stalled `gptoss120b_unread_priya_s43` by killing all four round-3 turns |
| `truncated` | `finish_reason: "length"` — the whole `max_tokens` budget went into reasoning and the call was never reached | 29 DeepSeek and 2 Kimi-K3 steps across v11/v13, each with a 30k-character CoT cut off mid-word |

`agent.classify_step` separates those from a real pass and `salvage_retries` (default 2, per
turn) re-runs the step, dropping the dud message from the stream first so the retry is a
resample rather than a continuation. A blank step whose reasoning does *not* end in an
arguments blob is left alone — that is what a genuine pass looks like. Every discard lands in
the turn's `discarded_steps` and the run's `discards` summary whether or not a retry was
spent, and the viewer tags the step, so a turn cut short by the provider can be told from one
the model chose to end. `salvage_retries: 0` restores the old behaviour, minus the silence.

Steps also record `provider` and `finish_reason` now. Without them a routing-caused failure
cannot be diagnosed after the fact — which is why the gpt-oss culprit above cannot be named
from its own logs.

### Pin the provider, and pin it on a measurement

`dropped_call` turned out to be an upstream, not a model. Replaying three real failing
contexts from `inf_askA_gptoss_s226` 24 times against each tool-capable OpenRouter endpoint:

    CoreWeave 0/24 · Nebius 0/24 · Together 0/24 · AkashML 0/20 (4x 429)
    Novita 6/24 (25%) · DeepInfra 12/24 (50%)
    Parasail, BaseTen: clean but 429 under load · Cerebras, Groq: HTTP 400

s226 ran pinned to DeepInfra and lost **45% of its steps**; on CoreWeave the same contexts
drop nothing. Two plausible-sounding inferences were wrong along the way and are worth not
repeating: quantization does not predict this (the two bf16 endpoints are the worst and one
of the cleanest), and the fault reproducing on a pinned provider is not evidence that the
model is at fault. `reasoning_effort` and `temperature` were swept over the same contexts and
changed nothing (0/24 at medium/high x 0.7/1.0 on every clean endpoint), so temperature stays
at 0.7 to match the other models.

Unpinned routing is therefore a lottery over parsers, and gpt-oss-120b is served by 20
endpoints — 5 of which do not advertise `tools` at all. Every OpenRouter config here pins
`provider` with `allow_fallbacks: false` and `require_parameters: true`. When adding a model,
measure its endpoints before choosing — `bakeoff.py` is that test, and it replays real
contexts out of a run record rather than a synthetic prompt, because a synthetic four-step
context dropped 1/10 where the real one dropped 12/24.

## Not done

- **The workspace generator.** `demo_workspace.py` is a stand-in with the right shape and
  deliberately thin content. The real one is authored from the scenario and frozen per seed.
- **`hint` and `deception`.** Both now live in the employee's opening ask, and the wording
  doesn't exist yet. The pilot runs the plain ask.
- **`confidentiality` beyond `audience`.** sj3's wording assumed assistants posting as
  assistants on a single channel, which is false here, so only `none` and `audience` are
  written. `prompts.py` raises rather than inventing the rest.
- **Cluster plumbing.** No `.sub` yet, and the job must call `cluster/patch_vllm_client.py`.
- **Judges.** The run record carries reasoning, tool calls, tool results, texts, transcripts
  and the ledger; nothing consumes them yet.
