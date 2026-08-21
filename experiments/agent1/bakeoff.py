from __future__ import annotations

"""Which OpenRouter endpoint can actually serve this model's tool calls?

The endpoints behind one model id are not interchangeable. Replaying three real contexts out
of `outputs/v14/inf_askA_gptoss_s226.json` 24 times against each tool-capable endpoint for
`openai/gpt-oss-120b`:

    CoreWeave 0/24 · Nebius 0/24 · Together 0/24 · AkashML 0/20 (4x 429)
    Novita 6/24 (25%) · DeepInfra 12/24 (50%)
    Parasail, BaseTen: clean but 429 under load · Cerebras, Groq: HTTP 400

s226 itself ran pinned to DeepInfra and lost 45% of its steps — the call arriving as an
arguments blob on the end of the chain-of-thought instead of as a `tool_calls` entry, which
`agent.classify_step` calls a `dropped_call`. On CoreWeave the identical contexts drop
nothing. So: measure before pinning, and pin on the measurement. Guessing from quantization
or from a provider's reputation got it wrong twice (the two bf16 endpoints were the worst and
one of the cleanest).

Replay **real** contexts, not a synthetic prompt: a synthetic four-step conversation dropped
1/10 where the real deep ones dropped 12/24. Depth and the full tool set are what provoke it.

    python -m experiments.agent1.bakeoff --model qwen/qwen3.6-35b-a3b \
        --source experiments/agent1/outputs/v14/inf_askA_glm_s208.json

Phase A sweeps every tool-capable endpoint at the settings a run would actually use. Phase B
takes whatever came back clean and sweeps `temperature` and `reasoning_effort` over it. A
trial "drops" when the reply carries no tool calls and no text — the shape the runner used to
record as the assistant choosing to pass.
"""

import argparse
import collections
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


