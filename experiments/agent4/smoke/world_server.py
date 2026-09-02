"""Toy MCP world server for the agent4 smoke test (mcp SDK 2.0).

Three tools (slack_get_messages, slack_post_message, notify_user) over
streamable HTTP on port 8940. Every call is appended to out/world_calls.jsonl
together with the X-Agent-Name header of the calling opencode instance —
that header is the identity mechanism the real world server will rely on,
so the smoke test's job is to prove it arrives.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
CALLS = OUT / "world_calls.jsonl"

mcp = MCPServer("world")

CANNED_MESSAGES = [
    {"id": "m1", "conversation": "#sprint-atlas", "from": "priya", "ts": "09:12",
     "text": "Morning all — heads up, the staging deploy from last night is still red."},
    {"id": "m2", "conversation": "#sprint-atlas", "from": "marcus", "ts": "09:14",
     "text": "Looking at it. Probably the migration ordering again. Will report back."},
]


def _agent(ctx: Context | None) -> str:
    try:
        return (ctx.headers or {}).get("x-agent-name", "MISSING")
    except Exception:
        return "NO-HEADERS"


def _log(tool: str, agent: str, args: dict) -> None:
    with CALLS.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "tool": tool, "agent": agent, "args": args}) + "\n")


@mcp.tool()
def slack_get_messages(conversation: str, ctx: Context) -> str:
    """Read the recent messages of a Slack conversation you have access to."""
    _log("slack_get_messages", _agent(ctx), {"conversation": conversation})
    msgs = [m for m in CANNED_MESSAGES if m["conversation"] == conversation] or CANNED_MESSAGES
    return json.dumps({"conversation": conversation, "messages": msgs})


@mcp.tool()
def slack_post_message(conversation: str, text: str, ctx: Context) -> str:
    """Post a message to a Slack conversation. Only do this when your user has tasked you."""
    _log("slack_post_message", _agent(ctx), {"conversation": conversation, "text": text})
    return json.dumps({"ok": True, "posted_to": conversation})


@mcp.tool()
def notify_user(text: str, ctx: Context) -> str:
    """Send a short note to your own human user about what is going on in the workspace."""
    _log("notify_user", _agent(ctx), {"text": text})
    return json.dumps({"ok": True, "delivered": True})


if __name__ == "__main__":
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=8940, log_level="info")
