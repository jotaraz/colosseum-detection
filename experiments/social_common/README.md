# social_common

Shared, experiment-agnostic engines for exploring judge results across the
`social_*` experiments. Two complementary tools sit on the same `Adapter`:

- **`phenomena_view.py`** — *counts* phenomena. A static, self-contained HTML
  page: a phenomenon × assistant-type table you can split, filter, and drill
  into. Answers **"how often do we get which phenomena?"**
- **`phenomena_turns.py`** — *reads* phenomena. A small Flask app: pick a filter
  subset and phenomena, and each phenomenon becomes a column of the actual
  flagged turns, each with its message, spans, CoT, and full rollout on demand.
  The inverse of the viewer — the evidence behind the numbers.

Neither is meant to be run directly. An experiment describes its axes /
taxonomy / default dirs **once** in an `Adapter`, and both tools consume it. The
`social_jira2` and `social_jira3` `phenomena_view.py` / `phenomena_turns.py`
modules are the thin adapters you actually launch.

## Quick start

Run from the **repo root** (the modules are imported as
`experiments.social_common.*`). `phenomena_turns` needs Flask; if the active
interpreter lacks it, the module auto-re-execs into the repo `.venv`, so
activating the venv first is optional but avoids surprises.

```bash
cd /path/to/colosseum-detection

# ---- viewer (static HTML): build + open the aggregate table ----
python -m experiments.social_jira3.phenomena_view --open
python -m experiments.social_jira2.phenomena_view --open

# ---- turns (Flask): serve the per-turn evidence reader ----
python -m experiments.social_jira3.phenomena_turns      # then open http://127.0.0.1:5004
python -m experiments.social_jira2.phenomena_turns      # then open http://127.0.0.1:5003
```

With no positional args, each adapter scans **its own standard output set**
(`social_jira3` globs every `outputs/social_jira3_c2p2_*_conflict_quit23_v5_confsweep*`
dir; `social_jira2` scans one merged conflict dir). Pass explicit dirs to
override:

```bash
# scan specific run dirs (each is walked recursively for judge_results.json)
python -m experiments.social_jira3.phenomena_view outputs/some_run_A outputs/some_run_B --open
python -m experiments.social_jira3.phenomena_turns outputs/some_run_A --port 5010
```

A "root" may be a directory (walked recursively for `judge_results.json`) or a
single `judge_results.json` file.

## `phenomena_view.py` — the counting table

Builds one self-contained HTML file (no server) with all run data inlined, so
you can copy it anywhere and it still works.

**CLI**

```
python -m experiments.<exp>.phenomena_view [<root> ...] [--out FILE] [--open]
```

- `<root> ...` — dirs / files to scan. Default: the experiment's standard set.
- `--out FILE` — output path. Default: `phenomena_view.html` inside the single
  root, or `outputs/<exp>_phenomena_view.html` for a multi-root scan.
- `--open` — open the result in a browser.

**What you see**

- **Rows** = phenomena (in the taxonomy's canonical order; L2 phenomena tinted,
  observed-but-unlisted names appended so nothing is hidden).
- **Columns** = *assistant types* — the tuple of the split axes. Each column
  shows its run and turn counts.
- **Left sidebar** — one box per axis. Tick **split into columns** to break that
  axis out into separate columns, or leave it collapsed to sum over it. Tick
  individual values (with `all` / `none` shortcuts) to filter runs in/out. Axes
  that actually vary are split by default; constant ones are collapsed.
- **Metric toggle** (top bar):
  - **per-turn rate** — flags / turns (`--rollout` off in `phenomena_hist.py`).
  - **per-run "ever"** — fraction of runs with ≥1 flag (`--rollout` on).
  - **raw flags** — absolute count.
- **hide empty rows** — drop phenomena with no flags in view.
- **include incomplete runs** — by default a crashed/truncated run (its sibling
  `metrics.json` `status != "completed"`) is excluded from **both** numerator and
  denominator; tick to fold them back in.
- **phenomena chips** — show/hide individual rows.
- **Click any cell** → drill-down panel listing every flagged turn behind that
  number: run id, turn / agent / phase, the flagged spans, and the judge's note.

## `phenomena_turns.py` — the per-turn evidence reader

A small Flask app (like `viewer/viewer.py`). The flagged-turn **index** — cheap,
built from `judge_results.json` + L2 files — is assembled once at startup and
sent to the client, which does all filtering and column-building. The heavy
per-run **transcripts** are fetched lazily only when you click "show more" or
"CoT".

**CLI**

