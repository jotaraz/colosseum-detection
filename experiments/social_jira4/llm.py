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
from typing import Any, Callable, Dict

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
    temperature: float = 0.0,   # judges are deterministic
    timeout: int = 180,
) -> Caller:
    """The caller the 3 critics + validator use — DeepSeek-V4-Pro via OpenRouter by default.

    Tracked (``.last_reasoning`` / ``.last_usage``, both thread-local) so every judge verdict can
    be recorded with the reasoning behind it and its token cost. The critic shares one instance
    across its thread pool; see ``_TrackingCaller`` for why that is safe."""
    return _tracking_caller(provider=provider, model=model, max_tokens=max_tokens,
                            temperature=temperature, timeout=timeout)


_EMPTY_USAGE: Dict[str, Any] = {}


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

    def __init__(self, model: str, max_tokens: int, temperature: float):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._local = threading.local()
        self._lock = threading.Lock()
        self.totals: Dict[str, int] = {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

    # last_* are per-thread views of the most recent call made *by this thread*.
    @property
    def last_reasoning(self) -> str:
        return getattr(self._local, "reasoning", "")

    @property
    def last_usage(self) -> Dict[str, Any]:
        return getattr(self._local, "usage", _EMPTY_USAGE)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.totals)

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        from experiments.social_jira2.openrouter_client import OpenRouterClient
        client = OpenRouterClient()
        messages = OpenRouterClient.init_context(system_prompt, user_prompt)
        params = {"model": self.model, "max_completion_tokens": self.max_tokens,
                  "temperature": self.temperature}
        data, response_str = client.generate_response(messages, params)
        steps = getattr(client, "_reasoning_steps", None) or []
        self._local.reasoning = "\n".join(
            str(s.get("reasoning_content") or "") for s in steps
        ).strip()
        usage = (data or {}).get("usage") or {}
        self._local.usage = usage
        with self._lock:
            self.totals["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                v = usage.get(k)
                if isinstance(v, (int, float)):
                    self.totals[k] += int(v)
        return response_str


class _UntrackedCaller:
    """Same surface as ``_TrackingCaller`` for providers whose client we do not instrument
    (Azure): the reasoning/usage accessors exist but stay empty, so callers never branch."""

    def __init__(self, base: Caller):
        self._base = base
        self.last_reasoning = ""
        self.last_usage: Dict[str, Any] = {}
        self.totals: Dict[str, int] = {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

    def snapshot(self) -> Dict[str, int]:
        return dict(self.totals)

    def __call__(self, s: str, u: str) -> str:
        self.totals["calls"] += 1
        return self._base(s, u)


# Back-compat alias: the prompter caller was named this before usage tracking was added.
_ReasoningCaller = _TrackingCaller


def _tracking_caller(
    *, provider: str, model: str, max_tokens: int, temperature: float, timeout: int
) -> Caller:
    """A caller exposing ``.last_reasoning`` / ``.last_usage`` / ``.snapshot()``. OpenRouter
    captures both; other providers fall back to the untracked shim."""
    prov = (provider or "auto").lower()
    if prov == "auto":
        prov = "azure" if os.getenv("AZURE_OPENAI_ENDPOINT") else "openrouter"
    if prov == "openrouter":
        return _TrackingCaller(model, max_tokens, temperature)
    return _UntrackedCaller(
        make_caller(provider=prov, model=model, max_tokens=max_tokens,
                    temperature=temperature, timeout=timeout)
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
