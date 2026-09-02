"""Pass-through proxy to openrouter that dumps every request/response pair.

opencode's provider baseURL points at http://127.0.0.1:8899/api/v1; we forward
to https://openrouter.ai/api/v1 with the caller's own Authorization header and
append each exchange to out/proxy_dump.jsonl. SSE responses are buffered whole
before being returned — fine for a smoke test, and the dump then contains the
complete stream text.

This dump is the ground truth for what opencode actually sends: the exact
system prompt, the tools array, and whether reasoning comes back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

UPSTREAM = "https://openrouter.ai"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
DUMP = OUT / "proxy_dump.jsonl"

HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}


async def proxy(request: Request) -> Response:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    async with httpx.AsyncClient(timeout=600) as client:
        upstream = await client.request(
            request.method,
            UPSTREAM + request.url.path,
            params=dict(request.query_params),
            content=body,
            headers=headers,
        )
    try:
        req_json = json.loads(body) if body else None
    except json.JSONDecodeError:
        req_json = {"_raw": body.decode(errors="replace")[:2000]}
    with DUMP.open("a") as f:
        f.write(json.dumps({
            "ts": time.time(),
            "path": request.url.path,
            "status": upstream.status_code,
            "request": req_json,
            "response_text": upstream.text[:200_000],
        }) + "\n")
    resp_headers = {"content-type": upstream.headers.get("content-type", "application/json")}
    return Response(upstream.content, status_code=upstream.status_code, headers=resp_headers)


app = Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")
