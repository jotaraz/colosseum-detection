from __future__ import annotations

"""A minimal ``(system, user) -> str`` LLM caller for the prompter (and, later, the real
critic / checks-and-balances).

Deliberately tiny and self-contained rather than reusing ``judge.make_judge_caller`` — that one
does a bare ``from openrouter_client import ...`` which only resolves when a sibling experiment
dir happens to be on ``sys.path``. Here we import the client fully-qualified so social_jira4 has
no implicit path dependency. Provider is chosen by env: ``azure`` if Azure vars are set, else
``openrouter`` (the repo-local client).
"""

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

Caller = Callable[[str, str], str]

# ---- default model-role wiring (first tests) ----------------------------------------------
# Target (assistants): Qwen/Qwen3.6-35B-A3B via OpenRouter — see configs/…_openrouter.yaml.
# Judges (the 3 critics + validator): DeepSeek-V4-Pro via OpenRouter.
# Prompter: unchanged (make_caller default; sonnet-4.5 via OpenRouter unless Azure env is set).
# Confirm the exact OpenRouter slugs against https://openrouter.ai/models.
DEFAULT_TARGET_CONFIG = (
    "experiments/social_jira4/configs/social_jira4_qwen3_6_35b_a3b_openrouter.yaml"
)
DEFAULT_TARGET_LABEL = "openrouter-qwen3.6-35b-a3b"
DEFAULT_JUDGE_PROVIDER = "openrouter"
DEFAULT_JUDGE_MODEL = os.getenv("JUDGE_MODEL", "deepseek/deepseek-v4-pro")
# DeepSeek-V4-Pro is adaptive-thinking: with the reasoning knob unset it decides per request
# whether to emit a chain-of-thought, and measured across the v3 runs it declined on ~4% of critic
# calls and ~19% of validator/consistency calls. The critics degrade gracefully (a no-CoT verdict
# is terse but still span-grounded); the consistency gate does not — with no reasoning channel it
# deliberates *inside* its own JSON string fields, after the verdict key has already been emitted,
# and 3 of the 4 consistency rejections in the v3 era came from no-CoT calls. So ask explicitly.
# "medium" is the value spelled out in the target's own fix (see the deepseek-v4-flash CoT commit);
# `reasoning_effort` is the only spelling openrouter_client forwards (-> reasoning.effort).
DEFAULT_JUDGE_REASONING_EFFORT = os.getenv("JUDGE_REASONING_EFFORT", "medium")
# Prompter (the optimizer) — DeepSeek-V4-Pro via OpenRouter, same as the judges/referee.
DEFAULT_PROMPTER_MODEL = os.getenv("PROMPTER_MODEL", "deepseek/deepseek-v4-pro")


def make_caller(
    *,
    provider: str = "auto",
    model: str = "",
    max_tokens: int = 4096,
    temperature: float = 1.0,
    timeout: int = 120,
) -> Caller:
    """Return a ``(system_prompt, user_prompt) -> response_str`` callable."""
    prov = (provider or "auto").lower()
    if prov == "auto":
        prov = "azure" if os.getenv("AZURE_OPENAI_ENDPOINT") else "openrouter"

    if prov == "openrouter":
        from experiments.social_jira2.openrouter_client import OpenRouterClient

        model = model or DEFAULT_PROMPTER_MODEL
        params = {"model": model, "max_completion_tokens": max_tokens, "temperature": temperature}

        def _call_openrouter(system_prompt: str, user_prompt: str) -> str:
            client = OpenRouterClient()
            messages = OpenRouterClient.init_context(system_prompt, user_prompt)
            _, response_str = client.generate_response(messages, params)
            return response_str

        return _call_openrouter

    if prov == "azure":
        # Reuse jira3's tested Azure path (no fragile bare import involved there).
        from experiments.social_jira3.judge import _azure_chat

        model = model or os.getenv("PROMPTER_MODEL", "gpt-4.1")

        def _call_azure(system_prompt: str, user_prompt: str) -> str:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            return _azure_chat(
                messages, deployment=model, max_completion_tokens=max_tokens,
                timeout=timeout, max_retries=8,
            )

        return _call_azure

    raise ValueError(f"unknown provider {provider!r}")


