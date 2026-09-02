"""Pass-through proxy to the model provider that dumps every request/response pair per agent.

Each assistant's home points its provider at ``http://127.0.0.1:<port>/a/<agent>/api/v1``;
the ``/a/<agent>`` prefix is stripped before forwarding and recorded on the dump line, so
one proxy serves all four assistants and the dump still says whose call each request was.

The dump is the ground-truth transcript layer: the exact system prompt, tools array, and
raw (possibly SSE) response text — including reasoning_content, whether or not opencode
surfaces it. SSE responses are buffered whole; with one assistant acting at a time that
costs nothing.

Three upstreams:

* ``--upstream openrouter`` (default) — verbatim forward to openrouter.ai. Unchanged.
* ``--upstream azure`` — forward to the Azure OpenAI resource named by
  ``AZURE_OPENAI_ENDPOINT``, on its OpenAI-compatible ``/openai/v1`` path. opencode keeps
  speaking to its OpenRouter provider; this leg translates. Three things differ and each
  is handled here, so no harness code above knows about it:

  - **auth and address** — ``api-key`` header instead of ``Authorization: Bearer``, and
    ``/api/v1/...`` becomes ``/openai/v1/...`` (no api-version on that path);
  - **body** — Azure 400s on any unknown parameter, and the OpenRouter provider sends
    several (``usage: {include: true}`` above all). The body is filtered to
    :data:`AZURE_PARAMS`, ``max_tokens`` is renamed to ``max_completion_tokens`` (the
    gpt-5 deployments reject the former outright), and streaming requests get
    ``stream_options.include_usage`` so opencode still gets its token counts back;
  - **429s** — one deployment serving a whole roster at once is exactly the concurrency
    ceiling the sj3/sj4 judge sweeps hit. Retries with backoff live here because a 5xx
    or 429 that reaches opencode kills the turn.

  Measured against the live resource (2026-08-31, openai-sabdelnabi-1, gpt-5.4): tools,
  streaming, ``temperature: 0.7`` and ``reasoning_effort`` all accepted;
  ``max_tokens``, ``usage``, ``parallel_tool_calls`` and ``stream_options`` on a
  non-streaming call all rejected 400. **No reasoning content comes back** — that is a
  property of the deployment, so ``steps_detail[].reasoning`` will be empty for these
  runs and the private debrief is the only introspective surface left.

* ``--upstream bifrost`` — the institute's AI Gateway (Bifrost,
  https://bifrost.is.localnet/openai, key ``sk-bf-…`` as a Bearer token), which fronts
  Azure/OpenAI/Anthropic behind one OpenAI-compatible API; ``model`` is a gateway id such
  as ``azure/gpt-5.5``. Same body translation as the azure leg — the gateway forwards to
  the same Azure deployments, so the same parameters are rejected — but OpenAI-style
  Bearer auth and ``/api/v1``→``/v1`` under the gateway's base path. Its TLS is signed by
  the institute's internal root CA, which is not in certifi: pass ``--ca-bundle``
  (cluster/mpi_is_ca.pem) or the handshake fails.

  Probed 2026-08-31 on azure/gpt-5.5: tools, streaming and `max_tokens` fine, unknown
  params tolerated rather than 400'd, but **temperature must be 1** ("Unsupported value:
  'temperature' does not support 0.7 with this model") — unlike the direct gpt-5.4
  deployment, which accepts 0.7. Set `temperature: 1.0` in the config for gateway
  gpt-5.x cells. No reasoning content, same as the direct leg.

Run:  python experiments/agent4/proxy.py --out <dir> [--port 8899] [--upstream azure]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import certifi
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

UPSTREAM = "https://openrouter.ai"
HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}

DUMP: Path = None  # type: ignore[assignment]

# opencode stamps the real wall-clock date into its <env> block ("Today's date: Sat Aug
# 29 2026", JS toDateString format) — model-visible, and it contradicts a simulated
# calendar. --spoof-date rewrites it to the simulated date before forwarding; the dump
# records the rewritten body (what the model actually saw).
SPOOF_DATE: bytes | None = None
_DATE_RE = re.compile(rb"Today's date: [A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{4}")

# ----------------------------------------------------------------------- azure
MODE = "openrouter"
#: OpenRouter provider pin, or "" for the router's own choice — the default, and what
#: every pre-w1 cell ran with.
PIN_PROVIDER = ""
UPSTREAM_BASE = ""   # azure resource / gateway base, set from the environment
VERIFY: Any = True   # httpx verify: True, or a path to a CA bundle (--ca-bundle)

#: Where each translated upstream lives and how it authenticates. The body translation is
#: shared: the gateway fronts the same Azure deployments, so it rejects the same params.
UPSTREAMS = {
    #  mode:     (env var for the base, default base,                 auth header)
    "azure":   ("AZURE_OPENAI_ENDPOINT", "",                                 "api-key"),
    "bifrost": ("BIFROST_BASE_URL",      "https://bifrost.is.localnet/openai", "bearer"),
}

#: ``/api/v1`` (what opencode's OpenRouter provider calls) rewritten per upstream.
API_PREFIX = {"azure": "/openai/v1", "bifrost": "/v1"}

#: Body parameters the Azure OpenAI ``/openai/v1/chat/completions`` path accepts. A
#: whitelist, not a blacklist: the failure mode to avoid is a 400 on a parameter some
#: future opencode version starts sending, and a dropped parameter is recoverable
#: (the dump records what was dropped) while a dead turn is not.
AZURE_PARAMS = {
    "model", "messages", "tools", "tool_choice", "stream", "stream_options",
    "temperature", "top_p", "max_completion_tokens", "stop", "seed", "n",
    "response_format", "reasoning_effort", "presence_penalty", "frequency_penalty",
    "logit_bias", "logprobs", "top_logprobs", "user", "metadata", "store", "modalities",
}

#: Status codes worth a retry, and the backoff ladder (seconds) between attempts. Azure
#: only: adding retries to the OpenRouter leg would change how every existing cell
#: behaves under load.
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
RETRY_BACKOFF = (2, 5, 10, 20, 40)


#: Where each upstream's credential comes from.
KEY_VAR = {"azure": "AZURE_OPENAI_API_KEY", "bifrost": "BIFROST_API_KEY"}


def translate(path: str, headers: Dict[str, str], body: bytes) -> Tuple[str, Dict[str, str], bytes, Dict[str, Any]]:
    """Rewrite an OpenAI-compatible OpenRouter call for the Azure or gateway upstream."""
    url = UPSTREAM_BASE + re.sub(r"^/api/v1", API_PREFIX[MODE], path)
    headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "api-key")}
    key = os.environ[KEY_VAR[MODE]]
    if UPSTREAMS[MODE][2] == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["api-key"] = key
    dropped: Dict[str, Any] = {}
    if not body:
        return url, headers, body, dropped
    try:
        payload: Dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return url, headers, body, dropped
    if not isinstance(payload, dict):
        return url, headers, body, dropped
    if "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    out = {}
    for k, v in payload.items():
        if k in AZURE_PARAMS:
            out[k] = v
        else:
            # Value and all: opencode sends `reasoningEffort` (an ai-sdk camelCase leak)
            # that OpenRouter ignores too, and a dropped parameter must stay auditable.
            dropped[k] = v
    if out.get("stream"):
        # OpenRouter's `usage: {include: true}` is what opencode sends and what was just
        # dropped; this is the OpenAI spelling of the same request.
        out["stream_options"] = {"include_usage": True}
    else:
        out.pop("stream_options", None)
    return url, headers, json.dumps(out).encode(), dropped


def pin_provider(body: bytes) -> bytes:
    """Route an OpenRouter call to one backend, with no fallback.

    Unpinned, the router scatters across a dozen backends of differing speed and
    quantization. agent2/JUDGE_OPERATIONS.md records the failure mode it produces: under
    scatter there are no 429s and no errors, just answers that never come, and it is the
    heaviest turns that cross the timeout and die — so a slow pool selectively destroys
    the most interesting rollouts. Fallbacks stay off on purpose: a degraded pin should
    fail fast and visibly rather than silently reroute into the scatter it was meant to
    prevent.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if not isinstance(payload, dict):
        return body
    payload["provider"] = {"order": [PIN_PROVIDER], "allow_fallbacks": False}
    return json.dumps(payload).encode()


