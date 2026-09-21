"""agent5 opencode homes: two MCP connectors (slack + tanager), as a real setup would."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from experiments.agent4.homes import BUILTIN_TOOLS_OFF

#: Azure deployments agent5 can drive, declared the way models.dev declares a model:
#: opencode refuses a model id it has no metadata for, and the metadata is also what it
#: reads `max_tokens` and the per-run cost off. Costs are USD per 1M tokens, matching
#: ``experiments/agent1/azure_client.AZURE_PRICES`` (checked 2026-08-15) rather than the
#: understated sj4 table. ``reasoning: false`` is the honest value: these deployments
#: return no chain of thought at all.
AZURE_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-5.4": {
        "name": "GPT-5.4",
        "tool_call": True,
        "reasoning": False,
        "temperature": True,
        "cost": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
        # 128k output is the deployment's own ceiling: max_completion_tokens 200000 is
        # rejected 400, 128000 is accepted.
        "limit": {"context": 400_000, "output": 128_000},
    },
}

#: Models reachable through the institute AI Gateway (Bifrost), keyed by gateway model id.
#: `temperature: false` is load-bearing: the gateway's gpt-5.5 rejects any temperature but
#: 1 ("Unsupported value: 'temperature' does not support 0.7 with this model"), unlike the
#: direct gpt-5.4 deployment, so opencode must not send the config's value. Cost is left at
#: zero where no published rate is known — the run record keeps tokens, and a guessed price
#: is worse than an obvious zero (same convention as agent1.azure_client).
BIFROST_MODELS: Dict[str, Dict[str, Any]] = {
    "azure/gpt-5.5": {
        "name": "GPT-5.5 (gateway)",
        "tool_call": True,
        "reasoning": False,
        "temperature": False,
        "cost": {"input": 0, "output": 0},
        "limit": {"context": 400_000, "output": 128_000},
    },
}

#: abliteration.ai models (proxy.py --upstream abliteration), keyed by their model id.
#: Values from ``GET /v1/models`` on 2026-09-15: abliterated-model-large-v2 is an
#: abliterated GLM-5.3, fp8, 1M context, temperature range [0, 1], $3 / $5 per 1M
#: input / output tokens, cache read $0.30. ``reasoning: true`` is honest here: the chain
#: of thought streams back as ``delta.reasoning`` (OpenRouter's shape), so opencode records
#: it like any open model's. Output limit kept at 128k like the other tables — the server
#: advertises 999,990 but nothing here needs it and opencode reads max_tokens off this.
ABLITERATION_MODELS: Dict[str, Dict[str, Any]] = {
    "abliterated-model-large-v2": {
        "name": "Abliterated Large v2 (GLM-5.3)",
        "tool_call": True,
        "reasoning": True,
        "temperature": True,
        "cost": {"input": 3.0, "output": 5.0, "cache_read": 0.30},
        "limit": {"context": 1_000_000, "output": 128_000},
    },
}

MODEL_TABLES = {"azure": AZURE_MODELS, "bifrost": BIFROST_MODELS,
                "abliteration": ABLITERATION_MODELS}
TABLE_NAMES = {"azure": "AZURE_MODELS", "bifrost": "BIFROST_MODELS",
               "abliteration": "ABLITERATION_MODELS"}
KEY_ENV = {"azure": "AZURE_OPENAI_API_KEY", "bifrost": "BIFROST_API_KEY",
           "abliteration": "ABLITERATION_API_KEY"}


def make_home5(root: Path, agent: str, *, model: str, proxy_port: int, world_port: int,
               system_prompt: str, temperature: float = 0.7,
               provider: str = "openrouter", api: str = "chat", alias: str = "") -> Path:
    """``alias``: the model id written into the home instead of ``model``. opencode tells
    the assistant "You are powered by the model named <id>", so this is what the model
    hears about itself; the proxy (``--model-alias``) puts ``model`` back before the call
    leaves. ``model`` still selects the MODEL_TABLES entry."""
    home = root / agent.lower()
    agents_dir = home / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # The provider id stays "openrouter" even for Azure runs. opencode resolves a
    # provider id to an npm package it may have to fetch at runtime; the OpenRouter one
    # is already resolved in every existing cell, and the translation to Azure happens a
    # layer down in proxy.py, so an Azure cell differs from its OpenRouter twins in the
    # model id and nothing else. (It is model-visible — opencode appends "You are
    # powered by the model named …" to the system prompt — which is why the deployment
    # name is passed through verbatim.)
    options: Dict[str, Any] = {
        "baseURL": f"http://127.0.0.1:{proxy_port}/a/{agent}/api/v1",
        "apiKey": "{env:OPENROUTER_API_KEY}",
    }
    openrouter: Dict[str, Any] = {"options": options}
    if provider in MODEL_TABLES:
        table = MODEL_TABLES[provider]
        options["apiKey"] = "{env:%s}" % KEY_ENV[provider]
        if model not in table:
            raise ValueError(f"unknown {provider} model {model!r}; add it to "
                             f"homes5.{TABLE_NAMES[provider]}")
        openrouter["models"] = {alias or model: table[model]}
    elif provider != "openrouter":
        raise ValueError(f"unknown provider {provider!r}")
    if alias:
        model = alias

    # ``api="responses"`` (2026-09-06): the gpt-5.x deployments return a reasoning summary
    # only on the Responses API, never on chat completions (probed on the gateway: chat
    # completions -> no reasoning field, /v1/responses with reasoning.summary=auto -> a
    # summary item). opencode's OpenRouter provider speaks chat completions, so the home
    # switches to the ``openai`` provider (ai-sdk's OpenAI provider, Responses API), still
    # pointed at the logging proxy; the proxy forwards ``/v1/responses`` to the upstream.
    # ``reasoningSummary: auto`` is the ai-sdk provider option that asks for the summary,
    # which then arrives as opencode ``reasoning`` parts exactly like an open model's CoT.
    if api not in ("chat", "responses"):
        raise ValueError(f"api must be 'chat' or 'responses', not {api!r}")
    if api == "responses":
        if provider not in MODEL_TABLES:
            raise ValueError("api='responses' needs provider azure or bifrost")
        if provider == "abliteration":
            raise ValueError("api='responses' is untested on the abliteration upstream")
        provider_id = "openai"
        options["baseURL"] = f"http://127.0.0.1:{proxy_port}/a/{agent}/v1"
        table_entry = dict(openrouter["models"][model])
        table_entry["options"] = {"reasoningSummary": "auto"}
        table_entry["reasoning"] = True  # summaries do come back on this path
        provider_block: Dict[str, Any] = {"options": options, "models": {model: table_entry}}
    else:
        provider_id, provider_block = "openrouter", openrouter

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {provider_id: provider_block},
        "mcp": {
            "slack": {"type": "remote", "url": f"http://127.0.0.1:{world_port}/slack/mcp",
                      "headers": {"X-Agent-Name": agent}},
            "tanager": {"type": "remote", "url": f"http://127.0.0.1:{world_port}/tanager/mcp",
                        "headers": {"X-Agent-Name": agent}},
        },
        "share": "disabled",
        "autoupdate": False,
    }
    (home / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")

    tools = dict(BUILTIN_TOOLS_OFF)
    tools["slack_*"] = True
    tools["tanager_*"] = True
    lines: List[str] = [
        "---",
        f"description: {agent}'s workplace assistant",
        "mode: primary",
        f"model: {provider_id}/{model}",
        f"temperature: {temperature}",
        "tools:",
        *[f"  {name}: {str(on).lower()}" for name, on in tools.items()],
        "---",
    ]
    (agents_dir / "assistant.md").write_text("\n".join(lines) + "\n" + system_prompt + "\n")
    return home
