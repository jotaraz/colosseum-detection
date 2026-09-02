"""The agent5 runner: agent4's concurrent runner with real-daemon wake semantics.

Differences from ConcRunner, all subclass overrides:
- **No ring.** Every message wakes every assistant-backed member of the conversation
  except the sender, immediately and simultaneously — the thundering herd a real Events
  API subscription produces. The wake payload is the raw Slack event JSON.
- The 09:27 channel creation arrives as a ``member_joined_channel`` event per member.
- World server is ``slack_server.py`` (Slack-shaped tools); homes mount two MCP
  connectors (slack + tanager); prompts come from ``prompts5``.

Run:  .venv/bin/python -m experiments.agent5.runner5 --config <yaml>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import time

# Match the world server: naive datetime <-> epoch conversions in the fixture's timezone
# everywhere, so cluster (UTC) and laptop (CEST) mint identical ts. Berlin at import; the
# runner re-pins to the fixture's own ``tz`` once it has read it (w1 is America/New_York).
os.environ["TZ"] = "Europe/Berlin"
time.tzset()

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent4.runner_conc import PY, ConcRunner
from experiments.agent5 import prompts5
from experiments.agent5.homes5 import make_home5
from experiments.agent5.slack_world import (
    API_APP_ID,
    TEAM_ID,
    VERIFICATION_TOKEN,
    client_msg_id,
    load_triggers,
    trigger_matches,
)

AGENT5 = Path(__file__).resolve().parent


class Slack5Runner(ConcRunner):
    EXPERIMENT = "agent5"
    RUNS_DIR = AGENT5 / "runs"
    WORLD_SERVER = AGENT5 / "slack_server.py"

    def __init__(self, config: Dict[str, Any], config_path: str):
        super().__init__(config, config_path)
        #: Model provider: "openrouter" (default), "azure" (direct Azure OpenAI
        #: deployment, ``model`` is the deployment name, e.g. gpt-5.4) or "bifrost" (the
        #: institute AI Gateway, ``model`` is a gateway id, e.g. azure/gpt-5.5). Both
        #: OpenAI legs return no chain of thought, so those runs have empty
        #: ``steps_detail[].reasoning``.
        self.provider = str(config.get("provider") or "openrouter")
        #: Optional OpenRouter backend pin (e.g. "GMICloud"), fallbacks off. Unset means
        #: the router chooses, which is how every cell before w1 ran. Pinning removes the
        #: silent-stall failure mode documented in agent2/JUDGE_OPERATIONS.md, and also
        #: removes quantization as an uncontrolled source of between-seed variance.
        self.pin_provider = str(config.get("pin_provider") or "")
        #: What ends a run early. ``valid`` (default, every rollout to date): the board is
        #: complete *and* every ticket has its pair, plus the reporter's DM. ``settled``:
        #: the board is complete and the reporter has reported, whether or not a ticket is
        #: short — so a refusal (both data scientists declining one partner, W1_PLAN §8a)
        #: ends the run instead of burning to horizon. Changing this changes run behaviour
        #: and breaks comparability with earlier rollouts, so it is opt-in per config.
        #: ``anchored``: the run ends once each assistant named in ``anchor_agents``
        #: (default the two data scientists) has made its first public act — a
        #: ``board_assign`` or a post in the sprint channel — which is exactly the prefix
        #: ``preference_judge`` reads (W1_PLAN §7.4, "mini" runs). Nothing after that point
        #: is observed, so these runs carry no board outcome; pair with ``horizon`` as the
        #: backstop for an assistant that never acts.
        self.converge_on = str(config.get("converge_on") or "valid")
        if self.converge_on not in ("valid", "settled", "anchored"):
            raise ValueError("converge_on must be 'valid', 'settled' or 'anchored', "
                             f"not {self.converge_on!r}")
        self.anchor_agents = [str(a) for a in (config.get("anchor_agents") or ("Priya", "Nadia"))]
        fx = json.loads(self.fixture_path.read_text())
        os.environ["TZ"] = fx.get("tz") or "Europe/Berlin"
        time.tzset()
        self.uid_by_name = {u["name"]: u["id"] for u in fx["users"]}
        self.name_by_uid = {u["id"]: u["name"] for u in fx["users"]}
        self.bot_uids = {u["id"] for u in fx["users"] if u.get("is_bot")}
        self.sprint_cid = fx["sprint_channel_id"]
        # The homes path is model-visible (opencode's <env> block stamps the cwd), so it
        # must not carry a wall-clock stamp that contradicts the simulated date. Keep the
        # <token>-<pid> shape: the cluster reaper frees dirs whose trailing pid is dead.
        self.homes_root = Path("/tmp/tanager") / f"{secrets.token_hex(4)}-{os.getpid()}"
        # Rebuild conv metadata from the Slack-native fixture (super guessed agent4 shapes)
        self.conv_meta = {}
        for c in fx["conversations"]:
            names = [self.name_by_uid[u] for u in c["members"]]
            label = f"#{c['name']}" if c.get("is_channel") else "dm:" + "+".join(sorted(names))
            self.conv_meta[c["id"]] = {"label": label,
                                       "type": "channel" if c.get("is_channel") else "dm",
                                       "members": names, "pinned": bool(c.get("pins"))}
        self.rings = {}  # no speaking ring: the daemon wakes everyone at once
        #: pending member_joined_channel deliveries: (at, cid) not yet announced
        self._joins_pending: Dict[str, datetime] = dict(self.channel_online)
        # Conditional scripted events (v17). The world fires them; the runner evaluates
        # the same specs only so it will not fast-forward the clock past a fire that has
        # not been delivered yet — an idle roster would otherwise jump to the next known
        # event and land the message minutes late, after the moment it was written for.
        self._triggers = load_triggers(fx)
        if self.config.get("script"):
            self._triggers += load_triggers(
                json.loads((REPO / str(self.config["script"])).read_text()))
        self._trigger_due: Dict[str, datetime] = {}
        self.trigger_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------- hooks

    def _first_public_act(self, agent: str) -> bool:
        """Has this assistant claimed a ticket or posted in the sprint channel yet?"""
        t0 = self.clock_start.timestamp()
        if any(t["agent"] == agent and any(c.get("tool") == "board_assign"
                                           for c in t.get("tool_calls") or [])
               for t in self.turns):
            return True
        return any(m.get("conv_id") == self.sprint_cid and m["user"] == agent
                   and float(m["ts"]) > t0 for m in self.all_new)

    def _converged(self, state: Dict[str, Any]) -> bool:
        if self.converge_on == "valid":
            return super()._converged(state)
        if self.converge_on == "anchored":
            return all(self._first_public_act(a) for a in self.anchor_agents)
        # settled: everyone has claimed something and the reporter has reported. An
        # unstaffed ticket is a finding (board_shape == "unstaffed"), not an unfinished run.
        if not state["board_complete"]:
            return False
        if not (self.reporter and self.report_to):
            return True
        return any(m["type"] == "dm" and m["user"] == self.reporter
                   and set(m["members"]) == {self.reporter, self.report_to}
                   and float(m["ts"]) > self.clock_start.timestamp()
                   for m in self.all_new)

    async def _scheduler(self) -> str:
        outcome = await super()._scheduler()
        if outcome == "converged" and self.converge_on == "anchored":
            # The base runner ends a run by appending a sentinel to each inbox, so every
            # wake already queued still runs first — under the thundering-herd wake rule
            # that is a dozen-plus turns after the anchor (first mini batch: both anchors
            # by turn 21, run ended at turn 37). Nothing after the anchor is observed in
            # this mode, so drop the queue; in-flight turns still get their grace.
            dropped = 0
            for q in self.inbox.values():
                while not q.empty():
                    q.get_nowait()
                    dropped += 1
            print(f"anchored: both anchors reached, {dropped} queued wake(s) dropped")
        return outcome

    def world_cmd(self) -> List[str]:
        return [
            PY, str(self.WORLD_SERVER), "--fixture", str(self.fixture_path),
            "--out", str(self.out), "--port", str(self.world_port),
            "--start", self.clock_start.isoformat(),
            "--replay-after", self.clock_start.isoformat(),
            "--clock-scale", str(self.clock_scale),
            *(["--client-blocks"] if self.config.get("slack_blocks") else []),
            *(["--script", str(REPO / str(self.config["script"]))]
              if self.config.get("script") else []),
        ]

    def proxy_cmd(self) -> List[str]:
        # opencode stamps the real date into its <env> block; the proxy rewrites it.
        cmd = super().proxy_cmd() + ["--spoof-date", self.clock_start.date().isoformat()]
        if self.provider != "openrouter":
            cmd += ["--upstream", self.provider]
        elif self.pin_provider:
            cmd += ["--pin-provider", self.pin_provider]
        if self.provider == "bifrost":
            # The gateway's certificate chains to the institute root CA, which certifi
            # does not carry; without this the proxy's upstream handshake fails.
            cmd += ["--ca-bundle", str(REPO / "cluster" / "mpi_is_ca.pem")]
        return cmd

    def env_required(self) -> Tuple[str, ...]:
        if self.provider == "azure":
            return ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY")
        if self.provider == "bifrost":
            return ("BIFROST_API_KEY",)
        return super().env_required()

    def system_prompt(self, agent: str) -> str:
        return prompts5.system_prompt(
            agent, now=self.clock_start, confidentiality=self.confidentiality,
            discussion_norms=self.discussion_norms,
            slack_blocks=bool(self.config.get("slack_blocks")))

    def build_home(self, agent: str, system_prompt: str) -> Path:
        return make_home5(self.homes_root, agent, model=self.model,
                          proxy_port=self.proxy_port, world_port=self.world_port,
                          system_prompt=system_prompt, temperature=self.temperature,
                          provider=self.provider)

    # ------------------------------------------------------------------- wakes
    def _envelope(self, event: Dict[str, Any], recipient_uid: str) -> Dict[str, Any]:
        """The full event_callback JSON a real Events API delivery carries.

        Each employee's connector is its own app install, so event ids and the
        authorizations block are per-recipient; both are minted deterministically.
        """
        ts = str(event.get("event_ts") or event.get("ts") or f"{self.now.timestamp():.6f}")
        digest = hashlib.sha1(f"{ts}:{recipient_uid}".encode()).digest()
        ectx = {"et": event["type"], "tid": TEAM_ID, "aid": API_APP_ID,
                "cid": event.get("channel", "")}
        return {
            "token": VERIFICATION_TOKEN,
            "team_id": TEAM_ID,
            "context_team_id": TEAM_ID,
            "context_enterprise_id": None,
            "api_app_id": API_APP_ID,
            "event": event,
            "type": "event_callback",
            "event_id": "Ev" + base64.b32encode(digest).decode()[:8],
            "event_time": int(float(ts)),
            "authorizations": [{"enterprise_id": None, "team_id": TEAM_ID,
                               "user_id": recipient_uid, "is_bot": False,
                               "is_enterprise_install": False}],
            "is_ext_shared_channel": False,
            "event_context": "4-" + base64.b64encode(
                json.dumps(ectx, separators=(",", ":")).encode()).decode(),
        }

    def _message_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ev: Dict[str, Any] = {
            "type": "message", "channel": row["conv_id"], "user": row["user_id"],
            "text": row["text"], "ts": row["ts"], "event_ts": row["ts"],
            "team": TEAM_ID,
            "channel_type": "im" if row["type"] == "dm" else "group",
        }
        if row["user_id"] not in self.bot_uids:
            ev["client_msg_id"] = client_msg_id(row["conv_id"], row["ts"])
        if row.get("blocks"):
            ev["blocks"] = row["blocks"]
        if row.get("thread_ts"):
            ev["thread_ts"] = row["thread_ts"]
        return ev

    def _check_triggers(self, row: Dict[str, Any]) -> None:
        refs = {row["conv_id"], row["label"], row["label"].lstrip("#")}
        for spec in self._triggers:
            if spec.get("_fired") and (spec.get("when") or {}).get("once", True):
                continue
            if not trigger_matches(spec, sender=row["user"], conv_refs=refs,
                                   text=row.get("text", "")):
                continue
            spec["_fired"] = True
            due = self.now + timedelta(seconds=int(spec.get("delay_seconds") or 0))
            self._trigger_due[str(spec.get("id"))] = due
            self.trigger_log.append({
                "id": spec.get("id"), "matched_clock": self.now.isoformat(),
                "matched_conversation": row["label"], "matched_user": row["user"],
                "matched_ts": row["ts"], "matched_text": row.get("text", ""),
                "delivers_at": due.isoformat(),
            })

    def _process_feed(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            self._check_triggers(row)
            self.all_new.append(row)
            if self.closing_times:  # winding down: record late messages, wake nobody
                continue
            ev = self._message_event(row)
            for member in row["members"]:
                if member in self.roster and member != row["user"]:
                    payload = prompts5.event_wake(
                        self._envelope(ev, self.uid_by_name[member]))
                    self._enqueue(member, "wake", payload,
                                  wake={"label": row["label"], "from": row["user"],
                                        "ts": row["ts"]})

    def _process_asks(self) -> None:
        super()._process_asks()
        for cid, at in list(self._joins_pending.items()):
            if at <= self.now:
                del self._joins_pending[cid]
                meta = self.conv_meta.get(cid) or {}
                for member in meta.get("members", []):
                    if member in self.roster:
                        uid = self.uid_by_name.get(member, member)
                        ev = {"type": "member_joined_channel", "channel": cid,
                              "user": uid, "channel_type": "G", "team": TEAM_ID,
                              "inviter": self.uid_by_name.get("ops-bot", ""),
                              "event_ts": f"{self.now.timestamp():.6f}"}
                        payload = prompts5.event_wake(self._envelope(ev, uid))
                        self._enqueue(member, "added", payload,
                                      wake={"label": meta.get("label", cid)})

    def _next_event(self, unread: Dict[str, Dict[str, int]]) -> Optional[datetime]:
        base = super()._next_event(unread)
        joins = [at for at in self._joins_pending.values() if at > self.now]
        fires = [at for at in self._trigger_due.values() if at > self.now]
        candidates = [t for t in [base, *joins, *fires] if t is not None]
        return min(candidates) if candidates else None

    async def execute_async(self) -> Path:
        out_path = await super().execute_async()
        try:  # the run record is assembled by the base class; annotate it afterwards
            record = json.loads(out_path.read_text())
            record["triggers"] = self.trigger_log
            out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        except Exception as exc:
            print(f"trigger log not recorded: {exc}")
        try:
            from experiments.agent5.viewer5 import render
            print(f"board: {render(out_path)}")
        except Exception as exc:
            print(f"board viewer failed: {exc}")
        return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    asyncio.run(Slack5Runner(config, args.config).execute_async())


if __name__ == "__main__":
    main()
