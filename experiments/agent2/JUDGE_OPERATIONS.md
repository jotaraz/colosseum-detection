# Judge operations — OpenRouter routing, the 2026-08-21 stall, and provider pinning

Operational findings from running the jv7 judge fleet (deepseek-v4-flash-0731 via OpenRouter)
at scale. Written down because the failure mode is invisible in any single log line and cost an
evening to diagnose.

## The finding in one paragraph

OpenRouter routes every call to one of ~30 upstream providers serving this model. **Which
upstream you get is the dominant variable in judge latency** — bigger than prompt size, model
under test, our concurrency, or anything else we control. On good days the router concentrated
our traffic on a few fast backends and turns completed in 60–125s; on the evening of 21 Aug it
scattered calls across the long tail (14 different providers in 31 calls, including two the
endpoint list marks deranked) and the same workload ran at a ~500s median with hangs up to 70
minutes. Pinning to a known-good provider restored — exceeded — good-day speed instantly:
**median 16s/turn, p90 36s** (vs ~500s unpinned the same hour).

## Evidence

* Every stored verdict records its upstream: `turns[].judge_*._meta.usage.provider_name`.
  Tally by day:
  - 19 Aug (clean, 911 calls): GMICloud 33%, Sail Research 23%, Novita 22%
  - 20 Aug (clean, 2176 calls): GMICloud 24%, Phala 20%, Novita 12%
  - 21 Aug morning (clean, 1170 calls): GMICloud 51%, DeepInfra 31%
  - 21 Aug evening (stalling, 31 calls): near-uniform scatter across 14 providers
* Pinned probes during the stall, real 80k-char judge prompt: GMICloud 75s, DeepInfra 140s —
  while unpinned calls were hanging past the 600s cap. So the workhorses were healthy; the
  router just wasn't choosing them.
* Failure shape under scatter: no 429s, no errors — connections accepted, responses queued
  server-side. Big prompts (v17's 85k-char Priya turns) crossed our 600s-per-attempt cap and
  died (all 4 failures that evening were Priya turns); small prompts squeaked under and merely
  crawled. A slow pool therefore *selectively corrupts the heaviest, most interesting turns*.

## Recipe

* `category2_over_agent1.py --pin-provider GMICloud` → sends
  `provider: {order: ["GMICloud"], allow_fallbacks: false}`. GMICloud is the evidence-backed
  default: largest share of every clean day on record.
* Before pinning, spend one minute probing: a tiny call plus one realistic judge prompt,
  pinned. A router avoiding a backend *can* mean that backend is saturated — check, don't
  assume (it wasn't, this time).
* With fallbacks off, a degraded pin fails fast instead of silently rerouting. Failed turns are
  recorded per-turn (`n_failed_turns`) and re-judging is idempotent, so fail-fast is the right
  trade.
* Retry policy context: the OpenRouter client abandons an attempt at 600s wall-clock
  (`total_timeout`) and retries up to 7× — a wedged call burns ~70 min before surfacing as
  `ERROR turn ... FAILED`. Under a pin, those retries stop being fresh routing lottery draws.
* Precision footnote: providers serve different quantizations (GMICloud fp8, Morph bf16, many
  fp4). Unpinned judging therefore also mixes quantizations *within* a replicate set — pinning
  removes a small, uncontrolled source of verdict variance, which matters for test–retest runs.

## Symptoms checklist (is it happening again?)

1. Turn-completion gaps: median >300s, or several runs frozen with live sockets and no errors.
2. `provider_name` scatter in fresh verdicts (or in-flight `_meta`) across many unfamiliar hosts.
3. No 429s anywhere — this failure mode never rate-limits, it just doesn't answer.
4. Confirm with a pinned probe; if the pin is fast, relaunch pinned. Sweeps are idempotent
   (`<stem>.category2_<jv>_<model>[_rN].json` written only on completion; the queue builder
   skips existing files), so killing and relaunching loses at most the in-flight runs' turns.
