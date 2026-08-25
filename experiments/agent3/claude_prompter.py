from __future__ import annotations

"""The prompter, driven through ``claude -p`` instead of OpenRouter.

Everything above the model call is inherited from :class:`prompter.Prompter`: the scaffold, the
briefing (`_user_prompt`), the batch contract and the re-ask loop in ``propose``. Only
``_converse`` changes — from a hand-rolled chat-completions tool loop to a subprocess that runs
Claude Code's own agentic loop. The five tools reach the model as an MCP stdio server
(:mod:`mcp_rollout_server`) built from the same ``prompter_tools.TOOLS``, so the two backends put
the identical tool surface in front of the model.

The invocation is not obvious and every flag on it was measured, so it is written down here:

``--setting-sources ""`` and a scratch ``cwd``
    Claude Code otherwise loads the user's settings, the repo's ``CLAUDE.md`` and the skills
    listing into the system prompt — measured at 19.8k tokens of instructions that have nothing
    to do with this experiment sitting above our scaffold, and irreproducible besides, since it
    changes whenever the repo does. With a scratch cwd and ``--disable-slash-commands`` the
    floor is 5.2k, which is the harness preamble and the MCP tool definitions.
``--safe-mode`` is NOT used
    It would strip the same context, but it also disables MCP servers, so the prompter silently
    loses its tools and answers from the briefing alone. It looks like it works. It does not.
``--tools ""``
    Kills the built-in tool set. MCP tools are unaffected, so the model can call our five
    functions and nothing else — no Bash, no file reads, nothing to sandbox.
``--json-schema``
    Forces the reply through a ``StructuredOutput`` tool call, so a prose preamble cannot break
    the parse. ``parse_batch`` still runs, as the validator for tiers, roles and length.
``--output-format stream-json`` (which requires ``--verbose``)
    The tool trajectory. Note the server writes its own ``--trace`` as well: the stream carries a
    call's *arguments* but not the size of what came back, and a step is not reconstructable
    without knowing how much the model actually saw.
``--system-prompt-file``
    Undocumented but present; the rendered scaffold is ~40 KB and does not belong in argv.

There is no ``--max-turns`` in the CLI, so the tool budget is enforced inside the MCP server.
"""

import json
import logging
import os
import pickle
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.agent1.workspace import Workspace
from experiments.agent3 import prompter_tools
from experiments.agent3.candidate import TIERS
from experiments.agent3.prompter import Prompter

logger = logging.getLogger("experiments.agent3.claude_prompter")

DEFAULT_CLI_MODEL = "sonnet"
#: Retried inside ``_converse``: a dead prompter ends the run (``loop.Loop.step``), and on a
#: subscription a rate-limit window is minutes, not a reason to lose eighteen steps.
RETRY_SLEEP_S = (60, 240, 600)


def proposals_schema(optimized: Sequence[str], tiers: Sequence[str] = TIERS) -> Dict[str, Any]:
    """The batch contract as JSON Schema, for ``--json-schema``.

    Enforces the shape and the tier vocabulary. "Exactly one proposal per tier" is not
    expressible here and stays where it already lives, in ``candidate.parse_batch``."""
    return {
        "type": "object",
        "properties": {"proposals": {
            "type": "array", "minItems": len(tiers), "maxItems": len(tiers),
            "items": {
                "type": "object",
                "properties": {
                    "tier": {"type": "string", "enum": list(tiers)},
                    "rationale": {"type": "string"},
                    "asks": {
                        "type": "object",
                        "properties": {who: {"type": "string"} for who in optimized},
                        "required": list(optimized),
                        "additionalProperties": False},
                },
                "required": ["tier", "rationale", "asks"],
                "additionalProperties": False},
        }},
        "required": ["proposals"],
        "additionalProperties": False,
    }


