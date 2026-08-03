"""Start the two sj4 target servers exactly as the loop does, and report what actually happens.

The v4c batch lost every qwen rollout in four of six runs to
``vLLM server for model 'qwen_qwen3_6_35b_a3b' exited prematurely with code 1``, and the cause was
never visible: the vendored ``llm_server`` runtime writes its server log to ``logs/vllm/<id>.log``
relative to the CWD with mode "w", and all jobs shared one CWD, so the crashed server's log was
always overwritten by a healthy one before anyone could read it.

This runs the SAME code path (``build_vllm_runtime`` -> ``ensure_server``) from a private working
directory, so each server's log survives, and on failure prints the tail of that log — which is the
thing nobody has seen yet. Then it sends one real chat completion to each server that came up, since
"ready" and "usable" are not the same claim.

    python diag_vllm.py <config.yaml>

Exit code 0 means both servers came up AND answered; anything else means the report above it says
what broke.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path


def tail(path: Path, n_bytes: int = 12000) -> str:
    try:
        data = path.read_text(errors="replace")
    except OSError as exc:
        return f"(could not read {path}: {exc})"
    return data[-n_bytes:] if len(data) > n_bytes else data


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    project = "/fast/jtaraz/LIARS/colosseum-detection"
    if project not in sys.path:
        sys.path.insert(0, project)

    from experiments.common.run_utils import load_yaml
    from terrarium.utils import build_vllm_runtime

    cfg = load_yaml(argv[1])
    print(f"config: {argv[1]}")
    print(f"cwd:    {Path.cwd()}  (server logs -> ./logs/vllm/)")
    print(f"visible GPUs (job): {__import__('os').environ.get('CUDA_VISIBLE_DEVICES')}\n")

    failures = 0
    for entry in cfg.get("llm_models") or []:
        label = entry.get("label")
        llm = entry.get("llm") or {}
        runtime = build_vllm_runtime(llm)
        spec = runtime.global_config.get_model_for_agent("diag")
        print(f"=== {label}  id={spec.id}  port={spec.port}  tp={spec.tensor_parallel_size}  "
              f"gpus={(spec.env or {}).get('CUDA_VISIBLE_DEVICES')}  "
              f"max_model_len={spec.max_model_len}")
        log_path = Path(runtime.describe_log_path())
        t0 = time.time()
        try:
            runtime.server_manager.ensure_server(spec)
        except Exception as exc:  # noqa: BLE001 — the whole point is to report it
            failures += 1
            print(f"--- FAILED after {time.time() - t0:.0f}s: {type(exc).__name__}: {exc}")
            print(f"--- tail of {log_path} ---")
            print(tail(log_path))
            print("--- end of log ---\n")
            continue
        print(f"--- ready in {time.time() - t0:.0f}s; sending one completion")
        try:
            import requests
            r = requests.post(
                f"http://127.0.0.1:{spec.port}/v1/chat/completions",
                json={"model": spec.served_name(),
                      "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                      "max_tokens": 16},
                timeout=300,
            )
            body = r.json()
            content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
            print(f"--- HTTP {r.status_code}, reply={content!r}\n")
            if r.status_code != 200:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"--- COMPLETION FAILED: {type(exc).__name__}: {exc}")
            print(traceback.format_exc())
            print(f"--- tail of {log_path} ---\n{tail(log_path)}\n--- end of log ---\n")

    print(f"RESULT: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
