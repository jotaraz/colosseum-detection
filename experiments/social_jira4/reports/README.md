# reports — standalone summaries of a run set

Self-contained HTML pages that summarise a set of social_jira4 runs for reading or presenting.
Unlike `viewer/`, nothing here is a server and nothing reads `outputs/` at view time: every number,
figure and transcript is **baked into the file**, so the page opens by double-click, offline, on a
machine that has neither the run dirs (`outputs/` is gitignored) nor a Python environment.

| file | covers |
|---|---|
| `v3_runs.html` | the six v3-era runs — 3 targets × {cold, warm-start}, run sha `57673ba` |

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
