# Colosseum local-vLLM cluster runs — handoff report

_Last updated: 2026-06-15_

## Goal
Run the **collusion** experiment (JiraTicket, complete topology, n=6, 2 colluders) on the
cluster with four open-source models served **locally via vLLM** (no API spend), one
cluster job per model.

## What exists now

### Config YAMLs (`experiments/collusion/configs/`)
All four are copies of `collusion_jira_complete_n6_c2_regret_models.yaml` with the cloud
models replaced by a single local vLLM model. Same sweep/seeds/output as the original;
runs are separated by model `label`.

| Config | Model (`checkpoint`) | TP size | Port | Notes |
|---|---|---|---|---|
| `..._regret_qwen72b_local.yaml` | `Qwen/Qwen2.5-72B-Instruct` | 4 | 8020 | public |
| `..._regret_llama33_70b_local.yaml` | `meta-llama/Llama-3.3-70B-Instruct` | 4 | 8021 | **gated** (needs HF_TOKEN) |
| `..._regret_gemma3_27b_local.yaml` | `google/gemma-3-27b-it` | 2 | 8022 | **gated**; tool-calling finicky |
| `..._regret_qwen3_27b_local.yaml` | `Qwen/Qwen3.5-27B` | 2 | 8023 | multimodal MoE "thinking" model; needs recent vLLM |

- All set `HF_HOME: /fast/jtaraz/hf_cache` in the YAML `llm.vllm.env` block.
- gemma/qwen3 configs carry commented knobs for tool-call/reasoning parsers if startup rejects auto tool-choice.

### Submission scripts (`cluster/`)
`run_collusion_<model>.sh` — one per model, modeled on `~/code/cluster_stuff/run_task.sh`.
Each: sets env → exports `HF_HOME=/fast/jtaraz/hf_cache` → `cd $PROJECT` → (re)builds venv with
`uv` if missing → activates → runs `python -m experiments.collusion.run --config <config>`.

`PROJECT` is currently `/fast/jtaraz/LIARS/colosseum` in the repo copy; on the cluster it was
edited to `/fast/jtaraz/LIARS/colosseum-detection` (see open issue below).

## Key facts established (verified against Terrarium source)
- **vLLM server is a subprocess** launched with `env = os.environ.copy()` + merged
  `llm.vllm.env` (global) + `models[].env` (per-model). So env reaches vLLM via: shell export,
  or the YAML `env:` blocks.
- **Terrarium does NOT load `.env` in the vLLM runtime** (only the API clients call
  `load_dotenv`). So for all-vLLM runs, `HF_HOME`/`HF_TOKEN` must come from the YAML `env:`
  block or a shell `export` — NOT `.env`.
- `export HF_TOKEN=...` before submitting works (passes through `os.environ.copy()`).
- Models download from HuggingFace Hub on first run into `HF_HOME`; gated models
  (llama, gemma) need license acceptance + `HF_TOKEN`.
- `--download-dir` (spec field `download_dir`) relocates only weights; `HF_HOME` relocates the
  whole HF cache. We use `HF_HOME`.

## UPDATE (2026-06-15, later): venv fixed; new failure = vLLM server race
The hardened script worked: venv now builds (`Installed 212 packages in 38m 50s`,
`vllm==0.12.0`, `terrarium-agents==0.1.1`) and the run starts. Error log:
`/Users/jo/code/cluster_stuff/test_17345240.err`. Three findings:

1. **(non-fatal)** gemma-3 isn't in Terrarium's tool-parser list, so it
   **silently disabled `--enable-auto-tool-choice`** -> agents couldn't tool-call.
2. **(fatal)** `RuntimeError: vLLM server for model 'google_gemma_3_27b_it' exited
   prematurely with code 1`. Root cause = config concurrency race, NOT gemma/OOM:
   the configs had `max_concurrent_runs: 10` + `persistent_server: false`, but all
   runs share ONE served model on ONE port (8022). Concurrent runs launch/tear-down
   servers on the same port -> the loser fails to bind -> exits code 1. (Run #1
   completed, then it crashed — classic race signature.)
3. Real vLLM crash log lives at (relative to project dir):
   `<PROJECT>/logs/vllm/google_gemma_3_27b_it.log` — grab this to confirm a port-bind
   error vs. a gemma load/OOM error.

### Fixes applied to ALL FOUR vLLM configs
- `persistent_server: true` (keep one warm server, no teardown between runs)
- `max_concurrent_runs: 1` (serialize runs; the single server batches internally)
gemma config also: `tool_call_parser: "pythonic"` so tool-calling stays enabled.

### Tool-calling auto-config support (from Terrarium's TOOL_CALLING_RULES)
- **Qwen2.5-72B**: matches `qwen2.5` -> parser `hermes`. Works out of the box. **Best
  model to start with — no tool-calling config needed.**
- **Qwen3.5-27B**: matches `qwen3` (substring of qwen3.5) -> `hermes` + reasoning
  `qwen3`, auto. Should work; verify tool calls since 3.5 is newer than the rules.
