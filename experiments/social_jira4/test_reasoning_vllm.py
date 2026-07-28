"""Brief probe: does Qwen3.6-35B-A3B served by local vLLM return chain-of-thought in a way the
pipeline's OpenRouterClient captures?

The client's ``_capture_reasoning`` reads ``message.get("reasoning") or message.get("reasoning_content")``.
OpenRouter puts CoT in ``reasoning``; vLLM (with ``--reasoning-parser qwen3``) puts it in
``reasoning_content``. This confirms the fallback actually fires — on a plain thinking turn AND on a
tool-call turn (the realistic agent path, where reasoning must survive alongside ``tool_calls``).

Run by cluster/test_reasoning_vllm.sh once vLLM is up on 127.0.0.1:8000. NOT the full pipeline.
"""

import sys

sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.social_jira2.openrouter_client import OpenRouterClient  # noqa: E402

MODEL = "Qwen/Qwen3.6-35B-A3B"
BASE = "http://127.0.0.1:8000/v1"

client = OpenRouterClient(base_url=BASE, api_key="local", request_timeout=300, total_timeout=360)


def report(tag: str, data: dict, resp_str: str, steps: list) -> bool:
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    rc, rr = msg.get("reasoning_content"), msg.get("reasoning")
    captured = (steps[-1].get("reasoning_content") if steps else None) or ""
    print(f"\n===== {tag} =====")
    print("  raw message keys           :", sorted(msg.keys()))
    print(f"  message.reasoning_content  : present={bool(rc)} len={len(rc or '')}")
    print(f"  message.reasoning          : present={bool(rr)} len={len(rr or '')}")
    print(f"  tool_calls present         : {bool(msg.get('tool_calls'))}")
    print(f"  --> CAPTURED by client     : present={bool(captured)} len={len(captured)}")
    if captured:
        print("      reasoning[:220]:", repr(captured[:220]))
    print("  content[:140]:", repr((msg.get("content") or "")[:140]))
    return bool(captured)


results = {}

# --- Test A: plain thinking turn --------------------------------------------------------------
try:
    msgs = OpenRouterClient.init_context(
        "You are a careful reasoner. Think step by step, then give a short answer.",
        "A room is free only Tue 14:00-16:00 and Wed 10:00-11:00. A 90-minute meeting is needed. "
        "Which single slot fits? Reason first, then state it.",
    )
    data, s = client.generate_response(
        msgs, {"model": MODEL, "max_completion_tokens": 3000, "temperature": 0.7}
    )
    results["A_plain"] = report("A: plain reasoning turn", data, s, client._reasoning_steps)
except Exception as e:  # noqa: BLE001
    print("\n===== A: plain reasoning turn =====\n  ERROR:", type(e).__name__, str(e)[:300])
    results["A_plain"] = False

# --- Test B: tool-call turn (realistic agent path) --------------------------------------------
try:
    tool = {
        "type": "function",
        "function": {
            "name": "propose_pairing",
            "description": "Submit a proposed task pairing with a public justification.",
            "parameters": {
                "type": "object",
                "properties": {"justification": {"type": "string", "description": "public reason"}},
                "required": ["justification"],
            },
        },
    }
    msgsB = OpenRouterClient.init_context(
        "You are an assistant negotiating a team task allocation. Think step by step about your "
        "reasoning, THEN call the propose_pairing tool with a justification.",
        "Choose a justification for some pairing and call propose_pairing. Reason before the call.",
    )
    dataB, sB = client.generate_response(
        msgsB,
        {
            "model": MODEL,
            "max_completion_tokens": 3000,
            "temperature": 0.7,
            "tools": [tool],
            "tool_choice": "auto",
        },
    )
    results["B_tool"] = report("B: tool-call turn", dataB, sB, client._reasoning_steps)
except Exception as e:  # noqa: BLE001
    print("\n===== B: tool-call turn =====\n  ERROR:", type(e).__name__, str(e)[:300])
    results["B_tool"] = False

# --- verdict ----------------------------------------------------------------------------------
print("\n===== SUMMARY =====")
for k, v in results.items():
    print(f"  {k}: {'reasoning captured' if v else 'NO reasoning'}")
ok = all(results.values())
some = any(results.values())
print("  RESULT:", "PASS" if ok else ("PARTIAL" if some else "FAIL"))
sys.exit(0 if ok else (2 if some else 1))
