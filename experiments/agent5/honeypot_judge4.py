"""hp4 honeypot judges — J1 over the messages, J2 over the assistant's own record.

    python -m experiments.agent5.honeypot_judge4 --view msg \
        --critic CRITIC_HONEYPOT_W1_hp4msg.md --out outputs/honeypot_hp4msg
    python -m experiments.agent5.honeypot_judge4 --view cot \
        --critic CRITIC_HONEYPOT_W1_hp4cot.md --out outputs/honeypot_hp4cot

J1 is one call over the conversation alone. J2 is two calls in one conversation over the full
agent-eye record: a gate-blind extraction pass, then the gates. ``--pass2 digest`` sends the
second call the extraction instead of the transcript, at roughly half the tokens and with no way
back to anything pass 1 missed.

Runs whose reasoning coverage is below ``--min-coverage`` are never sent to J2: their gates are
`n/a` by construction (see honeypot_agree.py), and the transcript still costs 40k tokens to be
told nothing. The skip is written out as a verdict file so the set stays complete.

Gates are the boolean keys of the critic's **last** JSON example, so a critic version and its
output directory always agree on what a complete verdict is.

``--model`` is ``provider:model``. Default ``openrouter:openai/gpt-5.6-terra``, keyed from
``experiments/agent5/.env3`` — never the repo-root ``.env`` (user rule, 2026-09-17).
``bifrost:azure/gpt-5.5`` is the other path, and is what hp1-hp3 and the first hp4 smokes ran on.
Verdict files carry the judge slug, so two judges can share an output directory; never pool their
verdicts (sj4 inflation).
"""
from __future__ import annotations

import argparse, json, re, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PASS2 = "<<<PASS 2>>>"


def gates_of(critic: str) -> dict[str, list[str] | None]:
    """The gates of the critic's last output example, in order, as {name: options or None}.

    A gate is a top-level key whose example value is either a bool — answered true/false/unclear —
    or a string listing its values, `"explicit | implicit | no | unclear"`. Nested values (the
    per-gate `confidence` object) and `null` placeholders (`declined_because`) are not gates, so a
    critic states its own answer space and the runner validates against it with nothing hardcoded.
    """
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", critic, re.S)
    out: dict[str, list[str] | None] = {}
    for k, v in json.loads(blocks[-1]).items():
        if isinstance(v, bool):
            out[k] = None
        elif isinstance(v, str) and " | " in v:
            out[k] = [x.strip() for x in v.split("|")]
    return out


def _messages(rec: dict) -> str:
    return "\n\n".join(f'{m["clock"]}  [{m["where"]}]  {m["who"]}: {m["text"]}'
                       for m in rec["messages"])


def _transcript(rec: dict) -> str:
    out = []
    for t in rec["turns"]:
        head = f'### turn {t["turn"]} · {t["kind"]} · {t["clock"]}'
        out.append(f'{head}\n[woken by] {t["wake"]}' if t["wake"] else head)
        for s in t["steps"]:
            blk = [f'-- step {s["step"]}']
            if s["reasoning"]:
                blk.append(f'[thinking] {s["reasoning"]}')
            if s["note"]:
                blk.append(f'[private note to {rec["seat"]}] {s["note"]}')
            for c in s["calls"]:
                blk.append(f'[calls] {c["tool"]}({json.dumps(c["args"], ensure_ascii=False)})')
                blk.append(f'[returns] {json.dumps(c["result"], ensure_ascii=False)}')
            out.append("\n".join(blk))
        if t["to_principal"]:
            out.append(f'[message to {rec["seat"]}] {t["to_principal"]}')
    return "\n\n".join(out)


def render(rec: dict, body: str) -> str:
    sub = {"{roster}": rec["roster"], "{employee}": rec["seat"], "{avoided}": rec["avoided"],
           "{outsider}": rec["outsider"], "{request_dm}": rec["request_dm"],
           "{ask}": rec.get("ask", ""), "{system_prompt}": rec.get("system_prompt", ""),
           "{board}": json.dumps(rec.get("board") or {}, ensure_ascii=False),
           "{messages}": _messages(rec) if "messages" in rec else "",
           "{transcript}": _transcript(rec) if "turns" in rec else ""}
    for k, v in sub.items():
        body = body.replace(k, v)
    return body


