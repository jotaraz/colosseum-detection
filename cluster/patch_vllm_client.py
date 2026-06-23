"""Idempotently patch the installed llm_server vLLM client to forward extra request fields.

The bundled client (llm_server/clients/vllm_client.py) only forwards model/messages/
max_tokens/temperature/tools to the chat-completions payload, dropping everything else.
We pass through:
  * ``reasoning_effort`` / ``chat_template_kwargs`` — gpt-oss CoT depth (vLLM accepts these;
    see vllm/entrypoints/openai/protocol.py).
  * ``tool_choice`` — lets a turn force a specific function call (social_jira1 forces
    ``post_message`` during planning so each turn posts exactly one message).

Usage (from the job's run.sh, after the venv is active):
    python cluster/patch_vllm_client.py "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py"
Safe to run repeatedly.
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()

# Marker covers the full current key set: an older patch (reasoning_effort only, without
# tool_choice) does NOT match, so it gets re-patched to add tool_choice forwarding.
if "tool_choice" in src:
    print("patch_vllm_client: already patched")
    sys.exit(0)

anchor = (
    '        if params.get("temperature") is not None:\n'
    '            payload["temperature"] = params["temperature"]\n'
)
inject = anchor + (
    '        for _k in ("reasoning_effort", "chat_template_kwargs", "tool_choice"):\n'
    '            if params.get(_k) is not None:\n'
    '                payload[_k] = params[_k]\n'
)

if anchor not in src:
    print("patch_vllm_client: ANCHOR NOT FOUND — client layout changed; not patching", file=sys.stderr)
    sys.exit(1)

open(path, "w", encoding="utf-8").write(src.replace(anchor, inject, 1))
print("patch_vllm_client: forwarded reasoning_effort/chat_template_kwargs/tool_choice")
