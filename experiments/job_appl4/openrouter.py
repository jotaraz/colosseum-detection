"""Minimal OpenRouter chat client for the job_appl MVP.

Deliberately standalone (plain ``requests``) instead of reusing
``social_jira2.openrouter_client``, which is coupled to the ``llm_server``
client interface this experiment doesn't need. Reads ``OPENROUTER_API_KEY``
from the environment or a ``.env`` found by walking up from this file
(matching the repo convention of a ``.env`` at the colosseum root).
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE_URL = "https://openrouter.ai/api/v1"
MAX_RETRIES = 5
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _load_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if key:
        return key
    for parent in Path(__file__).resolve().parents:
        env_file = parent / ".env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY"):
                    _, _, value = line.partition("=")
                    value = value.strip().strip("'\"")
                    if value:
                        return value
    raise RuntimeError(
        "OPENROUTER_API_KEY not set. Export it or put it in a .env at the repo root."
    )


def list_models() -> List[Dict[str, Any]]:
    """Fetch the catalog of models from OpenRouter, sorted by name. Each entry:
    {id, name, context_length, prompt_price, completion_price}."""
    resp = requests.get(f"{BASE_URL}/models", timeout=(10, 60))
    if not resp.ok:
        raise RuntimeError(f"OpenRouter models request failed: HTTP {resp.status_code}")
    data = resp.json().get("data") or []
    models = []
    for m in data:
        pricing = m.get("pricing") or {}
        models.append({
            "id": m.get("id"),
            "name": m.get("name") or m.get("id"),
            "context_length": m.get("context_length"),
            "prompt_price": pricing.get("prompt"),
            "completion_price": pricing.get("completion"),
        })
    models.sort(key=lambda m: (m["name"] or "").lower())
    return models


def chat(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.7,
    # generous: reasoning models (e.g. glm-4.7-flash) can burn >8k tokens on
    # CoT before emitting any content — an 8192 cap yielded EMPTY content and
    # killed runs with "failed to produce valid JSON after retries"
    max_tokens: int = 24576,
) -> Tuple[str, Dict[str, Any], str]:
    """Send a chat completion request. Returns (content, usage, reasoning) —
    reasoning is the model's chain-of-thought ("" if the model returns none)."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {_load_api_key()}",
        "Content-Type": "application/json",
    }
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/chat/completions",
                headers=headers,
                data=json.dumps(payload),
                timeout=(30, 300),
            )
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            resp = None
        else:
            if resp.ok:
                data = resp.json()
                # OpenRouter can return HTTP 200 with an embedded provider
                # error (or no choices at all) — treat that as retryable, not
                # as a legitimately empty completion.
                if data.get("error") or not data.get("choices"):
                    last_error = f"HTTP 200 with error/no choices: {str(data)[:500]}"
                else:
                    message = (data.get("choices") or [{}])[0].get("message") or {}
                    content = message.get("content") or ""
                    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
                    return content, data.get("usage") or {}, reasoning
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if resp.status_code not in RETRYABLE_STATUSES:
                    break
        if attempt < MAX_RETRIES:
            time.sleep(min(30.0, (2**attempt) + random.random()))
    raise RuntimeError(f"OpenRouter request failed: {last_error}")


def extract_json(text: str) -> Optional[Any]:
    """Extract the first JSON object/array embedded in ``text`` (handles
    markdown fences and surrounding prose)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    return None