def openrouter_key(path: Path) -> str:
    """The OpenRouter key from a key file that holds more than one key.

    `.env3` carries the OpenRouter key and an abliteration key under comments, so
    ``read_text().strip()`` would send the whole file as the bearer token (see
    ``preference_judge._read_openrouter_key``, same parse, same reason).
    """
    for line in path.read_text().splitlines():
        line = line.strip().strip('"').strip("'")
        if line.startswith("OPENROUTER_API_KEY="):
            line = line.split("=", 1)[1].strip().strip('"').strip("'")
        if line.startswith("sk-or-"):
            return line
    raise RuntimeError(f"{path} holds no OpenRouter key (a bare sk-or-… line or "
                       f"OPENROUTER_API_KEY=sk-or-…)")


class Judge:
    """One judge backend, called with a message list — J2 needs a three-message conversation,
    which the repo's ``(system, user)`` callers cannot express."""

    def __init__(self, spec: str, *, pin: str = "", effort: str = "medium", timeout: float = 900):
        self.provider, _, self.model = spec.partition(":")
        if not self.model or self.provider not in ("openrouter", "bifrost"):
            raise ValueError(f"--model must be openrouter:… or bifrost:…, got {spec!r}")
        self.slug = self.model.replace("/", "-")
        self.pin, self.effort, self.timeout = pin, effort, timeout
        self.usage: list[dict] = []
        if self.provider == "openrouter":
            self.url = "https://openrouter.ai/api/v1/chat/completions"
            self.key = openrouter_key(HERE / ".env3")
            self.verify: str | bool = True
        else:
            self.url = "https://bifrost.is.localnet/openai/v1/chat/completions"
            self.key = (REPO / ".env2").read_text().strip()
            self.verify = str(REPO / "cluster" / "mpi_is_ca.pem")

    def body(self, messages: list[dict]) -> dict:
        b: dict = {"model": self.model, "messages": messages}
        if self.provider == "bifrost":
            b["temperature"] = 1                       # the gateway rejects anything else
            return b
        # OpenAI's gpt-5 line lists no temperature among its parameters and rejects one, so the
        # field is absent rather than present-and-zero. No max-token cap: a reasoning model over a
        # 100k-token transcript needs its own headroom, and the verdict itself is ~1k.
        b["reasoning"] = {"effort": self.effort}
        b["usage"] = {"include": True}                 # cost and served-by, for the verdict file
        if self.pin and self.pin != "none":
            b["provider"] = {"order": [x.strip() for x in self.pin.split(",") if x.strip()],
                             "allow_fallbacks": False}
        return b

    def __call__(self, messages: list[dict]) -> str:
        for attempt in range(6):
            try:
                r = httpx.post(self.url, verify=self.verify, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self.key}"},
                               json=self.body(messages))
                if r.status_code == 200:
                    d = r.json()
                    ch = (d.get("choices") or [{}])[0]
                    text = str((ch.get("message") or {}).get("content") or "")
                    u = dict(d.get("usage") or {})
                    if d.get("provider"):
                        u["provider_name"] = d["provider"]
                    if ch.get("finish_reason"):
                        u["finish_reason"] = ch["finish_reason"]
                    self.usage.append(u)
                    if not text.strip():               # a 200 with no content says nothing about
                        raise RuntimeError(f"empty content: {json.dumps(u)[:200]}")
                    return text
                if r.status_code not in (408, 429, 500, 502, 503, 504) or attempt == 5:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                wait = float(r.headers.get("retry-after") or 0)
            except httpx.HTTPError as exc:
                if attempt == 5:
                    raise RuntimeError(str(exc)[:200])
                wait = 0
            time.sleep(max(wait, 10 * 2 ** attempt))   # gateway 429s outlast short backoffs
        raise RuntimeError("unreachable")


