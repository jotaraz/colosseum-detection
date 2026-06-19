"""Idempotently patch the installed llm_server vLLM client to forward `reasoning_effort`.

The bundled client (llm_server/clients/vllm_client.py) only forwards model/messages/
max_tokens/temperature/tools to the chat-completions payload, dropping everything else.
gpt-oss reasoning effort is controlled by the `reasoning_effort` request field (vLLM
accepts it; see vllm/entrypoints/openai/protocol.py). This inserts a pass-through so a
`reasoning_effort` key in the experiment config's llm.vllm.params reaches vLLM.

Usage (from the job's run.sh, after the venv is active):
    python cluster/patch_vllm_client.py "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py"
Safe to run repeatedly.
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()

if "reasoning_effort" in src:
    print("patch_vllm_client: already patched")
    sys.exit(0)

anchor = (
    '        if params.get("temperature") is not None:\n'
    '            payload["temperature"] = params["temperature"]\n'
)
inject = anchor + (
    '        for _k in ("reasoning_effort", "chat_template_kwargs"):\n'
    '            if params.get(_k) is not None:\n'
    '                payload[_k] = params[_k]\n'
)

if anchor not in src:
    print("patch_vllm_client: ANCHOR NOT FOUND — client layout changed; not patching", file=sys.stderr)
    sys.exit(1)

open(path, "w", encoding="utf-8").write(src.replace(anchor, inject, 1))
print("patch_vllm_client: forwarded reasoning_effort/chat_template_kwargs")
