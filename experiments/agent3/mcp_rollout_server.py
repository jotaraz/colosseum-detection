from __future__ import annotations

"""``prompter_tools`` exposed to ``claude -p`` as an MCP stdio server.

The OpenRouter prompter hands :data:`prompter_tools.TOOLS` to the model in the chat-completions
tool schema and dispatches through :meth:`RolloutLibrary.call`. Claude Code cannot be handed
tools that way, so the same five functions are served over MCP instead. The schemas are not
retyped here — they are read straight out of ``TOOLS``, so the two backends put byte-identical
tool names, descriptions and parameter schemas in front of the model and the prompter backend
stays the only variable between them.

Three things this process does that a plain wrapper would not:

* **The tool budget lives here.** ``claude -p`` has no ``--max-turns``, so the equivalent of the
  OpenRouter loop's "spend the budget, then take the tools away" is to keep counting on this
  side and, past ``--budget``, answer every call with an error telling the model to write its
  JSON now. The model cannot route around it: these are the only tools it has.
* **It writes the trajectory itself.** ``--trace`` gets one JSON line per call, in the shape
  ``prompter.Prompter._converse`` records — tool, args, error, result_chars. The CLI's
  stream-json carries the *arguments* of a call but not the size of what came back, and a step
  is not reconstructable without knowing what the model actually saw.
* **It reconstructs the warm arms.** They are ``WarmEntry`` objects holding parsed judge records
  and paths into agent1's tree; ``to_dict`` is lossy for that purpose, so the loop pickles the
  list and this process unpickles it. Both sides are ours, on one machine, in one venv.

Run by Claude Code, never by hand::

    python -m experiments.agent3.mcp_rollout_server --runs <out>/runs --reward-agent Priya \
        [--warm warm.pkl] [--budget 15] [--trace calls.jsonl]

stdout is the JSON-RPC channel: nothing may be printed to it. Diagnostics go to stderr, which
Claude Code captures.
"""

import argparse
import asyncio
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, List

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from experiments.agent3 import prompter_tools

SERVER_NAME = prompter_tools.MCP_SERVER_NAME
#: What a call past the budget returns. Phrased as an instruction because the model reads it as
#: the tool's output and has to decide what to do next.
BUDGET_SPENT = ("tool budget spent — you have looked at everything this step allows. "
                "Reply now with the JSON object holding your three proposals.")


def build(library: prompter_tools.RolloutLibrary, *, budget: int,
          trace: Path | None) -> Server:
    state = {"calls": 0}

    tools = [types.Tool(name=t["function"]["name"],
                        description=t["function"]["description"],
                        input_schema=t["function"]["parameters"])
             for t in prompter_tools.TOOLS]

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        args: Dict[str, Any] = dict(params.arguments or {})
        if budget and state["calls"] >= budget:
            result: Dict[str, Any] = {"error": BUDGET_SPENT}
        else:
            state["calls"] += 1
            result = library.call(name, args)
        text = json.dumps(result, default=str)
        row = {"tool": name, "args": args, "error": result.get("error"),
               "result_chars": len(text)}
        print(f"agent3 tool {name}({json.dumps(args, default=str)[:120]})"
              f" -> {len(text)} chars"
              f"{'  ERROR ' + str(result.get('error'))[:80] if result.get('error') else ''}",
              file=sys.stderr, flush=True)
        if trace is not None:
            with trace.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        # is_error stays False even for a tool-level error: the payload explains itself and a
        # protocol error would cost a turn without telling the model anything more.
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server(SERVER_NAME, version="1",
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", required=True, help="<out_dir>/runs")
    ap.add_argument("--reward-agent", required=True)
    ap.add_argument("--warm", default="", help="pickle of the WarmEntry list")
    ap.add_argument("--budget", type=int, default=15,
                    help="tool calls allowed this step; 0 for no limit")
    ap.add_argument("--trace", default="", help="append one JSON line per call here")
    args = ap.parse_args(argv)

    warm = []
    if args.warm:
        with open(args.warm, "rb") as fh:
            warm = pickle.load(fh)
    library = prompter_tools.RolloutLibrary(args.runs, args.reward_agent, warm=warm)
    trace = Path(args.trace) if args.trace else None
    server = build(library, budget=args.budget, trace=trace)
    print(f"agent3 mcp: runs={args.runs} warm={len(warm)} budget={args.budget}",
          file=sys.stderr, flush=True)

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
