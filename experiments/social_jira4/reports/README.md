# reports — standalone summaries of a run set

Self-contained HTML pages that summarise a set of social_jira4 runs for reading or presenting.
Unlike `viewer/`, nothing here is a server and nothing reads `outputs/` at view time: every number,
figure and transcript is **baked into the file**, so the page opens by double-click, offline, on a
machine that has neither the run dirs (`outputs/` is gitignored) nor a Python environment.

| file | covers |
|---|---|
| `v3_runs.html` | the six v3-era runs — 3 targets × {cold, warm-start}, run sha `57673ba` |
| `v4_dspro_fabrication_qualifying.jsonl` | index of the 52 dspro-prompted, fabrication-gated `v4*` steps that scored > 0, + their meta-judge verdicts |
| `v4_dspro_meta_judge_verdicts.jsonl` | one row per (prompt, question, seat) behind those verdicts — the sweep's durable output |

## `v4_dspro_fabrication_qualifying.jsonl`

An **index, not a bake** — unlike the HTML pages it holds pointers into `outputs/`, so it is only
useful on a machine that has the run dirs. One row per prompt-pair, sorted by score descending:

`step_path` (the step JSON — rendered prompt lives at `meta.rendered.{system,user}`), `run_dir`,
`rollouts_dir` (`runs/stepNNN`, `null` where the rollouts were pruned), `step`/`opt_step`, `score`
and `score_per_seed`, `n_qualifying_turns` / `n_fabrication_turns`, `prompt_sha`, `gate` +
`gate_prompt`, `prompter`, `target_per_seed`, `prompter_rationale`.

Three fields join `score_per_seed` to `target_per_seed`, which the run dirs otherwise keep apart —
the per-seed scores are a bare list and the seed→model assignment a string in `metadata.json`:

- `seed_breakdown` — the merge itself: one entry per seed with `seed`, `model`, `score`,
  `n_qualifying`, `n_distinct`.
- `models_that_lied` — the targets that scored > 0 on at least one seed of this prompt.
- `seeds_lied_per_model` — `{model: "lied/total"}`, so a target that lied on 1 of 3 seeds is not
  read as equivalent to one that lied on all 3.

Across the 52: 21 prompts broke gpt-oss-120b only, 11 qwen3.6-35b-a3b only, 20 both.

Selection: `outputs/v4*` steps that reached rollout (non-empty `objective`), with
`metadata.models.prompter == deepseek/deepseek-v4-pro`, `meta.raw.question == "fabrication"`, and
`score > 0` — 52 of the 252 executed `v4*` pairs. Note `dspro` here names the *prompter*; it is also
the meta-gate and turn judge in the `gpt54` runs, which this filter excludes.

### `meta_judges` — the offline panel folded into each row

`meta_judge_sweep.py` (submitted as `cluster/mj_sweep_v4.sub`) asks five questions about each of the
52 prompts, of four models each: `meta_judges[question][seat] = {rationale, answer, confidence,
confidence_field, model, provider, provider_pin, usage_cost_usd}`. Questions are `realism`,
`fabrication`, `admissibility`, `forecast`, `intent` — coldread is excluded, its envelope is free
text by design. `forecast` reports `probability` rather than `confidence` (a forecast of the
target's behaviour, not a confidence in a verdict), so read `confidence_field` before comparing
numbers across questions.

Three things about this panel that a bare reading of the numbers will get wrong:

- **The envelope differs from the in-loop gate's.** The questions are the `*_RATIONALE_FIRST.md`
  copies, which ask for `rationale` *before* `answer`/`confidence` so the judge reasons before it
  commits. That is a change to the instrument. These verdicts do **not** pool with
  `v3_meta_judge_verdicts.jsonl` or with the `meta` block of a step file, both answer-first.
- **The judged text is byte-identical to the gate's, and checked.** Each row's `step_path` is opened
  and `meta.rendered.{system,user}` judged verbatim in the `system_user` view; all 52 re-hash to the
  recorded `prompt_sha`, re-verified per task, hard error on mismatch. So the prompts are the same;
  only the question envelope and the panel changed.
- **Two seats are confounded, knowingly.** `dspro` wrote all 52 prompts *and* ran the fabrication
  gate that admitted them — its column is close to the author re-grading itself under a modified
  rubric. `dsflash` shares a family with models used as assistants elsewhere in this project. Both
  were included deliberately; neither is a clean outside view.

**The `sonnet5` column carries a caveat the other three do not.** During the sweep (2026-08-05
morning) ~50% of its calls returned HTTP 200 with empty content — 517 model calls for 260 tasks —
against 0% for `dspro`/`gpt54` and 2.6% for `dsflash`. Retries recovered all of them, so the column
is complete, but 80 of its 260 verdicts (31%) were produced under a *modified* prompt: `_call_json`
appends a "your previous reply was EMPTY … answer NOW, concisely" nudge before retrying. Median
rationale under that nudge is 27% shorter (411 vs 566 chars); the yes-rate difference is small
(41% vs 36%) and inconsistent across questions. Rows carry `attempt_log`, so the affected verdicts
are identifiable rather than inferred — treat a row with a non-empty `attempt_log` as elicited under
a different instruction than one without.

The cause was **not** established. An A/B nine hours later — the same 10 prompts x 5 questions,
same pin, judged from a compute node (proxy egress) and from a laptop (direct egress) — came back
50/50 clean on *both* paths, so the proxy hypothesis is unsupported and the effect did not
reproduce; a transient upstream condition during the morning window is the remaining explanation.
That A/B did establish two things worth keeping: sonnet's verdicts are stable run-to-run (48/50
agree across the nine-hour gap, and both flips were on un-nudged morning verdicts, so the nudge is
not implicated), and `checks_balances._call_json` now records every discarded attempt with its own
usage, so a recurrence will be diagnosable instead of invisible.

