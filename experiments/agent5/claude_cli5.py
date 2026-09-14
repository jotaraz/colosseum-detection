"""agent5 with Claude Code (``claude -p``) as the harness behind every assistant.

Selected by ``backend: claude-cli`` in a config; ``model`` is then a Claude model id
(e.g. ``claude-opus-5``) and auth is whatever ``claude`` itself uses (the subscription
login on the laptop). Everything above the model call is Slack5Runner: world, wakes,
asks, debriefs, horizon, record.

One persistent conversation per assistant: the first turn runs with ``--session-id``,
every later turn with ``--resume`` on the same id, so the context grows exactly as an
opencode session's does. Each turn is its own subprocess (killed on ``turn_timeout``).

Flags, following agent3/claude_prompter.py where they were measured:
- ``--system-prompt-file`` replaces Claude Code's system prompt with agent5's.
- ``--tools ""`` removes every built-in tool; ``--strict-mcp-config --mcp-config`` mounts
  only the world's slack + tanager servers (the user's own connectors stay out), and
  ``--allowedTools mcp__slack mcp__tanager`` lets them run without permission prompts.
- ``--setting-sources ""``, ``--disable-slash-commands`` and a per-assistant scratch cwd
  keep settings, CLAUDE.md and the skills listing out of the context.
- NOT ``--bare`` (reads only ANTHROPIC_API_KEY, so no subscription auth) and NOT
  ``--safe-mode`` (silently disables MCP).
- The parent's CLAUDE* environment is stripped: a nested ``claude`` would otherwise
  inherit this session's effort/session variables.
- Claude Code still injects the account email and the wall-clock date into the
  conversation (no flag removes them), so every assistant's API traffic goes through
  ``claude_proxy5.py`` (``ANTHROPIC_BASE_URL``), which cuts the email and sets the date to
  the simulated one; ``rewrites.jsonl`` in the run dir says per request whether either
  survived. Still model-visible and not rewritten: the environment reminder (cwd under
  /tmp/tanager, darwin, zsh) and "You are powered by the model named Opus 5".

Per-turn usage is the ``result`` event's: ``cache.write`` = cache_creation_input_tokens,
``output`` includes thinking (the API does not split it out). ``cost`` is Claude Code's
``total_cost_usd`` at API prices — notional on a subscription.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from experiments.agent4.runner import Procs
from experiments.agent5.runner5 import AGENT5, PY, Slack5Runner

MCP_SERVERS = ("slack", "tanager")


class ClaudeCliSlack5Runner(Slack5Runner):
    def __init__(self, config: Dict[str, Any], config_path: str):
        super().__init__(config, config_path)
        self.claude_bin = shutil.which("claude") or "claude"
        #: None = Claude Code's own default effort for the model
        self.effort = str(config.get("effort") or "") or None
        #: Claude Code treats a bare ``claude-opus-5`` as a 200k-context model and
        #: auto-compacts near 172k: the history is replaced by a Claude-written summary (plus
        #: re-attached reminders, one of which slipped the email past the proxy). It did so
        #: for Rafael in 3 of the first 5 Opus rollouts (2026-09-14). From then on the model
        #: runs with the ``[1m]`` window and auto-compaction off; ``context_window: default``
        #: restores the old behaviour. A turn that still compacts is counted in the record.
        self.cli_model = self.model if (str(config.get("context_window") or "1m") != "1m"
                                        or self.model.endswith("]")) else f"{self.model}[1m]"
        self.session_ids: Dict[str, str] = {}
        self.started: Dict[str, bool] = {}
        self.agent_dirs: Dict[str, Path] = {}
        self.cli_env = {k: v for k, v in os.environ.items()
                        if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
        self.cli_env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
        if self.cli_model != self.model:
            self.cli_env["DISABLE_AUTO_COMPACT"] = "1"

    # ----------------------------------------------------------------- hooks
    def env_required(self) -> Tuple[str, ...]:
        return ()

    def proxy_cmd(self) -> List[str]:
        return [PY, str(AGENT5 / "claude_proxy5.py"), "--out", str(self.out),
                "--port", str(self.proxy_port), "--date", self.clock_start.date().isoformat(),
                *(["--dump"] if self.config.get("claude_proxy_dump") else [])]

    def enrich_record(self, out_path: Path) -> None:
        # steps_detail is filled per turn from the stream; here only the proxy's audit
        record = json.loads(out_path.read_text())
        rows = [json.loads(l) for l in (self.out / "rewrites.jsonl").read_text().splitlines()]
        msgs = [r for r in rows if r["path"].startswith("/v1/messages")]
        audit = {"requests": len(msgs),
                 "email_cut": sum(r.get("email_cut", 0) for r in msgs),
                 "date_set": sum(r.get("date_set", 0) for r in msgs),
                 "leak_email": sum(1 for r in msgs if (r.get("leak") or {}).get("email")),
                 "leak_real_date": sum(1 for r in msgs if (r.get("leak") or {}).get("real_date")),
                 "batching_dropped": sum(r.get("batching_dropped", 0) for r in msgs),
                 "leak_batching": sum(1 for r in msgs if (r.get("leak") or {}).get("batching")),
                 "rewrite_errors": sum(1 for r in msgs if r.get("rewrite_error")),
                 "scrubbed": sum(r.get("scrubbed", 0) for r in msgs),
                 "leak_literal": sum(1 for r in msgs if r.get("leak_literal")),
                 "cli_model": self.cli_model,
                 "compactions": sum(t.get("compactions", 0) for t in record.get("turns") or [])}
        record["claude_proxy"] = audit
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        print(f"claude proxy: {audit}")

    async def start_agents(self, procs: Procs) -> None:
        for p in self.roster:
            d = self.homes_root / p.lower()
            (d / "cwd").mkdir(parents=True, exist_ok=True)
            (d / "system_prompt.md").write_text(self.system_prompt(p), encoding="utf-8")
            (d / "mcp.json").write_text(json.dumps({"mcpServers": {
                name: {"type": "http", "url": f"{self.world}/{name}/mcp",
                       "headers": {"X-Agent-Name": p}}
                for name in MCP_SERVERS}}), encoding="utf-8")
            self.agent_dirs[p] = d
            self.session_ids[p] = str(uuid.uuid4())
            self.started[p] = False
        (self.out / "claude_sessions.json").write_text(json.dumps(
            {p: {"session_id": s, "cwd": str(self.agent_dirs[p] / "cwd")}
             for p, s in self.session_ids.items()}, indent=2))

    def _argv(self, agent: str) -> List[str]:
        d = self.agent_dirs[agent]
        sid = self.session_ids[agent]
        argv = [self.claude_bin, "-p",
                "--model", self.cli_model,
                *(["--resume", sid] if self.started[agent] else ["--session-id", sid]),
                "--setting-sources", "",
                "--disable-slash-commands",
                "--tools", "",
                "--strict-mcp-config", "--mcp-config", str(d / "mcp.json"),
                "--allowedTools", *[f"mcp__{n}" for n in MCP_SERVERS],
                "--system-prompt-file", str(d / "system_prompt.md"),
                "--output-format", "stream-json", "--verbose"]
        if self.effort:
            argv += ["--effort", self.effort]
        return argv

    # ------------------------------------------------------------------ turn
    async def _run_turn(self, agent: str, item: Dict[str, Any]) -> None:
        state = await self._control("GET", "/control/state")
        clock0, seq0 = state["now"], state["calls"]
        wall0 = time.time()
        d = self.agent_dirs[agent]
        argv = self._argv(agent)
        timed_out = False
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(d / "cwd"),
            env={**self.cli_env,
                 "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.proxy_port}/a/{agent}"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(item["text"].encode()), timeout=self.turn_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            proc.kill()
            out, err = await proc.communicate()
        self.started[agent] = True  # a killed first turn still created the session file
        wall1 = time.time()
        state1 = await self._control("GET", "/control/state")
        with (self.out / f"claude_{agent.lower()}.jsonl").open("ab") as fh:
            fh.write(json.dumps({"turn_wall_start": wall0, "argv": argv[1:],
                                 "returncode": proc.returncode}).encode() + b"\n")
            fh.write(out)
        if err.strip():
            with (self.out / f"claude_{agent.lower()}.stderr").open("ab") as fh:
                fh.write(err)

        parsed = parse_stream(out.decode(errors="replace"))
        tool_calls = []
        try:
            for line in (self.out / "world_calls.jsonl").read_text().splitlines():
                call = json.loads(line)
                if (call["agent"] == agent and call["seq"] > seq0
                        and wall0 - 0.5 <= call["wall"] <= wall1 + 0.5):
                    tool_calls.append({k: call[k] for k in ("seq", "tool", "args", "result", "clock")})
        except FileNotFoundError:
            pass
        # world calls in seq order line up with the stream's tool_use blocks in order
        order = [(s["step"], name) for s in parsed["steps"] for name in s["tool_names"]]
        for call, (step, _) in zip(tool_calls, order):
            call["step"] = step

        steps = [{"step": s["step"], "reasoning": s["reasoning"], "text": s["text"]}
                 for s in parsed["steps"]]
        self.turns.append({
            "i": len(self.turns), "kind": item["kind"], "agent": agent,
            "clock": clock0, "clock_end": state1["now"],
            "wall_start": wall0, "wall_end": wall1,
            "message_in": item["text"],
            **({"timed_out": True} if timed_out else {}),
            **({"cli_error": parsed["error"]} if parsed["error"] else {}),
            **({"compactions": parsed["compactions"]} if parsed["compactions"] else {}),
            "text_to_principal": parsed["final_text"],
            "reasoning": [s["reasoning"] for s in steps if s["reasoning"].strip()],
            "tool_calls": tool_calls,
            "wake": item.get("wake") or {},
            "usage": parsed["usage"],
            "elapsed_seconds": round(wall1 - wall0, 2),
            "steps": len(steps), "steps_detail": steps,
            "reasoning_full": [s["reasoning"] for s in steps],
        })
        if parsed["error"] or timed_out:
            print(f"[claude:{agent}] turn {'timed out' if timed_out else 'error'}: "
                  f"{parsed['error']} {err.decode(errors='replace').strip()[-300:]}", flush=True)


def parse_stream(stdout: str) -> Dict[str, Any]:
    """stream-json -> steps (one per API response), final text, usage."""
    steps: Dict[str, Dict[str, Any]] = {}
    final_text, error = "", ""
    usage: Dict[str, Any] = {}
    compactions = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "compact_boundary":
            compactions += 1
        elif kind == "assistant":
            msg = ev.get("message") or {}
            mid = msg.get("id") or f"_{len(steps)}"
            s = steps.setdefault(mid, {"step": len(steps) + 1, "reasoning": "", "text": "",
                                       "tool_names": []})
            for block in msg.get("content") or []:
                bt = block.get("type")
                if bt == "thinking":
                    s["reasoning"] += block.get("thinking") or ""
                elif bt == "text":
                    s["text"] += block.get("text") or ""
                elif bt == "tool_use":
                    name = str(block.get("name") or "")
                    s["tool_names"].append(name.split("__")[-1])
        elif kind == "result":
            u = ev.get("usage") or {}
            usage = {"tokens": {
                "input": int(u.get("input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
                "reasoning": 0,
                "cache": {"read": int(u.get("cache_read_input_tokens") or 0),
                          "write": int(u.get("cache_creation_input_tokens") or 0)}},
                "cost": float(ev.get("total_cost_usd") or 0.0),
                "num_turns": ev.get("num_turns"),
                "model_usage": ev.get("modelUsage")}
            usage["tokens"]["total"] = (usage["tokens"]["input"] + usage["tokens"]["output"]
                                        + usage["tokens"]["cache"]["read"]
                                        + usage["tokens"]["cache"]["write"])
            final_text = str(ev.get("result") or "")
            if ev.get("is_error") or ev.get("subtype") != "success":
                error = (str(ev.get("api_error_status") or "") or str(ev.get("subtype") or "error")
                         + (": " + final_text[:300] if final_text else ""))
    if not usage and not error:
        error = "no result event"
    return {"steps": list(steps.values()), "final_text": final_text, "usage": usage,
            "error": error, "compactions": compactions}
