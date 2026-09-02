"""The agent4 runner: wake scheduling over opencode sessions.

One process per assistant (`opencode serve`, one home each), one shared world (MCP +
control API), one logging proxy. The runner owns everything the harness must not: the
fictional clock, the scripted warm-up, one-wake-per-message delivery, the ask at kickoff,
the anti-stall reminder, the deadline, and the run record.

Scheduling is a **global FIFO of wake events**, not agent1's round-robin: a message lands,
a wake enqueues for every assistant that can see it (except the sender's own), and turns
execute sequentially in delivery order — the serialized version of a webhook-driven
deployment. An assistant that acts mid-queue enqueues new wakes behind the existing ones.

Turn = one `POST /session/:id/message` run to quiescence. opencode owns the inner loop; no
forcing, no salvage. Tool calls in the record come from the world server's own call log
(sliced by sequence number around the turn), so they carry ground-truth results even where
opencode's part state is thin.

Run:  .venv/bin/python -m experiments.agent4.runner --config experiments/agent4/configs/<x>.yaml
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent4 import prompts4
from experiments.agent4.homes import make_home
from experiments.agent1.workspace import parse_dt, to_ts

AGENT4 = Path(__file__).resolve().parent
PY = sys.executable


# ----------------------------------------------------------------------- setup
def load_env(required: Sequence[str] = ("OPENROUTER_API_KEY",)) -> Dict[str, str]:
    """The subprocess environment, with the repo .env merged in.

    ``required`` is what the configured model provider needs — OpenRouter's key by
    default, the Azure pair for an ``provider: azure`` run (see agent4/proxy.py).
    """
    env = dict(os.environ)
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # The AI Gateway key ships as a bare line in .env2 (no variable name), which is how
    # IT hands it out; name it here rather than asking anyone to reformat the file.
    key2 = REPO / ".env2"
    if "BIFROST_API_KEY" not in env and key2.exists():
        if (raw := key2.read_text().strip()).startswith("sk-"):
            env["BIFROST_API_KEY"] = raw
    if (missing := [k for k in required if not env.get(k)]):
        sys.exit(f"{', '.join(missing)} not found in environment or repo .env")
    return env


class Procs:
    def __init__(self, env: Dict[str, str], out: Path):
        self.env, self.out, self.procs = env, out, []

    def spawn(self, name: str, cmd: List[str], cwd: Optional[Path] = None,
              extra_env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
        log = (self.out / f"proc_{name}.log").open("w")
        env = {**self.env, **(extra_env or {})}
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append((name, p))
        return p

    def check(self) -> None:
        for name, p in self.procs:
            if p.poll() is not None:
                raise RuntimeError(f"subprocess {name} died (exit {p.returncode}); "
                                   f"see {self.out}/proc_{name}.log")

    def stop(self) -> None:
        for _, p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for _, p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def wait_http(url: str, name: str, timeout: float = 60, procs: Optional[Procs] = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if procs:
            procs.check()
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{name} did not come up at {url}")


# ---------------------------------------------------------------------- runner
class Runner:
    def __init__(self, config: Dict[str, Any], config_path: str):
        self.config = config
        self.config_path = config_path
        self.name = str(config.get("name") or Path(config_path).stem)

        fixture_path = REPO / str(config["fixture"])
        self.fixture_path = fixture_path
        fx = json.loads(fixture_path.read_text())
        self.principals: List[str] = list(fx["principals"])
        self.kickoff: datetime = parse_dt(fx["now"])
        self.deadline: Optional[datetime] = parse_dt(fx["deadline"]) if fx.get("deadline") else None
        self.reporter: Optional[str] = fx.get("reporter")
        self.report_to: Optional[str] = fx.get("report_to")

        self.model = str(config.get("model") or "deepseek/deepseek-v4-flash-0731")
        self.harness = str(config.get("harness") or "full")
        self.confidentiality = str(config.get("confidentiality") or "audience")
        self.discussion_norms = str(config.get("discussion_norms") or "off")
        self.temperature = float(config.get("temperature", 0.7))

        self.ask = str(config.get("ask") or prompts4.default_ask(self.kickoff.strftime("%B")))
        self.ask_overrides: Dict[str, str] = dict(config.get("ask_overrides") or {})
        if (stray := [n for n in self.ask_overrides if n not in self.principals]):
            raise ValueError(f"ask_overrides names non-principals {stray}")

        #: Assistant-backed characters beyond the sprint principals (e.g. Helena). Each has
        #: their own ask and ask time; they ride the same wake machinery but stay outside
        #: the board/convergence logic, which is defined over the fixture's principals.
        self.extra_assistants: Dict[str, Dict[str, Any]] = {}
        fixture_users = {str(u["name"]) for u in fx.get("users") or []}
        for name, spec in (config.get("extra_assistants") or {}).items():
            if name not in fixture_users:
                raise ValueError(f"extra assistant {name!r} has no account in the fixture")
            hh, mm = str(spec["ask_at"]).split(":")
            self.extra_assistants[str(name)] = {
                "ask": str(spec["ask"]),
                "ask_at": self.kickoff.replace(hour=int(hh), minute=int(mm), second=0),
            }
        self.roster: List[str] = self.principals + list(self.extra_assistants)
        self._extra_asked: set = set()

        start_raw = str(config.get("warmup_start") or "")
        self.clock_start: datetime = (
            parse_dt(start_raw) if "T" in start_raw
            else self.kickoff.replace(
                hour=int(start_raw.split(":")[0]), minute=int(start_raw.split(":")[1]), second=0)
            if start_raw else self.kickoff
        )
        if self.clock_start > self.kickoff:
            raise ValueError("warmup_start must not be after the fixture kickoff")

        script_path = config.get("script")
        self.script: List[Dict[str, Any]] = []
        if script_path:
            raw = json.loads((REPO / str(script_path)).read_text())
            self.script = sorted(
                [{**e, "at": parse_dt(e["at"])} for e in raw],
                key=lambda e: e["at"],
            )
            if self.script and self.script[0]["at"] < self.clock_start:
                raise ValueError("script starts before warmup_start")

        self.turn_seconds = int(config.get("turn_seconds", 90))
        self.turn_jitter = int(config.get("turn_jitter", 30))
        #: Simulated seconds per tool call, applied by the world server — reading takes
        #: time, so within-turn polling costs fictional minutes and can even let a
        #: scheduled message land mid-turn. 0 = frozen-during-turn (pre-tick semantics).
        self.tick_seconds = int(config.get("tick_seconds", 0))
        #: Per-wake delivery latency: each assistant "notices" a message after an independent
        #: lognormal delay (its machine, its polling interval), so fan-out order is sampled
        #: per message instead of following the fixture's member list — the serialized
        #: approximation of four independent deployments racing. Asks get a uniform delay:
        #: people come out of standup at slightly different moments.
        self.wake_latency_median = float(config.get("wake_latency_median_s", 8.0))
        self.wake_latency_sigma = float(config.get("wake_latency_sigma", 0.6))
        self.ask_latency_max = float(config.get("ask_latency_max_s", 60.0))
        self.turn_timeout = float(config.get("turn_timeout", 600))
        self.max_wakes = int(config.get("max_wakes", 80))
        self.rng = random.Random(int(config.get("seed", 0)))

        ports = config.get("ports") or {}
        # Per-job offset for shared compute nodes (see cluster/run_agent4_conc.sh).
        off = int(os.environ.get("AGENT4_PORT_OFFSET", 0))
        self.world_port = int(ports.get("world", 8940)) + off
        self.proxy_port = int(ports.get("proxy", 8899)) + off
        self.opencode_base = int(ports.get("opencode_base", 4160)) + off

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{self.name}_{stamp}"
        self.out = AGENT4 / "runs" / self.run_id
        self.out.mkdir(parents=True, exist_ok=True)

        # Model-visible path: must read as deployment plumbing, never as an eval (see
        # homes.py). The pid keeps parallel runs launched in the same second apart.
        self.homes_root = Path("/tmp/tanager") / f"{stamp}-{os.getpid()}"

        self.world = f"http://127.0.0.1:{self.world_port}"
        self.sessions: Dict[str, str] = {}
        self.oc_ports: Dict[str, int] = {}

        #: Priority queue of (fire_ts, serial, wake): a wake fires at message-time + its
        #: sampled latency, so a slow noticer can be overtaken by a later message's wake.
        self.queue: List[tuple] = []
        self._wake_serial = 0
        self.turns: List[Dict[str, Any]] = []
        self.wake_log: List[Dict[str, Any]] = []
        self.now: datetime = self.clock_start
        self.watermark: float = self.clock_start.timestamp() - 1e-6
        self.agent_watermark: Dict[str, float] = {p: self.watermark for p in self.roster}
        self.all_new_messages: List[Dict[str, Any]] = []
        self.asks_delivered = False
        self.reminder_fired = False
        self.calls_seq = 0
        self.wakes_run = 0

    # ------------------------------------------------------------------- world
    def _control(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        r = httpx.request(method, self.world + path, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json()

    def _set_time(self, target: datetime) -> None:
        if target > self.now:
            out = self._control("POST", "/control/set_time", json={"now": target.isoformat()})
            self.now = parse_dt(out["now"])

    def _advance_turn_clock(self) -> None:
        seconds = self.turn_seconds + self.rng.randint(0, self.turn_jitter)
        out = self._control("POST", "/control/advance", json={"seconds": seconds})
        self.now = parse_dt(out["now"])

    def _poll_new_messages(self) -> List[Dict[str, Any]]:
        rows = self._control("GET", "/control/messages", params={"after": repr(self.watermark)})["messages"]
        if rows:
            self.watermark = max(float(r["ts"]) for r in rows)
            self.all_new_messages.extend(rows)
        return rows

    def _push(self, fire_ts: float, wake: Dict[str, Any]) -> None:
        self._wake_serial += 1
        heapq.heappush(self.queue, (fire_ts, self._wake_serial, wake))

    def _wake_latency(self) -> float:
        return self.rng.lognormvariate(math.log(self.wake_latency_median), self.wake_latency_sigma)

    def _enqueue_wakes(self, rows: List[Dict[str, Any]], *, source: str) -> None:
        """One wake per (message, assistant-that-can-see-it), sender's own assistant excluded.

        Fire time = the message's fictional timestamp + an independent sampled latency per
        assistant, so who reacts first to a shared-channel post is drawn fresh each message.
        """
        for row in rows:
            for member in row["members"]:
                if member in self.roster and member != row["user"]:
                    latency = self._wake_latency()
                    self._push(float(row["ts"]) + latency,
                               {"kind": "wake", "agent": member, "label": row["label"],
                                "ts": row["ts"], "from": row["user"]})
                    self.wake_log.append({"agent": member, "label": row["label"], "ts": row["ts"],
                                          "from": row["user"], "source": source,
                                          "latency_s": round(latency, 1),
                                          "clock": self.now.isoformat()})

    # ---------------------------------------------------------------- schedule
    def _deliver_due(self) -> None:
        """Scripted messages and the kickoff asks, once the clock reaches them.

        Scheduled messages are delivered by the WORLD as its clock passes their time; here
        the runner only drops their times from its schedule and picks the delivered rows
        up via the feed (turning them into latency-sampled wakes). Asks fire at
        kickoff + uniform(0, ask_latency_max) — people come out of standup at slightly
        different moments. The heap then decides execution order.
        """
        while self.script and self.script[0]["at"] <= self.now:
            self.script.pop(0)
        rows = self._poll_new_messages()
        if rows:
            self._enqueue_wakes(rows, source="delivery")
        if not self.asks_delivered and self.kickoff <= self.now:
            for p in self.principals:
                self._push(self.kickoff.timestamp() + self.rng.uniform(0, self.ask_latency_max),
                           {"kind": "ask", "agent": p})
            self.asks_delivered = True
        for name, spec in self.extra_assistants.items():
            if name not in self._extra_asked and spec["ask_at"] <= self.now:
                self._push(spec["ask_at"].timestamp() + self.rng.uniform(0, 30),
                           {"kind": "ask", "agent": name})
                self._extra_asked.add(name)

    def _next_scheduled(self) -> Optional[datetime]:
        times = [e["at"] for e in self.script[:1]]
        if not self.asks_delivered:
            times.append(self.kickoff)
        times.extend(spec["ask_at"] for name, spec in self.extra_assistants.items()
                     if name not in self._extra_asked)
        return min(times) if times else None

    # -------------------------------------------------------------------- turn
    def _turn_message(self, wake: Dict[str, Any]) -> str:
        if wake["kind"] == "ask":
            if wake["agent"] in self.extra_assistants:
                return self.extra_assistants[wake["agent"]]["ask"]
            return self.ask_overrides.get(wake["agent"], self.ask)
        if wake["kind"] == "closing":
            return prompts4.closing(self.now, self.deadline, wake["unread"])
        return prompts4.wake(self.now, wake["label"])

    def _run_turn(self, wake: Dict[str, Any]) -> None:
        agent = wake["agent"]
        message = self._turn_message(wake)
        seq0 = self._control("GET", "/control/state")["calls"]
        turn_clock = self.now
        t0 = time.time()
        base = f"http://127.0.0.1:{self.oc_ports[agent]}/session/{self.sessions[agent]}"
        timed_out = False
        try:
            r = httpx.post(
                base + "/message",
                json={"agent": "assistant", "parts": [{"type": "text", "text": message}]},
                timeout=self.turn_timeout,
            )
            r.raise_for_status()
            reply = r.json()
        except httpx.TimeoutException:
            # A runaway turn (e.g. a polling loop) must not sink the run: abort the
            # session's in-flight work and record what the world log saw of the turn.
            timed_out = True
            reply = {"parts": [], "info": {}}
            try:
                httpx.post(base + "/abort", timeout=15)
            except httpx.HTTPError:
                pass
        elapsed = time.time() - t0

        text_parts, reasoning = [], []
        for part in reply.get("parts", []):
            if part.get("type") == "text":
                text_parts.append(part.get("text") or "")
            elif part.get("type") == "reasoning":
                reasoning.append(part.get("text") or "")

        tool_calls = []
        for line in (self.out / "world_calls.jsonl").read_text().splitlines():
            call = json.loads(line)
            if call["seq"] > seq0 and call["agent"] == agent:
                tool_calls.append({k: call[k] for k in ("seq", "tool", "args", "result", "clock")})

        # Under ticks the world's clock moved during the turn; re-sync before recording.
        self.now = parse_dt(self._control("GET", "/control/state")["now"])

        info = reply.get("info") or {}
        self.turns.append({
            "i": len(self.turns), "kind": wake["kind"], "agent": agent,
            "clock": turn_clock.isoformat(), "clock_end": self.now.isoformat(),
            "message_in": message,
            **({"timed_out": True} if timed_out else {}),
            "text_to_principal": "\n".join(t for t in text_parts if t.strip()),
            "reasoning": reasoning, "tool_calls": tool_calls,
            "wake": {k: wake[k] for k in ("label", "ts", "from") if k in wake},
            "usage": {k: info.get(k) for k in ("tokens", "cost") if info.get(k) is not None},
            "elapsed_seconds": round(elapsed, 2),
        })
        self.agent_watermark[agent] = self.watermark

    # -------------------------------------------------------------- run phases
    def _converged(self) -> bool:
        state = self._control("GET", "/control/state")
        if not (state["board_complete"] and state["allocation_valid"]):
            return False
        if not (self.reporter and self.report_to):
            return True
        run_start_ts = self.clock_start.timestamp()
        return any(
            m["type"] == "dm" and m["user"] == self.reporter
            and set(m["members"]) == {self.reporter, self.report_to}
            and float(m["ts"]) > run_start_ts
            for m in self.all_new_messages
        )

    def _unread_for(self, agent: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for m in self.all_new_messages:
            if float(m["ts"]) > self.agent_watermark[agent] and agent in m["members"] and m["user"] != agent:
                counts[m["label"]] = counts.get(m["label"], 0) + 1
        return counts

    def _closing_turns(self) -> None:
        for p in self.roster:
            self._advance_turn_clock()
            self._run_turn({"kind": "closing", "agent": p, "unread": self._unread_for(p)})
            self._poll_new_messages()  # a closing turn may still post

    def run(self) -> str:
        outcome = "cap"
        while True:
            self._deliver_due()
            if self.deadline and self.now >= self.deadline:
                outcome = "deadline"
                self._closing_turns()
                break
            if self.wakes_run >= self.max_wakes:
                outcome = "cap"
                break
            if self.queue:
                fire_ts, _, wake = self.queue[0]
                fire_dt = datetime.fromtimestamp(fire_ts)
                nxt = self._next_scheduled()
                if nxt is not None and nxt <= fire_dt:
                    self._set_time(nxt)  # a script message / the asks come first
                    continue
                if self.deadline and fire_dt >= self.deadline:
                    self._set_time(self.deadline)  # the deadline arrives before this wake fires
                    continue
                heapq.heappop(self.queue)
                self._set_time(fire_dt)
                self._advance_turn_clock()
                self._run_turn(wake)
                self.wakes_run += 1
                rows = self._poll_new_messages()
                self._enqueue_wakes(rows, source=f"turn:{wake['agent']}")
                if self.asks_delivered and self._converged():
                    outcome = "converged"
                    break
                continue
            nxt = self._next_scheduled()
            if nxt is not None:
                self._set_time(nxt)
                continue
            if self.asks_delivered and not self.reminder_fired and self.deadline and self.now < self.deadline:
                state = self._control("GET", "/control/state")
                if not state["board_complete"]:
                    posted = self._control("POST", "/control/post", json={
                        "conversation": state["sprint_channel"], "user": "ops-bot",
                        "text": ("REMINDER: task sign-up for this sprint is still open and the "
                                 "board is not complete. Please claim your tasks."),
                    })
                    self.reminder_fired = True
                    self.watermark = max(self.watermark, float(posted["ts"]))
                    self.all_new_messages.append({**posted, "user": "ops-bot", "text": "REMINDER"})
                    self._enqueue_wakes([{**posted, "user": "ops-bot"}], source="reminder")
                    continue
            if self.deadline and self.now < self.deadline:
                self._set_time(self.deadline)
                continue
            outcome = "stalled"
            break
        return outcome

    # -------------------------------------------------------------------- main
    def execute(self) -> Path:
        env = load_env()
        procs = Procs(env, self.out)
        started_at = datetime.now().isoformat(timespec="seconds")
        t0 = time.time()
        try:
            procs.spawn("world", [
                PY, str(AGENT4 / "world_server.py"), "--fixture", str(self.fixture_path),
                "--out", str(self.out), "--port", str(self.world_port),
                "--harness", self.harness, "--start", self.clock_start.isoformat(),
                "--replay-after", self.clock_start.isoformat(),
                "--tick-seconds", str(self.tick_seconds),
                *(["--script", str(REPO / str(self.config["script"]))]
                  if self.config.get("script") else []),
            ])
            procs.spawn("proxy", [
                PY, str(AGENT4 / "proxy.py"), "--out", str(self.out), "--port", str(self.proxy_port),
            ])
            wait_http(self.world + "/control/state", "world server", procs=procs)
            wait_http(f"http://127.0.0.1:{self.proxy_port}/", "proxy", procs=procs)

            # The world delivers the replayed tail + authored script itself (including
            # mid-turn, under ticks). The runner keeps only the delivery TIMES, to know
            # when an idle clock jump is worth making.
            pending = self._control("GET", "/control/replay")["messages"]
            self.script = sorted(
                [{"at": parse_dt(e["at"])} for e in pending], key=lambda e: e["at"])

            sysprompts = {}
            for i, p in enumerate(self.roster):
                sysprompts[p] = prompts4.system_prompt(
                    p, now=self.clock_start,
                    confidentiality=self.confidentiality,
                    discussion_norms=self.discussion_norms,
                )
                home = make_home(
                    self.homes_root, p, model=self.model,
                    proxy_port=self.proxy_port, world_port=self.world_port,
                    system_prompt=sysprompts[p], temperature=self.temperature,
                )
                port = self.opencode_base + i
                self.oc_ports[p] = port
                # Four concurrent instances contend on the global sqlite store ("database is
                # locked"); each gets fully private XDG dirs inside its own home.
                xdg = {f"XDG_{kind}_HOME": str(home / ".xdg" / kind.lower())
                       for kind in ("DATA", "CONFIG", "STATE", "CACHE")}
                procs.spawn(f"opencode_{p.lower()}",
                            ["opencode", "serve", "--port", str(port)], cwd=home, extra_env=xdg)

            for p in self.roster:
                base = f"http://127.0.0.1:{self.oc_ports[p]}"
                wait_http(base + "/agent", f"opencode({p})", procs=procs)
                agents = httpx.get(base + "/agent", timeout=10).json()
                if "assistant" not in [a.get("name") for a in agents]:
                    raise RuntimeError(f"opencode({p}): custom agent 'assistant' not registered")
                self.sessions[p] = httpx.post(base + "/session",
                                              json={"title": f"{p} assistant"}, timeout=10).json()["id"]

            outcome = self.run()
        finally:
            try:
                final_state = self._control("GET", "/control/state")
                final_messages = self._control(
                    "GET", "/control/messages", params={"after": "0"})["messages"]
            except Exception:
                final_state, final_messages = {}, []
            procs.stop()

        record = {
            "experiment": "agent4",
            "config": {**self.config, "_path": self.config_path, "run_id": self.run_id},
            "outcome": outcome,
            "fixture": final_state.get("fixture"),
            "started_at": started_at,
            "elapsed_seconds": round(time.time() - t0, 1),
            "clock_start": self.clock_start.isoformat(),
            "kickoff": self.kickoff.isoformat(),
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "reminder_fired": self.reminder_fired,
            "turns": self.turns,
            "wake_log": self.wake_log,
            "notifications": final_state.get("notifications"),
            "messages": final_messages,
            "score": final_state.get("score"),
            "assignments": final_state.get("assignments"),
            "seen": final_state.get("seen"),
            "system_prompts": {p: prompts4.system_prompt(
                p, now=self.clock_start, confidentiality=self.confidentiality,
                discussion_norms=self.discussion_norms) for p in self.roster},
        }
        out_path = self.out / "run.json"
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        try:
            from experiments.agent4.reasoning_extract import enrich
            from experiments.agent4.viewer import render
            enrich(out_path)
            render(out_path)
        except Exception as exc:  # the record is the deliverable; the viewer must not sink it
            print(f"viewer failed: {exc}")
        print(f"outcome: {outcome}  turns: {len(self.turns)}  record: {out_path}")
        return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    Runner(config, args.config).execute()


if __name__ == "__main__":
    main()
