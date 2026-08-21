from __future__ import annotations

"""Azure OpenAI as an agent1 model provider.

Deliberately a thin subclass of :class:`OpenRouterClient` rather than a second implementation.
The probe that motivated this (see below) established that the Azure resource serves the
OpenAI-compatible ``/openai/v1/chat/completions`` path — same ``messages``, same ``tools``,
same ``tool_calls`` — so everything agent1 actually leans on is already written and tested:
the wall-clock-capped POST, the retry ladder, the tool-call round-trip that keeps a turn's
steps in order, and the reasoning capture. Only three things genuinely differ, and each is
one override:

* **auth and address** — ``api-key`` header, and the deployment name goes in the body as
  ``model`` while the URL carries no api-version at all on the v1 path;
* **token cap** — ``max_tokens`` is rejected outright by the gpt-5 deployments
  ("Unsupported parameter … Use 'max_completion_tokens' instead"), so the payload is
  translated on the way out;
* **cost** — Azure never returns one, so it is computed from a price table and injected into
  ``usage.cost``, which is the field agent1's step capture already reads for OpenRouter runs.
  An unpriced deployment records tokens with cost 0.0 rather than guessing.

Measured against the live resource (2026-08-15, endpoint openai-sabdelnabi-1):

    deployment  tool_calls  reasoning_content
    gpt-5.4     yes         no
    gpt-5.2     yes         no
    gpt-4.1     yes         no

**No chain-of-thought comes back from any of them.** OpenRouter runs carry per-step CoT in
``steps_detail[].reasoning`` and several agent1 findings were read off it; on Azure that field
will be empty, and the private debrief (the assistant's plain text to its employee) is the only
introspective surface left. That is a property of the provider, not a bug here.

Credentials come from the environment, same names the sj3/sj4 judges use:

    AZURE_OPENAI_ENDPOINT   https://<resource>.openai.azure.com
    AZURE_OPENAI_API_KEY    <key>
    AZURE_OPENAI_API_VERSION  optional; only used on the classic deployments path

On the cluster: ``source /fast/jtaraz/syco-bench/.env``.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from experiments.social_jira2.openrouter_client import OpenRouterClient

#: USD per 1M tokens, (input, output, cached_input), by deployment. Checked against published
#: Azure rates on 2026-08-15; cached_input may be None when no cached rate is known.
#:
#: NOTE — social_jira4.llm.AZURE_PRICES prices gpt-5.4 at (1.25, 10.0). That is the gpt-5
#: flagship rate, not gpt-5.4's, so every gpt-5.4 cost sj4 has recorded is understated by
#: roughly half. Deliberately not "fixed" by copying that table here.
#:
#: A deployment absent from the table records tokens with cost 0.0 rather than a guess, which
#: is why gpt-5.2 is not listed — no published rate was found for it. Override at runtime with
#: AZURE_PRICE_<DEPLOYMENT>="<in>,<out>[,<cached_in>]" (dots and dashes become underscores).
AZURE_PRICES: Dict[str, Tuple[float, float, Optional[float]]] = {
    "gpt-5.4": (2.50, 15.0, 0.25),
    "gpt-5": (1.25, 10.0, None),
    "gpt-4.1": (2.00, 8.00, None),
}


def azure_price(deployment: str) -> Optional[Tuple[float, float, Optional[float]]]:
    env = os.getenv("AZURE_PRICE_" + str(deployment).replace("-", "_").replace(".", "_").upper())
    if env:
        try:
            parts = [float(x) for x in env.split(",")]
            if len(parts) == 2:
                return (parts[0], parts[1], None)
            if len(parts) == 3:
                return (parts[0], parts[1], parts[2])
        except ValueError:
            pass
    return AZURE_PRICES.get(str(deployment))


class AzureOpenAIClient(OpenRouterClient):
    """OpenRouterClient pointed at an Azure OpenAI resource.

    ``super().__init__`` is not called: it demands an OPENROUTER_API_KEY that has nothing to do
    with this path. The attributes the inherited methods actually touch are set here instead,
    which is the whole contract between the two classes.
    """

    def __init__(
        self,
        *,
        deployment: str,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        use_v1_path: bool = True,
        request_timeout: int = 300,
        connect_timeout: int = 30,
        total_timeout: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        load_dotenv(override=True)
        resolved_endpoint = (endpoint or os.getenv("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
        resolved_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        if not resolved_endpoint or not resolved_key:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY not set. On the cluster: "
                "`source /fast/jtaraz/syco-bench/.env` before launching."
            )

        self.deployment = str(deployment)
        self.api_version = api_version or os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        self.use_v1_path = bool(use_v1_path)
        # `_post` appends "/chat/completions", so base_url is everything before that. The v1
        # path takes the deployment in the body and needs no api-version; the classic path
        # names the deployment in the URL and requires one (added in `_post`).
        self.base_url = (
            f"{resolved_endpoint}/openai/v1" if self.use_v1_path
            else f"{resolved_endpoint}/openai/deployments/{self.deployment}"
        )
        self.endpoint = resolved_endpoint
        self.api_key = resolved_key
        self.request_timeout = int(request_timeout)
        self.connect_timeout = int(connect_timeout)
        self.total_timeout = int(total_timeout) if total_timeout else max(self.request_timeout, 600)
        self.extra_headers = dict(extra_headers or {})
        self.session = requests.Session()
        self._reasoning_steps: List[Dict[str, Any]] = []
        self.price = azure_price(self.deployment)

    # ------------------------------------------------------------------ wire
    def _build_headers(self) -> Dict[str, str]:
        headers = {"api-key": self.api_key, "Content-Type": "application/json"}
        headers.update(self.extra_headers)
        return headers

    def _post(self, payload: Dict[str, Any]) -> requests.Response:
        """Translate an OpenRouter-shaped payload to Azure's, then post it unchanged upstream.

        Doing it here rather than in ``generate_response`` is what keeps the inherited retry
        ladder, wall-clock cap and session recycling in play — the only thing this method owns
        is the shape of the request body.
        """
        body = dict(payload)
        body["model"] = self.deployment          # the deployment IS the model on this path
        # gpt-5.x rejects max_tokens outright; 4.1 accepts both. Always send the new spelling.
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        # OpenRouter-only fields. `usage: {include}` asks OpenRouter to price the call (Azure
        # prices nothing) and `provider` is its routing block; both 400 here.
        body.pop("usage", None)
        body.pop("provider", None)
        # OpenRouter wraps the effort knob; Azure takes it flat, as OpenAI does.
        reasoning = body.pop("reasoning", None)
        if isinstance(reasoning, dict) and reasoning.get("effort"):
            body["reasoning_effort"] = reasoning["effort"]

        if not self.use_v1_path:
            return self._post_classic(body)
        return super()._post(body)

    def _post_classic(self, body: Dict[str, Any]) -> requests.Response:
        """The api-version-bearing deployments path, for a resource without ``/openai/v1``."""
        url = f"{self.base_url}/chat/completions?api-version={self.api_version}"
        return self.session.post(
            url, headers=self._build_headers(), json=body,
            timeout=(self.connect_timeout, self.request_timeout),
        )

    # ----------------------------------------------------------------- costs
    def _cost(self, usage: Dict[str, Any]) -> float:
        """Priced from the table, with cached input billed at its own rate when reported.

        Caching is not a rounding error here: an agent1 turn resends the whole conversation
        every step, so most of an agent1 prompt is a prefix Azure has already seen. gpt-5.4
        charges $0.25 per 1M for a cache hit against $2.50 for a miss, so ignoring the
        `cached_tokens` Azure reports would overstate the bill of a long run several-fold.
        """
        if not self.price:
            return 0.0
        pin, pout, pcached = self.price
        prompt = float(usage.get("prompt_tokens") or 0)
        completion = float(usage.get("completion_tokens") or 0)
        cached = float((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        cached = min(cached, prompt)
        fresh = prompt - cached
        cost = (fresh * pin + completion * pout) / 1_000_000.0
        # No cached rate known: bill the hits at the normal input rate, which overstates rather
        # than flatters. Reasoning tokens are already inside completion_tokens and Azure bills
        # them at the output rate, so they need no separate term.
        cost += cached * (pcached if pcached is not None else pin) / 1_000_000.0
        return cost

    def generate_response(
        self, input: List[Dict[str, Any]], params: Dict[str, Any],  # noqa: A002
    ) -> Tuple[Dict[str, Any], str]:
        response, text = super().generate_response(input, params)
        usage = (response or {}).get("usage")
        if isinstance(usage, dict) and "cost" not in usage:
            # `agent.py`'s step capture reads usage["cost"] and writes it into the turn record;
            # filling it here is what makes an Azure run's cost show up like any other.
            usage["cost"] = self._cost(usage)
        return response, text