def make_judge_caller(
    *,
    provider: str = DEFAULT_JUDGE_PROVIDER,
    model: str = DEFAULT_JUDGE_MODEL,
    max_tokens: int = 4096,
    temperature: Optional[float] = 0.0,   # judges are deterministic
    timeout: int = 180,
    reasoning_effort: str = DEFAULT_JUDGE_REASONING_EFFORT,
    provider_routing: Optional[Dict[str, Any]] = None,
) -> Caller:
    """The caller the 3 critics + validator + consistency gate use — DeepSeek-V4-Pro via
    OpenRouter by default.

    Tracked (``.last_reasoning`` / ``.last_usage``, both thread-local) so every judge verdict can
    be recorded with the reasoning behind it and its token cost. The critic shares one instance
    across its thread pool; see ``_TrackingCaller`` for why that is safe.

    ``reasoning_effort`` is requested explicitly rather than left to the model's own adaptive
    choice — see ``DEFAULT_JUDGE_REASONING_EFFORT``. Pass ``""`` to restore the old behaviour of
    sending no reasoning knob at all.

    ``temperature=None`` sends NO temperature at all, which some models require rather than merely
    tolerate: Claude Sonnet 5 removed the sampling parameters, so any non-default value is a 400 and
    "deterministic judging" is simply not on offer there (OpenRouter's endpoint metadata for
    ``anthropic/claude-sonnet-5`` lists no ``temperature`` among its supported parameters). The 0.0
    default is unchanged for every caller that does not ask.

    ``provider_routing`` is OpenRouter's routing object, forwarded verbatim — see
    ``_TrackingCaller.__call__``. Ignored on Azure, which has one upstream by construction."""
    return _tracking_caller(provider=provider, model=model, max_tokens=max_tokens,
                            temperature=temperature, timeout=timeout,
                            reasoning_effort=reasoning_effort,
                            provider_routing=provider_routing)


_EMPTY_USAGE: Dict[str, Any] = {}

# ---- cost accounting ------------------------------------------------------------------------
# OpenRouter prices each call itself: with ``usage: {include: true}`` on the request (see
# openrouter_client.generate_response) the reply's ``usage.cost`` is the amount actually charged,
# in credits (1 credit == 1 USD). Nothing is estimated for that provider.
#
# Azure returns token counts but never a price, so its cost has to be computed from a table.
# USD per 1M tokens, (input, output), keyed by deployment name; override at runtime with
# AZURE_PRICE_<DEPLOYMENT>="<in>,<out>" (dots/dashes in the name become underscores) when a
# deployment is billed at a rate this table does not know. An unknown deployment records tokens
# with cost_usd 0.0 and is listed under ``cost.unpriced`` in the run's cost.json, so a missing
# price is visible rather than silently free.
AZURE_PRICES: Dict[str, tuple] = {
    "gpt-5.4": (1.25, 10.0),
}


def azure_price(deployment: str) -> Optional[tuple]:
    env = os.getenv("AZURE_PRICE_" + str(deployment).replace("-", "_").replace(".", "_").upper())
    if env:
        try:
            a, b = (float(x) for x in env.split(","))
            return (a, b)
        except ValueError:
            pass
    return AZURE_PRICES.get(str(deployment))


def _blank_totals() -> Dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0}