- **Llama-3.3-70B**: NO generic llama rule -> would be silently disabled like gemma.
  FIXED: set `tool_call_parser: "llama3_json"` in its config.
- **gemma-3-27b**: not supported -> set `tool_call_parser: "pythonic"` (done).

### Next actions after device switch
0. RECOMMENDED START: submit **qwen2.5-72b** first — cleanest (tool-calling auto,
   public model, no HF_TOKEN). Validates the persistent_server/concurrency fix end-to-end.
1. Re-submit gemma. Expect: one server starts on 8022 and stays up; runs go 1->30
   sequentially (~3 min each, ~90 min total). No more "exited prematurely".
2. If it STILL exits prematurely, read `<PROJECT>/logs/vllm/google_gemma_3_27b_it.log`:
   - "address already in use" / bind error -> a stale vLLM server is up; run
     `pkill -f vllm.entrypoints.openai.api_server` and resubmit.
   - CUDA OOM during weight load -> gemma-27b too big for the 2 GPUs; lower
     `gpu_memory_utilization`, reduce `max_model_len`, or raise GPU count.
     401/gated -> HF_TOKEN missing (gemma is gated).
3. Verify tool calls actually work with `pythonic` parser (check a run's tool_events /
   blackboard output). If malformed, gemma may need a tool chat_template, or gemma is
   just a poor tool-caller — consider dropping it.
4. Once gemma is clean, the same config changes already apply to the other three;
   submit qwen2.5-72b / llama-3.3-70b (set HF_TOKEN, gated) / qwen3.5-27b.

---

## (Resolved) Earlier blocker: gemma job failed on venv
**Diagnosis: it was a venv/setup failure, NOT out-of-memory.**
- Condor log showed `Memory Usage 24 MB` (the ~490 GB figure is the *request*, not usage) and
  `Normal termination (return value 1)` after ~40s.
- Real error: `source $VENV/bin/activate: No such file or directory` → `set -e` → exit 1.
- **Allocating more memory will NOT fix it.**
- Confusing point: `set -e` would normally abort *before* printing `venv exists, activating`
  if a build step failed — yet that line printed with no `activate` present. So either the
  rebuild block was skipped (stale/broken venv) or the build silently produced nothing.
  Most likely the `colosseum-detection` path isn't the real repo / has a broken leftover venv.

### Fix applied
The four `cluster/*.sh` scripts were hardened to fail loudly with the real reason:
- error if `$PROJECT/pyproject.toml` missing or `uv` not on PATH (prints PATH + uv version),
- check `$VENV/bin/python` AND verify `$VENV/bin/activate` exists *before* sourcing,
- removed the misleading unconditional "venv exists" echo,
- `import vllm` confirmation after activation,
- `VIRTUAL_ENV="$VENV" uv sync` to pin the target venv.

## NEXT STEPS (do these after switching devices)

1. **Re-submit the hardened gemma job:** `cluster/run_collusion_gemma3_27b.sh`
   (make sure `PROJECT` points at the real repo, and `export HF_TOKEN=...` first — gemma is gated).

2. **Read the new job output. Look for, in order:**
   - `ERROR: no pyproject.toml in <PROJECT>` → wrong `PROJECT` path. Fix it to the actual repo
     (probably `/fast/jtaraz/LIARS/colosseum`, not `...-detection`). **<- most likely.**
   - `ERROR: 'uv' not found on PATH ...` → `uv` isn't available on the compute node; add it to
     PATH / install it / load the right module.
   - `using uv: ... (uv x.y.z)` then `venv missing or incomplete — (re)building` followed by
     `uv venv` / `uv sync` output → watch this for the real build error (e.g. Python 3.11 not
     found, no network to PyPI, vllm build/compile failure, disk full on scratch/UV_CACHE_DIR).
   - `ERROR: build did not produce .../activate` → the uv build failed; the cause is in the
     uv output just above this line.
   - `OK: vllm <version>` → venv is good; next watch for the **vLLM server startup** and model
     **download into `/fast/jtaraz/hf_cache`** (first run pulls ~54 GB for gemma-27b).
   - gemma-specific: if the vLLM server rejects `--enable-auto-tool-choice` at startup, set
     `tool_call_parser` / `chat_template` in the gemma YAML (commented hints are in the file).
   - Watch for a **gated-repo / 401** error → `HF_TOKEN` missing or license not accepted on HF.

3. **If gemma now runs**, apply the same `PROJECT`/token fixes and submit the other three.

4. **Optional (offered, not yet done):** generate HTCondor `.submit` files with correct
   `request_gpus` (4 for 70B/72B, 2 for 27B) and a sane `request_memory` (~64–96 GB, not 490 GB).

## Open questions to resolve
- Confirm the real repo path on the cluster (`colosseum` vs `colosseum-detection`) — likely the
  root cause of the gemma failure.
- Confirm `uv` and Python 3.11 are available on compute nodes.
- Verify `Qwen/Qwen3.5-27B` runs on the cluster's vLLM version (thinking/MoE model).