# ------------------------------------------------------------------ what to send
def tool_capable_endpoints(model: str) -> List[str]:
    """Provider names that advertise `tools` for this model, in OpenRouter's own order.

    The ones that don't are not a hypothetical: 5 of gpt-oss-120b's 20 endpoints omit
    `tools`, and unpinned routing can land on them, whereupon the model's call has nowhere
    to go. `require_parameters` in the request refuses them, but they are dropped here too so
    a sweep doesn't spend trials proving it.
    """
    with urllib.request.urlopen(ENDPOINTS_URL.format(model=model)) as fh:
        data = json.load(fh)["data"]
    seen, names = set(), []
    for endpoint in data.get("endpoints") or []:
        name = endpoint.get("provider_name")
        if "tools" in set(endpoint.get("supported_parameters") or []) and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def contexts_from(source: Path, count: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """The deepest turn-start contexts in a run record, one per agent.

    A stream is `system, user, [assistant, tool]*, user, ...`; every user message after the
    first is a turn boundary, so cutting there reproduces exactly what the runner sent when
    that turn opened. Deepest first, because the fault tracks context depth.
    """
    record = json.loads(source.read_text(encoding="utf-8"))
    picks: List[Tuple[int, str, List[Dict[str, Any]]]] = []
    for agent, stream in (record.get("streams") or {}).items():
        starts = [i for i, m in enumerate(stream) if m.get("role") == "user"][1:]
        if starts:
            cut = starts[-1] + 1
            picks.append((cut, f"{agent.lower()}-d{cut}", stream[:cut]))
    picks.sort(reverse=True)
    return {name: messages for _, name, messages in picks[:count]}


def tool_schemas(harness: str = "full") -> List[Dict[str, Any]]:
    """The real tool set, at the harness variant the config under test runs."""
    from experiments.agent1 import tools as _tools
    from experiments.agent1.tools import TaskAssignTools

    _tools.set_harness(harness)
    return TaskAssignTools(None).get_tools("")


# ---------------------------------------------------------------------- the trial
def run_trial(job: Dict[str, Any]) -> Tuple[Tuple, str, float]:
    cell = (job["provider"], job["effort"], job["temperature"])
    body = {
        "model": job["model"],
        "messages": job["messages"],
        "tools": job["tools"],
        "max_tokens": job["max_tokens"],
        "temperature": job["temperature"],
        "provider": {"order": [job["provider"]],
                     "allow_fallbacks": False, "require_parameters": True},
        "usage": {"include": True},
    }
    if job["effort"] != "-":
        body["reasoning"] = {"effort": job["effort"]}
    try:
        reply = requests.post(
            CHAT_URL,
            headers={"Authorization": f"Bearer {job['key']}", "Content-Type": "application/json"},
            data=json.dumps(body), timeout=240,
        )
    except Exception:
        return cell, "transport_error", 0.0
    if not reply.ok:
        return cell, f"http_{reply.status_code}", 0.0
    data = reply.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    cost = float((data.get("usage") or {}).get("cost") or 0.0)
    if message.get("tool_calls"):
        return cell, "call", cost
    text = str(message.get("content") or "").strip()
    if text:
        # `text` is not a failure. At a turn-start context late in a run the right move often
        # IS to write the debrief, and qwen does exactly that on every clean endpoint — 60
        # trials, 0 leaks, replies like "Done. T1: Marcus + Priya, T2: Nadia + Tomas".
        #
        # But qwen emits calls as `<tool_call>{...}</tool_call>` *inside* content, so an
        # endpoint that fails to parse leaks the call here rather than into the reasoning
        # channel the way gpt-oss does — and that shape is invisible to `classify_step`,
        # which treats any non-empty content as a considered pass. Separate verdict, because
        # a leak is a silent corruption of the debrief, the primary analysis surface.
        return cell, ("LEAKED" if _leaked_call(text, job["tools"]) else "text"), cost
    return cell, "DROPPED", cost


def _leaked_call(text: str, tools: List[Dict[str, Any]]) -> bool:
    """Does this "message" actually contain an unparsed tool call?"""
    if "<tool_call>" in text or "</tool_call>" in text:
        return True
    names = [t["function"]["name"] for t in tools]
    return bool(re.search(r"\{\s*\"(name|arguments|tool_name)\"\s*:", text)
                and any(f'"{name}"' in text for name in names))


def sweep(cells, contexts, common, trials, label) -> Tuple[List, float]:
    jobs = [
        {**common, "provider": p, "effort": e, "temperature": t, "messages": messages}
        for (p, e, t) in cells for messages in contexts.values() for _ in range(trials)
    ]
    tally: Dict[Tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    cost = 0.0
    with ThreadPoolExecutor(max_workers=10) as pool:
        for cell, outcome, spent in pool.map(run_trial, jobs):
            tally[cell][outcome] += 1
            cost += spent
    per_cell = trials * len(contexts)
    print(f"\n=== {label} — {trials} trials x {len(contexts)} contexts = {per_cell} per cell")
    print(f"{'provider':16s} {'effort':7s} {'temp':5s} {'drop rate':>11s}   outcomes")
    rows = sorted(tally.items(),
                  key=lambda kv: (kv[1]["DROPPED"] / max(sum(kv[1].values()), 1),
                                  -kv[1]["call"]))
    for (provider, effort, temperature), counts in rows:
        n = sum(counts.values())
        print(f"{provider:16s} {effort:7s} {temperature:<5} "
              f"{counts['DROPPED']}/{n} = {counts['DROPPED'] / n:5.0%}   {dict(counts)}")
    return rows, cost


def usable(rows, limit: int = 3) -> List[str]:
    """Endpoints that lost nothing and were not rate-limited.

    Judged on `DROPPED` and `LEAKED` — the two ways a call goes missing — never on the share
    of replies that were calls. A high `text` share is usually the model correctly writing its
    debrief, and an earlier version of this gate demanded 75% calls and therefore rejected
    five perfectly clean qwen endpoints. The one thing a low call share *can* mean is an
    endpoint that never calls tools at all, so a floor is kept, well below any legitimate mix.
    """
    out = []
    for (provider, _, _), counts in rows:
        n = sum(counts.values())
        errors = sum(v for k, v in counts.items()
                     if k.startswith("http") or k == "transport_error")
        lost = counts["DROPPED"] + counts["LEAKED"]
        if lost == 0 and errors == 0 and counts["call"] >= 0.2 * n:
            out.append(provider)
    return out[:limit]


def main() -> int:
    # Not `__doc__`: the module's prose sits after `from __future__ import annotations`
    # (house style here), which makes it an expression rather than a docstring.
    parser = argparse.ArgumentParser(
        description="Which OpenRouter endpoint can actually serve this model's tool calls?"
    )
    parser.add_argument("--model", required=True, help="OpenRouter model id")
    parser.add_argument("--source", required=True,
                        help="A run record whose streams supply the replay contexts")
    parser.add_argument("--trials", type=int, default=8, help="Trials per context per cell")
    parser.add_argument("--contexts", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Phase A temperature — match the config under test")
    parser.add_argument("--effort", default="-",
                        help="Phase A reasoning_effort, or '-' to send none (the default for "
                             "every model here except gpt-oss)")
    parser.add_argument("--harness", default="full", choices=("full", "paged"))
    parser.add_argument("--providers", default="",
                        help="Comma-separated override; default is every tool-capable endpoint")
    args = parser.parse_args()

    load_dotenv(str(Path(__file__).resolve().parents[2] / ".env"), override=True)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    providers = ([p.strip() for p in args.providers.split(",") if p.strip()]
                 or tool_capable_endpoints(args.model))
    contexts = contexts_from(Path(args.source), args.contexts)
    tools = tool_schemas(args.harness)
    common = {"model": args.model, "tools": tools, "key": key, "max_tokens": args.max_tokens}

    print(f"model    {args.model}")
    print(f"source   {args.source}")
    print(f"contexts {', '.join(f'{k} ({len(v)} msgs)' for k, v in contexts.items())}")
    print(f"tools    {len(tools)} at harness={args.harness}")
    print(f"providers ({len(providers)}) {', '.join(providers)}")

    total = 0.0
    rows, cost = sweep([(p, args.effort, args.temperature) for p in providers],
                       contexts, common, args.trials,
                       "phase A: endpoints at the config's settings")
    total += cost

    clean = usable(rows)
    if not clean:
        print("\nNothing came back clean — no phase B. Read the phase A outcomes: a column of "
              "http_4xx is a request the endpoint rejects, DROPPED is a parser losing calls.")
        print(f"\nbake-off cost ${total:.4f}")
        return 1

    print(f"\ncarrying forward: {clean}")
    efforts = [e for e in ("-", "medium", "high") if e != args.effort]
    knobs = [(p, e, t) for p in clean
             for (e, t) in [(args.effort, 1.0)] + [(e, args.temperature) for e in efforts]]
    _, cost = sweep(knobs, contexts, common, args.trials,
                    "phase B: temperature and reasoning_effort")
    total += cost
    print(f"\nbake-off cost ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