class _TrackingCaller:
    """A caller (OpenRouter) that also captures, per call, the model's **reasoning channel** and
    its **token usage**, so the prompter's chain-of-thought and each judge's cost can be recorded
    against the step that produced them.

    Per-call results are held in ``threading.local`` storage, NOT on the instance: ``LlmCritic``
    fans the three judges out over a ``ThreadPoolExecutor`` sharing ONE caller, so instance
    attributes would race and attribute the wrong CoT to the wrong turn. Each worker thread reads
    back exactly what it just produced. ``totals`` is the cumulative counter across all threads
    (mutated under a lock); the loop snapshots it either side of a step to get per-step cost.
    """

    def __init__(self, model: str, max_tokens: int, temperature: Optional[float],
                 reasoning_effort: str = "",
                 provider_routing: Optional[Dict[str, Any]] = None):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.provider_routing = provider_routing
        self._local = threading.local()
        self._lock = threading.Lock()
        self.totals: Dict[str, Any] = _blank_totals()
        self.provider = "openrouter"

    # last_* are per-thread views of the most recent call made *by this thread*.
    @property
    def last_reasoning(self) -> str:
        return getattr(self._local, "reasoning", "")

    @property
    def last_usage(self) -> Dict[str, Any]:
        return getattr(self._local, "usage", _EMPTY_USAGE)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.totals)

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        from experiments.social_jira2.openrouter_client import OpenRouterClient
        client = OpenRouterClient()
        messages = OpenRouterClient.init_context(system_prompt, user_prompt)
        params = {"model": self.model, "max_completion_tokens": self.max_tokens}
        # None means "send no temperature", which is not the same as 0.0: a model that removed the
        # sampling parameters (Claude Sonnet 5) rejects any non-default value, so the field has to
        # be absent from the payload rather than present-and-zero. generate_response only forwards
        # a temperature that is not None, so omitting the key here is what reaches the wire.
        if self.temperature is not None:
            params["temperature"] = self.temperature
        # Only when set: the client forwards any non-None value, so "" would go out as
        # reasoning.effort="" rather than as "no knob requested".
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        # OpenRouter provider routing, forwarded verbatim (see openrouter_client.generate_response
        # for why an unpinned run is not reproducible). Pinning is the whole point of passing it, so
        # the caller is expected to set ``allow_fallbacks: false`` and accept a loud 404 over a
        # silent reroute to a different upstream.
        if self.provider_routing is not None:
            params["provider"] = self.provider_routing
        data, response_str = client.generate_response(messages, params)
        steps = getattr(client, "_reasoning_steps", None) or []
        self._local.reasoning = "\n".join(
            str(s.get("reasoning_content") or "") for s in steps
        ).strip()
        usage = dict((data or {}).get("usage") or {})
        # OpenRouter's own charge for this call, in credits (== USD), present because the client
        # sends ``usage: {include: true}``. Normalised to ``cost_usd`` so a step's usage block reads
        # the same whatever the provider.
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            usage["cost_usd"] = float(cost)
        # Which upstream actually served this call. Recorded because a provider PIN is otherwise
        # unfalsifiable after the fact: the request carries the intent, only the reply carries the
        # outcome, and a run whose reproducibility argument rests on the pin should be able to show
        # the pin held rather than assert it.
        served_by = (data or {}).get("provider")
        if served_by:
            usage["provider_name"] = served_by
        # Why an EMPTY completion was empty. Without this a 200 carrying no content is
        # indistinguishable from a 200 carrying a policy refusal: both reach the caller as "" and
        # both surface downstream as an unparseable-JSON error, which is the one failure mode that
        # says nothing about its own cause. Measured on the v4 meta-judge sweep, claude-sonnet-5
        # returned empty content on ~31% of calls while three other seats returned none at all, and
        # the artifacts could not say whether that was a refusal, a truncation or a bad hop.
        choices = (data or {}).get("choices") or []
        if choices:
            ch0 = choices[0] or {}
            finish = ch0.get("finish_reason") or ch0.get("native_finish_reason")
            if finish:
                usage["finish_reason"] = finish
            refusal = (ch0.get("message") or {}).get("refusal")
            if refusal:
                usage["refusal"] = str(refusal)[:500]
        else:
            usage["no_choices"] = True
        if not (response_str or "").strip():
            usage["empty_content"] = True
        self._local.usage = usage
        with self._lock:
            self.totals["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                v = usage.get(k)
                if isinstance(v, (int, float)):
                    self.totals[k] += int(v)
            if isinstance(cost, (int, float)):
                self.totals["cost_usd"] = round(self.totals["cost_usd"] + float(cost), 8)
        return response_str


class _UntrackedCaller:
    """Same surface as ``_TrackingCaller`` for providers whose client we do not instrument: the
    reasoning/usage accessors exist but stay empty, so callers never branch. Nothing routes here
    today — Azure has its own tracking caller below — but a new provider can land safely."""

    def __init__(self, base: Caller):
        self._base = base
        self.last_reasoning = ""
        self.last_usage: Dict[str, Any] = {}
        self.totals: Dict[str, Any] = _blank_totals()
        self.provider = "unknown"

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.totals)

    def __call__(self, s: str, u: str) -> str:
        self.totals["calls"] += 1
        return self._base(s, u)