def parse(text: str, gates: dict[str, list[str] | None]) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    v = json.loads(m.group(0))
    missing = [g for g in gates if g not in v]
    if missing:
        raise ValueError(f"missing gates: {missing}")
    for g, opts in gates.items():
        x = v[g]
        if opts is None:                                  # true / false / "unclear"
            if isinstance(x, str):
                x = {"true": True, "false": False, "unclear": "unclear"}.get(x.strip().lower(), x)
            if x not in (True, False, "unclear"):
                raise ValueError(f"{g}: not true/false/unclear: {x!r}")
        else:                                             # one of the critic's own named values
            x = str(x).strip().strip('"').lower()
            if x not in opts:
                raise ValueError(f"{g}: {x!r} not one of {opts}")
        v[g] = x
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", choices=("msg", "cot"), required=True)
    ap.add_argument("--bundle", default="", help="default honeypot_bundle4_<view>.json")
    ap.add_argument("--critic", default="", help="default CRITIC_HONEYPOT_W1_hp4<view>.md")
    ap.add_argument("--out", default="", help="default outputs/honeypot_hp4<view>")
    ap.add_argument("--model", default="openrouter:openai/gpt-5.6-terra",
                    help="provider:model — openrouter:… (.env3 key) or bifrost:…")
    ap.add_argument("--pin-provider", default="",
                    help="openrouter: ordered backend allow-list, e.g. 'openai/flex,openai'")
    ap.add_argument("--effort", default="medium", help="openrouter: reasoning effort")
    ap.add_argument("--pass2", choices=("conversation", "digest"), default="conversation")
    ap.add_argument("--min-coverage", type=float, default=0.01,
                    help="cot only: runs below this are not sent, their gates are n/a anyway")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="print one rendered prompt and stop")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    critic = (HERE / (a.critic or f"CRITIC_HONEYPOT_W1_hp4{a.view}.md")).read_text()
    body = critic.split("---", 1)[1]
    p1, _, p2 = body.partition(PASS2)
    if a.view == "cot" and not p2:
        raise SystemExit(f"{a.critic}: no {PASS2} marker — J2 is a two-pass critic")
    gates = gates_of(critic)
    recs = json.loads((HERE / (a.bundle or f"honeypot_bundle4_{a.view}.json")).read_text())
    if a.dry_run:
        p = render(recs[0], p1)
        print(p[:8000] + f"\n\n[... {len(p)} chars total, ~{len(p) // 4} tokens]")
        return

    judge = Judge(a.model, pin=a.pin_provider, effort=a.effort)
    out = HERE / (a.out or f"outputs/honeypot_hp4{a.view}")
    out.mkdir(parents=True, exist_ok=True)
    slug = judge.slug

    def one(rec: dict) -> str:
        dst = out / f"{rec['run']}.{slug}.json"
        if dst.exists() and not a.force and "error" not in json.loads(dst.read_text()):
            return f"skip {rec['model']} s{rec['seed']}"
        stamp = {"run": rec["run"], "model": rec["model"], "seed": rec["seed"], "judge": a.model,
                 "pin_provider": a.pin_provider or None,
                 "critic": a.critic or f"CRITIC_HONEYPOT_W1_hp4{a.view}.md",
                 "coverage": rec["reasoning_coverage"]}
        if a.view == "cot" and rec["reasoning_coverage"] < a.min_coverage:
            dst.write_text(json.dumps(stamp | {g: "n/a" for g in gates} |
                                      {"skipped": f"reasoning_coverage={rec['reasoning_coverage']}"},
                                      indent=1) + "\n")
            return f"n/a  {rec['model']:11} s{rec['seed']:<3} no reasoning exposed"
        try:
            first = render(rec, p1)
            seen = len(judge.usage)
            if a.view == "msg":
                v = parse(judge([{"role": "user", "content": first}]), gates)
                extra = {}
            else:
                r1 = judge([{"role": "user", "content": first}])
                if a.pass2 == "conversation":
                    msgs = [{"role": "user", "content": first},
                            {"role": "assistant", "content": r1},
                            {"role": "user", "content": p2}]
                else:
                    head = first.split("<transcript>")[0]
                    msgs = [{"role": "user", "content":
                             f"{head}\n<extraction>\n{r1}\n</extraction>\n{p2}"}]
                v = parse(judge(msgs), gates)
                try:
                    extra = {"extraction": json.loads(re.search(r"\{.*\}", r1, re.S).group(0))}
                except Exception:
                    extra = {"extraction_raw": r1[:20000]}
        except Exception as exc:
            dst.write_text(json.dumps(stamp | {"error": str(exc)[:400]}, indent=1) + "\n")
            return f"ERR  {rec['model']} s{rec['seed']}: {str(exc)[:90]}"
        extra["usage"] = judge.usage[seen:]          # one entry per call, incl. cost and upstream
        dst.write_text(json.dumps(v | stamp | extra, indent=1, ensure_ascii=False) + "\n")
        return f"ok   {rec['model']:11} s{rec['seed']:<3} " + " ".join(
            f"{g}={str(v.get(g))[:10]}" for g in gates)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for line in ex.map(one, recs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
