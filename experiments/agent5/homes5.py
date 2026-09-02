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

MODEL_TABLES = {"azure": AZURE_MODELS, "bifrost": BIFROST_MODELS}
KEY_ENV = {"azure": "AZURE_OPENAI_API_KEY", "bifrost": "BIFROST_API_KEY"}


def make_home5(root: Path, agent: str, *, model: str, proxy_port: int, world_port: int,
               system_prompt: str, temperature: float = 0.7,
               provider: str = "openrouter") -> Path:
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
                             f"homes5.{'AZURE_MODELS' if provider == 'azure' else 'BIFROST_MODELS'}")
        openrouter["models"] = {model: table[model]}
    elif provider != "openrouter":
        raise ValueError(f"unknown provider {provider!r}")

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"openrouter": openrouter},
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
        f"model: openrouter/{model}",
        f"temperature: {temperature}",
        "tools:",
        *[f"  {name}: {str(on).lower()}" for name, on in tools.items()],
        "---",
    ]
    (agents_dir / "assistant.md").write_text("\n".join(lines) + "\n" + system_prompt + "\n")
    return home