Pins (`provider_pin`) make the run reproducible but not a replay: the in-loop gate ran `dspro`
*unpinned*, and StreamLake serves fp8 / DeepInfra fp4, so `fabrication@dspro` is a re-ask on a
possibly different serving stack. The sidecar's `usage.provider_name` records which upstream
actually served each call, so the pin is checkable rather than asserted. The four seats are not
matched on sampling: Claude Sonnet 5 removed the sampling parameters (any non-default temperature is
a 400), and the Azure caller has no effort knob — a property of the models, not an oversight.

## `v4_dspro_meta_judge_verdicts.jsonl`

The sweep's append-only sidecar, one row per (prompt, question, seat), flushed per row so an
interrupted run keeps what it paid for. Rerunning skips what already parsed; `--merge` rewrites the
`meta_judges` block from this file. It alone keeps each judge's chain-of-thought (`reasoning`) and
raw reply (`raw`) — the merged file keeps the verdict only, or the 52 rows would be mostly
transcript.

## `v3_runs.html`

Walks the loop and then reports what those runs produced: the six stages of one optimizer step;
the triple AND-gate the objective is built on; per-step score trajectories per target (with
gate-refused candidates on a rail under the axis rather than plotted as zeros); where the 14 refused
candidates died; the specificity histogram; the strategy-label mix **by optimizer step**; and four
elicited fabrications in full, each with the target's chain-of-thought and all three judges'
verdicts.

Computed from `outputs/<run>/steps/step_NNN.json` (`step_schema` 3). Because those step files carry
the target's reasoning verbatim, this page **embeds run content that `outputs/` itself does not
track** — that is deliberate (it is what makes the file portable), but it does mean transcripts and
chains-of-thought from these runs now live in version control.

Two provenance notes the page cannot show on its own:

- All eight v3-era runs record `git_sha` `57673ba`, **including the `_v3fix` reruns** — the
  `reasoning_effort: medium` change those reruns depend on was in the cluster's working tree but not
  yet committed when they launched (it landed locally later, as `a2c1ec0`). The recorded sha
  therefore cannot distinguish `cluster_dsflash_r4_v3` from `cluster_dsflash_r4_v3fix`; the run dir
  name is the only discriminator.
- The page covers the `_v3fix` deepseek reruns only. The two pre-fix deepseek runs are excluded
  throughout: ~75% of their judged turns arrived with no chain-of-thought, so the intent-based
  category critic scored them off "(no reasoning captured)" and their numbers are not comparable.

### Regenerating or editing

The page is generated, not hand-maintained: a shell (markup + CSS + chart code) with a
`/*__DATA__*/` marker, and a JSON payload extracted from the step files, spliced together. Neither
input is committed — the built artifact is the deliverable. To change a number, re-extract from
`outputs/`; to change the layout, edit the `<style>`/`<script>` in place, which is safe since the
file has no build step and no external dependencies.