class ClaudeCliPrompter(Prompter):
    """``Prompter`` with the model call replaced by a ``claude -p`` subprocess."""

    def __init__(self, base: Workspace, *,
                 workdir: str | Path,
                 runs_dir: str | Path,
                 cli_model: str = DEFAULT_CLI_MODEL,
                 claude_bin: str = "claude",
                 python_bin: str = "",
                 repo_root: str | Path = "",
                 max_budget_usd: float = 0.0,
                 timeout_s: int = 1800,
                 retries: int = len(RETRY_SLEEP_S),
                 **kw: Any):
        super().__init__(None, f"claude-cli:{cli_model}", base, **kw)
        self.cli_model = cli_model
        self.claude_bin = shutil.which(claude_bin) or claude_bin
        self.python_bin = str(python_bin or _default_python())
        self.repo_root = str(Path(repo_root or _default_repo_root()).resolve())
        self.max_budget_usd = float(max_budget_usd)
        self.timeout_s = int(timeout_s)
        self.retries = int(retries)
        # Absolute, all of it: the subprocess runs in a scratch cwd, so every path handed to
        # `claude` or to the MCP server has to be resolved here or it is resolved against that.
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = Path(runs_dir).resolve()

        # A cwd with no CLAUDE.md, so none is discovered. `--tools ""` means the model cannot
        # read from it either way; this is purely about what lands in the system prompt.
        self._cwd = self.workdir / "cwd"
        self._cwd.mkdir(exist_ok=True)
        self._sys_path = self.workdir / "system_prompt.md"
        self._sys_path.write_text(self.system_prompt, encoding="utf-8")
        self._schema = json.dumps(proposals_schema(self.optimized))
        self._warm_pkl = ""
        if self.warm:
            p = self.workdir / "warm.pkl"
            with p.open("wb") as fh:
                pickle.dump(list(self.warm), fh)
            self._warm_pkl = str(p)
        self._call = 0

    # ------------------------------------------------------------------ config
    def _mcp_config(self, trace: Path) -> Path:
        """Written per call: the trace path is per call, and so is the tool budget's reset."""
        args = ["-m", "experiments.agent3.mcp_rollout_server",
                "--runs", str(self.runs_dir),
                "--reward-agent", self.reward_agent,
                "--budget", str(self.max_tool_calls),
                "--trace", str(trace)]
        if self._warm_pkl:
            args += ["--warm", self._warm_pkl]
        cfg = {"mcpServers": {prompter_tools.MCP_SERVER_NAME: {
            "command": self.python_bin, "args": args,
            "env": {"PYTHONPATH": self.repo_root}}}}
        path = self.workdir / f"mcp_{self._call:03d}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def _argv(self, *, tools: bool, trace: Path) -> List[str]:
        argv = [self.claude_bin, "-p",
                "--model", self.cli_model,
                "--setting-sources", "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--tools", "",
                "--system-prompt-file", str(self._sys_path),
                "--json-schema", self._schema,
                "--output-format", "stream-json", "--verbose"]
        if tools and self.library is not None and self.max_tool_calls > 0:
            argv += ["--strict-mcp-config", "--mcp-config", str(self._mcp_config(trace)),
                     "--allowedTools", f"mcp__{prompter_tools.MCP_SERVER_NAME}"]
        if self.max_budget_usd:
            argv += ["--max-budget-usd", str(self.max_budget_usd)]
        return argv

    # ------------------------------------------------------------------ the call
    def _converse(self, user: str, *, tools: bool = True) -> Tuple[str, Dict[str, Any]]:
        last = ""
        for attempt in range(self.retries + 1):
            self._call += 1
            trace = self.workdir / f"trace_{self._call:03d}.jsonl"
            argv = self._argv(tools=tools, trace=trace)
            t0 = time.time()
            try:
                proc = subprocess.run(argv, input=user, capture_output=True, text=True,
                                      cwd=str(self._cwd), timeout=self.timeout_s,
                                      env={**os.environ, "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"})
                out, err = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                out, err = "", f"timed out after {self.timeout_s}s"
            text, meta = _parse_stream(out)
            meta["tool_calls"] = _read_trace(trace)
            meta["n_tool_calls"] = len(meta["tool_calls"])
            meta["duration_s"] = round(time.time() - t0, 1)
            meta["cli"] = {"model": self.cli_model, "argv": argv[1:],
                           "attempt": attempt + 1, "trace": str(trace)}
            if meta.get("error"):
                last = str(meta["error"])
                logger.warning("  claude -p failed (%s)%s", last[:200],
                               f"; stderr: {err.strip()[-300:]}" if err.strip() else "")
                if attempt < self.retries:
                    sleep = RETRY_SLEEP_S[min(attempt, len(RETRY_SLEEP_S) - 1)]
                    logger.warning("  retrying in %ds (%d/%d)", sleep, attempt + 1, self.retries)
                    time.sleep(sleep)
                    continue
                raise RuntimeError(f"claude -p failed after {attempt + 1} attempts: {last}")
            logger.info("  claude -p: %d tool calls, %d turns, $%.3f, %.0fs",
                        meta["n_tool_calls"], meta.get("hops") or 0,
                        meta["usage"]["cost"], meta["duration_s"])
            return text, meta
        raise RuntimeError(f"claude -p failed: {last}")   # pragma: no cover — loop always returns


def _default_python() -> str:
    import sys
    return sys.executable


def _default_repo_root() -> Path:
    # experiments/agent3/claude_prompter.py -> repo root
    return Path(__file__).resolve().parents[2]


def _read_trace(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row["hop"] = i
        rows.append(row)
    return rows


def _parse_stream(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """The stream-json events -> (final text, meta in ``Prompter._converse``'s shape)."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost": 0.0}
    text, error, hops, rate_limited = "", "", 0, 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "rate_limit_event":
            rate_limited += 1
        elif kind == "result":
            hops = int(ev.get("num_turns") or 0)
            u = ev.get("usage") or {}
            cached = int(u.get("cache_read_input_tokens") or 0)
            usage["cached_tokens"] = cached
            usage["prompt_tokens"] = (int(u.get("input_tokens") or 0) + cached
                                      + int(u.get("cache_creation_input_tokens") or 0))
            usage["completion_tokens"] = int(u.get("output_tokens") or 0)
            usage["cost"] = float(ev.get("total_cost_usd") or 0.0)
            so = ev.get("structured_output")
            # `structured_output` is the schema-validated object; `result` is the model's text,
            # which with --json-schema is normally the same JSON but is not guaranteed to be.
            text = json.dumps(so) if so is not None else str(ev.get("result") or "")
            if ev.get("is_error") or ev.get("subtype") != "success":
                error = (str(ev.get("api_error_status") or "")
                         or str(ev.get("terminal_reason") or "")
                         or str(ev.get("subtype") or "error"))
    if not error and not text:
        error = "claude -p produced no result event"
    return text, {"usage": usage, "hops": hops, "error": error,
                  "rate_limit_events": rate_limited}
