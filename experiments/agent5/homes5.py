"""agent5 opencode homes: two MCP connectors (slack + tanager), as a real setup would."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from experiments.agent4.homes import BUILTIN_TOOLS_OFF


def make_home5(root: Path, agent: str, *, model: str, proxy_port: int, world_port: int,
               system_prompt: str, temperature: float = 0.7) -> Path:
    home = root / agent.lower()
    agents_dir = home / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"openrouter": {"options": {
            "baseURL": f"http://127.0.0.1:{proxy_port}/a/{agent}/api/v1",
            "apiKey": "{env:OPENROUTER_API_KEY}"}}},
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