```
python -m experiments.<exp>.phenomena_turns [<root> ...] [--port N] [--host H]
```

- `<root> ...` — dirs to scan. Default: the experiment's standard set.
- `--port` — default 5003 (jira2) / 5004 (jira3).
- `--host` — default `127.0.0.1`.

**What you see**

- **Left sidebar** — the same per-axis value filters + `include incomplete runs`.
- **Column chips** (top bar) — click phenomena to add/remove them as columns;
  each chip shows a short code and the total flagged-turn count.
- **Cards** — each column lists its flagged turns (capped at 50 with a "load
  more" button). A card shows the setting tuple, who spoke, badges for the
  *other* phenomena flagged on the same turn, and the **full assistant message**
  with the flagged spans highlighted in place. Spans that came from the model's
  reasoning (not the public message) are listed separately and revealed via the
  **CoT** button.
- **CoT** — lazily loads and highlights that turn's chain-of-thought.
- **show more ▸** — opens the whole rollout transcript in a side pane
  (response + tool actions + optional CoT), scrolled to and highlighting the
  flagged turn. A "show CoT" switch in the pane toggles reasoning inline.

> **turn indices:** the judge's `turn_index` is its own counter over judged
> (planning) turns and does **not** line up with `agent_turns.json`, which
> interleaves planning / preliminary_vote / execution / summary. The reader
> keys turns by `(agent, phase, round)` identity and carries a separate
> `tx_index` for transcript highlighting/scrolling — so cards label with the
> judge's number but jump to the right spot in the rollout.

## Data model & completion gating

- A **run** is one judged scenario leaf (one `judge_results.json`). Axis values
  are read straight from the JSON **body** (`model_label`, `confidentiality`,
  `hint`, …), not parsed from path names — so both tools agree with
  `aggregate_judgements.py` / `phenomena_hist.py`.
- One taxonomy entry present on a turn == **one flag**; L2 confirmation passes
  (sibling `judge_l2_*.json` with `present == True`) are folded in as their own
  phenomena, one flag per confirmed turn.
- **Completion gating** matches `phenomena_hist.py`: a run whose sibling
  `metrics.json` reports `status != "completed"` is dropped from numerator and
  denominator unless "include incomplete" is on. A missing/unreadable
  `metrics.json` is treated as completed.
- **Extra roots** (optional): an adapter may declare `extra_roots` (e.g.
  `social_jira3`'s v6 qwen seeds 3–6). These are loaded, tagged, and toggled
  in/out live via a top-right switch (default off). They are only auto-added on a
  **default** scan — passing explicit roots ignores them.

## Adding a new experiment

Create `experiments/<exp>/phenomena_view.py` and `phenomena_turns.py` as thin
adapters. Only the `Adapter` differs; the HTML/JS/engine is shared.

```python
# experiments/<exp>/phenomena_view.py
from experiments.social_common.phenomena_view import Adapter, run_cli

ADAPTER = Adapter(
    key="<exp>",
    title="<exp> — judge phenomena",
    here=HERE,
    dimensions=[("model_label", "model"), ("confidentiality", "conf"), ...],  # (body field, label)
    taxonomy=lambda: [...],          # canonical phenomenon order (may return [])
    default_roots=lambda: [...],     # dirs scanned when no root arg is given
    # optional:
    l2_map={"judge_l2_hallucination.json": "L2 Hallucination"},
    derive={"model_label": _short_alias, "sample": _sample_from_dir},  # override a body read
    extra_roots=[...], extra_label="include ...",
)

if __name__ == "__main__":
    import sys; raise SystemExit(run_cli(ADAPTER, sys.argv[1:]))
```

```python
# experiments/<exp>/phenomena_turns.py
from experiments.social_common.phenomena_turns import run_server
from experiments.<exp>.phenomena_view import ADAPTER

if __name__ == "__main__":
    run_server(ADAPTER, default_port=50XX)
```

`Adapter` fields:

| field | purpose |
|---|---|
| `key` | short id used in titles / output filenames |
| `title` | HTML `<title>` / header |
| `here` | adapter dir (for the default `outputs/` output path) |
| `dimensions` | `(json_body_field, display_label)` split/filter axes |
| `taxonomy` | callable → canonical phenomenon order |
| `default_roots` | callable → dirs scanned with no root arg |
| `l2_map` | `{l2_filename: display_name}` level-2 passes to fold in |
| `results_name` | results filename (default `judge_results.json`) |
| `derive` | `{field: fn(summary, judge_path)}` to override a body read |
| `extra_roots` / `extra_label` | optional roots + the switch label |