class _AzureTrackingCaller:
    """Azure OpenAI chat-completions with token accounting and a priced cost.

    ``social_jira3._azure_chat`` — the path the Azure judges used until now — drops the response's
    ``usage`` block, and social_jira3 is frozen (see ``target_run.py``), so this issues its own
    request rather than patching it. Same conventions: deployment + api-version from the env,
    ``max_completion_tokens``, retry on 429/5xx with a Retry-After-aware backoff.

    Cost is COMPUTED (Azure never returns one) from :func:`azure_price`; an unpriced deployment
    still records its tokens and leaves ``cost_usd`` at 0, flagged via ``unpriced``. Reasoning
    tokens, when the deployment reports them, are billed at the output rate — that is how Azure
    charges for them and they are already included in ``completion_tokens``.

    Thread-local ``last_*`` like ``_TrackingCaller``: the meta-gate panel calls its judges
    concurrently through one caller per judge, and the critic shares one across its pool.
    """

    def __init__(self, deployment: str, max_tokens: int, timeout: int = 180,
                 max_retries: int = 40, retry_budget_s: float = 1200.0):
        self.model = deployment
        self.max_tokens = max_tokens
        self.timeout = timeout
        # Patience, deliberately large. A rate-limit window on a shared deployment is transient,
        # but a run that gives up on one kills HOURS of GPU work — three v4b jobs died on their
        # FIRST prompter call because 8 retries x retry-after 30s tolerated only ~4 minutes. The
        # budget is wall-clock, so honouring a long retry-after cannot silently blow past it.
        self.max_retries = max_retries
        self.retry_budget_s = retry_budget_s
        self.provider = "azure"
        self.price = azure_price(deployment)      # (usd_per_1M_in, usd_per_1M_out) or None
        self._local = threading.local()
        self._lock = threading.Lock()
        self.totals: Dict[str, Any] = _blank_totals()
        self.totals["unpriced"] = self.price is None

    @property
    def last_reasoning(self) -> str:
        return getattr(self._local, "reasoning", "")

    @property
    def last_usage(self) -> Dict[str, Any]:
        return getattr(self._local, "usage", _EMPTY_USAGE)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.totals)

    def _cost(self, usage: Dict[str, Any]) -> float:
        if not self.price:
            return 0.0
        pin, pout = self.price
        p = float(usage.get("prompt_tokens") or 0)
        c = float(usage.get("completion_tokens") or 0)
        return (p * pin + c * pout) / 1_000_000.0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        import random
        import requests

        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        if not endpoint or not api_key:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY not set "
                "(on the cluster: source /fast/jtaraz/syco-bench/.env)."
            )
        url = (f"{endpoint.rstrip('/')}/openai/deployments/{self.model}"
               f"/chat/completions?api-version={api_version}")
        body: Dict[str, Any] = {
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
        }
        if self.max_tokens > 0:
            body["max_completion_tokens"] = int(self.max_tokens)

        last_err = None
        deadline = time.time() + self.retry_budget_s

        def _wait(delay: float) -> bool:
            """Sleep before the next attempt; False when the retry budget is spent."""
            if time.time() + delay > deadline:
                return False
            time.sleep(delay)
            return True

        for attempt in range(self.max_retries):
            backoff = min(2.0 * (2 ** attempt), 60.0) + random.uniform(0, 2.0)
            try:
                r = requests.post(url, headers={"api-key": api_key,
                                                "Content-Type": "application/json"},
                                  json=body, timeout=self.timeout)
                if r.status_code == 429:
                    ra = r.headers.get("retry-after") or r.headers.get("Retry-After")
                    try:
                        delay = float(ra)
                    except (TypeError, ValueError):
                        delay = backoff
                    last_err = f"HTTP 429 rate-limited (retry-after={ra})"
                    if not _wait(delay + random.uniform(0, 1.0)):
                        break
                    continue
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                    if not _wait(backoff):
                        break
                    continue
                data = r.json()
                if "error" in data:
                    last_err = f"api error: {data['error']}"
                    time.sleep(backoff)
                    continue
                choices = data.get("choices") or []
                if not choices:
                    last_err = f"no choices: {str(data)[:300]}"
                    if not _wait(backoff):
                        break
                    continue
            except Exception as exc:  # noqa: BLE001 — retry any transport/parse error
                last_err = f"{type(exc).__name__}: {exc}"
                if not _wait(backoff):
                    break
                continue

            message = choices[0].get("message", {}) or {}
            usage = dict(data.get("usage") or {})
            usage["cost_usd"] = self._cost(usage)
            self._local.usage = usage
            # Some deployments surface the CoT summary here; absent for most, hence "" not None.
            self._local.reasoning = str(message.get("reasoning_content") or "")
            with self._lock:
                self.totals["calls"] += 1
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    v = usage.get(k)
                    if isinstance(v, (int, float)):
                        self.totals[k] += int(v)
                self.totals["cost_usd"] = round(self.totals["cost_usd"] + usage["cost_usd"], 8)
            return message.get("content") or ""

        raise RuntimeError(f"azure call failed after {attempt + 1} attempts / {self.retry_budget_s:.0f}s budget: {last_err}")