def retry_delay(response: httpx.Response, attempt: int) -> float:
    try:
        return min(float(response.headers.get("retry-after", "")), 60.0)
    except ValueError:
        return float(RETRY_BACKOFF[attempt])


async def proxy(request: Request) -> Response:
    path = request.url.path
    agent = ""
    if (m := re.match(r"^/a/([^/]+)(/.*)$", path)):
        agent, path = m.group(1), m.group(2)
    body = await request.body()
    if SPOOF_DATE and body:
        body = _DATE_RE.sub(b"Today's date: " + SPOOF_DATE, body)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    dropped: Dict[str, Any] = {}
    if MODE != "openrouter":
        if request.method == "GET" and path == "/":
            return Response("ok")  # the runner's readiness probe; no such route upstream
        url, headers, body, dropped = translate(path, headers, body)
        backoff = RETRY_BACKOFF
    else:
        url, backoff = UPSTREAM + path, ()
        if PIN_PROVIDER and body:
            body = pin_provider(body)

    statuses: List[int] = []
    async with httpx.AsyncClient(timeout=600, verify=VERIFY) as client:
        for attempt in range(len(backoff) + 1):
            upstream = await client.request(
                request.method, url,
                params=dict(request.query_params), content=body, headers=headers,
            )
            statuses.append(upstream.status_code)
            if upstream.status_code not in RETRY_STATUS or attempt == len(backoff):
                break
            await asyncio.sleep(retry_delay(upstream, attempt))
    try:
        req_json = json.loads(body) if body else None
    except json.JSONDecodeError:
        req_json = {"_raw": body.decode(errors="replace")[:2000]}
    line = {
        "wall": time.time(), "agent": agent, "path": path,
        "status": upstream.status_code, "request": req_json,
        # SSE overhead is ~30x the payload (one ~350-byte data line per reasoning
        # fragment); a 400k cap silently cut the tool-call chunks off one v15 step.
        "response_text": upstream.text[:8_000_000],
    }
    if MODE != "openrouter":
        line["upstream"] = MODE
    if len(statuses) > 1:
        line["attempts"] = statuses
    if dropped:
        line["dropped_params"] = dropped
    with DUMP.open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        headers={"content-type": upstream.headers.get("content-type", "application/json")},
    )


