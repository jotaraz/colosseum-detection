"""Honeypot judge — one call per run, gates from a CRITIC_HONEYPOT_W1_<tag>.md.

    python -m experiments.agent5.honeypot_judge5 --critic CRITIC_HONEYPOT_W1_hp2.md \
        --out outputs/honeypot_hp2 [--dry-run] [--workers 4]

The gates are whatever the critic's output example gives a boolean, so a critic version and its
output directory always agree on what a complete verdict is.

Default judge is gpt-5.5 through the institute gateway (temperature must be 1 there). Verdicts
are written one file per run and must never be pooled across judge models (sj4 inflation).
"""
from __future__ import annotations

import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def gates_of(critic: str) -> list[str]:
    """The boolean keys of the critic's own output example, in order."""
    block = re.search(r"```json\s*(\{.*?\})\s*```", critic, re.S).group(1)
    return [k for k, v in json.loads(block).items() if isinstance(v, bool)]
KIND = {"post": "posts", "msg": "", "dm": "", "think": "", "cal": ""}


def render(rec: dict, critic: str) -> str:
    rows = [f'{e["clock"]}  ‹{e["who"]}{" posts" if e["kind"] == "post" else ""}› {e["text"]}'
            for e in rec["timeline"]]
    return (critic.split("---", 1)[1]
            .replace("{roster}", rec["roster"]).replace("{employee}", rec["seat"])
            .replace("{avoided}", rec["avoided"]).replace("{ask}", rec["ask"])
            .replace("{request_dm}", rec["request_dm"])
            .replace("{calendar}", "  ·  ".join(rec["calendar"]))
            .replace("{booking_facts}", "; ".join(rec["booking_facts"]))
            .replace("{timeline}", "\n\n".join(rows))
            .replace("{board}", json.dumps(rec["board"], ensure_ascii=False)))


def call(prompt: str, model: str, key: str, ca: str) -> str:
    url = "https://bifrost.is.localnet/openai/v1/chat/completions"
    for attempt in range(6):
        try:
            r = httpx.post(url, verify=ca, timeout=600,
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": model, "temperature": 1,
                                 "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code not in (408, 429, 500, 502, 503, 504) or attempt == 5:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            wait = float(r.headers.get("retry-after") or 0)
        except httpx.HTTPError as exc:
            if attempt == 5:
                raise RuntimeError(str(exc)[:200])
            wait = 0
        time.sleep(max(wait, 10 * 2 ** attempt))      # the gateway's 429s outlast short backoffs
    raise RuntimeError("unreachable")


def parse(text: str, GATES: list[str]) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    v = json.loads(m.group(0))
    missing = [g for g in GATES if g not in v]
    if missing:
        raise ValueError(f"missing gates: {missing}")
    for g in GATES:              # hp3+ allow "unclear"; a string "true"/"false" is still a bool
        x = v[g]
        if isinstance(x, str):
            x = {"true": True, "false": False, "unclear": "unclear"}.get(x.strip().lower(), x)
        if x not in (True, False, "unclear"):
            raise ValueError(f"{g}: not true/false/unclear: {x!r}")
        v[g] = x
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="honeypot_bundle.json")
    ap.add_argument("--critic", default="CRITIC_HONEYPOT_W1_hp2.md")
    ap.add_argument("--out", default="outputs/honeypot_hp2")
    ap.add_argument("--model", default="azure/gpt-5.5")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="print one rendered prompt and stop")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    critic = (HERE / a.critic).read_text()
    GATES = gates_of(critic)
    recs = json.loads((HERE / a.bundle).read_text())
    if a.dry_run:
        p = render(recs[0], critic)
        print(p[:6000] + f"\n\n[... {len(p)} chars total, ~{len(p)//4} tokens]")
        return

    key = (REPO / ".env2").read_text().strip()
    ca = str(REPO / "cluster" / "mpi_is_ca.pem")
    out = HERE / a.out
    out.mkdir(parents=True, exist_ok=True)
    slug = a.model.replace("/", "-")

    def one(rec: dict) -> str:
        dst = out / f"{rec['run']}.{slug}.json"
        if dst.exists() and not a.force and "error" not in json.loads(dst.read_text()):
            return f"skip {rec['model']} s{rec['seed']}"
        try:
            reply = call(render(rec, critic), a.model, key, ca)
            v = parse(reply, GATES)
        except Exception as exc:
            dst.write_text(json.dumps({"run": rec["run"], "error": str(exc)[:400]}, indent=1))
            return f"ERR  {rec['model']} s{rec['seed']}: {str(exc)[:90]}"
        v |= {"run": rec["run"], "model": rec["model"], "seed": rec["seed"], "judge": a.model,
              "critic": a.critic}
        dst.write_text(json.dumps(v, indent=1, ensure_ascii=False) + "\n")
        return f"ok   {rec['model']:11} s{rec['seed']:<3} " + " ".join(
            f"{g}={str(v.get(g))[:5]}" for g in GATES)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for line in ex.map(one, recs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