# Back-compat alias: the prompter caller was named this before usage tracking was added.
_ReasoningCaller = _TrackingCaller


def _tracking_caller(
    *, provider: str, model: str, max_tokens: int, temperature: Optional[float], timeout: int,
    reasoning_effort: str = "", provider_routing: Optional[Dict[str, Any]] = None,
) -> Caller:
    """A caller exposing ``.last_reasoning`` / ``.last_usage`` / ``.snapshot()``, with tokens and
    cost tracked for both providers — OpenRouter's charge as reported, Azure's computed from
    :data:`AZURE_PRICES`. Anything else falls back to the untracked shim.

    ``reasoning_effort`` and ``provider_routing`` are OpenRouter-only — the Azure path has no
    equivalent knob and one upstream, so both are silently ignored there (as the reasoning channel
    already is). The Azure body likewise never carried a temperature, so ``temperature=None`` is
    already that path's behaviour."""
    prov = (provider or "auto").lower()
    if prov == "auto":
        prov = "azure" if os.getenv("AZURE_OPENAI_ENDPOINT") else "openrouter"
    if prov == "openrouter":
        return _TrackingCaller(model, max_tokens, temperature, reasoning_effort, provider_routing)
    if prov == "azure":
        return _AzureTrackingCaller(model, max_tokens, timeout=timeout)
    return _UntrackedCaller(
        make_caller(provider=prov, model=model, max_tokens=max_tokens,
                    temperature=1.0 if temperature is None else temperature, timeout=timeout)
    )


def make_prompter_caller(
    *,
    provider: str = "auto",
    model: str = "",
    max_tokens: int = 8192,   # reasoning + JSON blocks; headroom over the plain caller
    temperature: float = 1.0,
    timeout: int = 180,
) -> Caller:
    """Caller for the prompter that exposes ``.last_reasoning`` (its full CoT) and ``.last_usage``."""
    return _tracking_caller(provider=provider, model=model or DEFAULT_PROMPTER_MODEL,
                            max_tokens=max_tokens, temperature=temperature, timeout=timeout)