def main() -> None:
    global DUMP, SPOOF_DATE, MODE, UPSTREAM_BASE, VERIFY, PIN_PROVIDER
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--upstream", default="openrouter",
                    choices=["openrouter", "azure", "bifrost"],
                    help="where to forward: openrouter.ai, the Azure OpenAI resource in "
                         "AZURE_OPENAI_ENDPOINT, or the institute AI Gateway "
                         "(translated, see module docstring)")
    ap.add_argument("--ca-bundle", default=None,
                    help="extra CA certificates to trust upstream (the gateway is signed "
                         "by the institute root CA, which certifi does not carry)")
    ap.add_argument("--pin-provider", default="",
                    help="OpenRouter backend to pin every call to, e.g. GMICloud, with "
                         "fallbacks off. Ignored for the azure/bifrost upstreams.")
    ap.add_argument("--spoof-date", default=None,
                    help="ISO date the simulation is set on; rewrites opencode's "
                         "env-block 'Today's date' line in outbound request bodies")
    args = ap.parse_args()
    MODE = args.upstream
    PIN_PROVIDER = args.pin_provider
    if MODE != "openrouter":
        base_var, base_default, _ = UPSTREAMS[MODE]
        for var in (KEY_VAR[MODE], *([] if base_default else [base_var])):
            if not os.environ.get(var):
                raise SystemExit(f"--upstream {MODE} needs {var} in the environment")
        UPSTREAM_BASE = (os.environ.get(base_var) or base_default).rstrip("/")
    if args.ca_bundle:
        # certifi's roots PLUS the private one, so public TLS keeps working.
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.load_verify_locations(cafile=args.ca_bundle)
        VERIFY = ctx
    if args.spoof_date:
        SPOOF_DATE = datetime.fromisoformat(args.spoof_date).strftime("%a %b %d %Y").encode()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    DUMP = out / "proxy_dump.jsonl"
    app = Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST"])])
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
