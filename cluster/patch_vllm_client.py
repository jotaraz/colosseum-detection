"""Idempotently patch the installed llm_server vLLM client.

Two independent patches (each applied only if not already present):

1. Forward extra request fields to the chat-completions payload. The bundled client only
   forwards model/messages/max_tokens/temperature/tools, dropping everything else. We add:
     * ``reasoning_effort`` / ``chat_template_kwargs`` — gpt-oss CoT depth.
     * ``tool_choice`` — lets a turn force a specific function call.

2. Drop ``reasoning_content`` from the assistant message before it is appended to the
   conversation context in ``process_tool_calls``. The bundled client does
   ``context.append(message)`` with the FULL message, so for reasoning models (GLM-4.7,
   Qwen3.6, DeepSeek-R1) the multi-thousand-token reasoning is re-fed on every subsequent
   tool-call step, blowing past max_model_len within 2 steps (400 BadRequest: input_tokens).
   Reasoning is not meant to persist across turns, so we strip it from the re-sent history
   (the per-turn reasoning itself is unaffected).

Usage (from the job's run.sh, after the venv is active):
    python cluster/patch_vllm_client.py "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py"
Safe to run repeatedly.
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
changed = []

# --- Patch 1: forward reasoning_effort / chat_template_kwargs / tool_choice ---
if "tool_choice" not in src:
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
        print("patch_vllm_client: PATCH1 ANCHOR NOT FOUND — client layout changed", file=sys.stderr)
        sys.exit(1)
    src = src.replace(anchor, inject, 1)
    changed.append("forward reasoning_effort/chat_template_kwargs/tool_choice")

# --- Patch 2: strip reasoning_content from re-fed conversation history ---
if 'reasoning_content' not in src:
    anchor2 = (
        '        message = choices[0].get("message") or {}\n'
        '        context.append(message)\n'
    )
    inject2 = (
        '        message = choices[0].get("message") or {}\n'
        '        if isinstance(message, dict):\n'
        '            message.pop("reasoning_content", None)  # do not re-feed CoT into history (bloats context across tool-call steps)\n'
        '        context.append(message)\n'
    )
    if anchor2 not in src:
        print("patch_vllm_client: PATCH2 ANCHOR NOT FOUND — client layout changed", file=sys.stderr)
        sys.exit(1)
    src = src.replace(anchor2, inject2, 1)
    changed.append("strip reasoning_content from re-fed history")

if changed:
    open(path, "w", encoding="utf-8").write(src)
    print("patch_vllm_client: applied -> " + "; ".join(changed))
else:
    print("patch_vllm_client: already patched")
