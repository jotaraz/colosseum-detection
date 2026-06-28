from __future__ import annotations

"""OpenRouter client for social-jira2 (repo-local, OpenAI chat-completions compatible).

The installed ``llm_server`` package ships clients for openai/anthropic/gemini/together/
fireworks/vllm but not OpenRouter, and it lives in site-packages (not synced to the cluster),
so we add the provider here instead of patching the package. OpenRouter exposes an
OpenAI-compatible ``/chat/completions`` endpoint, so this is a thin generic chat client
modelled on :class:`llm_server.clients.fireworks_client.FireworksClient` with two extras the
gpt-oss runs rely on:

  * ``reasoning_effort`` is forwarded as OpenRouter's unified ``reasoning: {effort: ...}`` knob,
    so the personality sweep can keep effort as its only variable on OpenRouter too.
  * the per-message chain-of-thought is captured into ``self._reasoning_steps`` so the runner's
    existing ``_drain_reasoning`` / ``_take_reasoning`` helpers save CoT exactly as they do for
    the local vLLM gpt-oss client.
"""

import json
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from llm_server.clients.abstract_client import AbstractClient

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _convert_tools(tools: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """Convert Terrarium tool schema into OpenAI-compatible tool definitions."""
    if not tools:
        return []
    normalized: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        func = tool.get("function") or {}
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                },
            }
        )
    return normalized


class OpenRouterClient(AbstractClient):
    """Client that talks to OpenRouter's OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        request_timeout: int = 120,
        connect_timeout: int = 30,
        total_timeout: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        load_dotenv(override=True)
        resolved_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OpenRouter API key not found. Set OPENROUTER_API_KEY in the .env file "
                "(or pass openrouter.api_key in the config)."
            )

        self.base_url = str(base_url).rstrip("/")
        self.api_key = resolved_key
        self.request_timeout = int(request_timeout)
        self.connect_timeout = int(connect_timeout)
        # Hard wall-clock cap per attempt. requests' own ``timeout`` is per-read, which a
        # stalled upstream + OpenRouter keep-alive bytes can defeat (a single call then wedges
        # the whole run forever). This cap guarantees we abandon and retry such a call. Default
        # is generous enough for legitimate long reasoning generations.
        self.total_timeout = int(total_timeout) if total_timeout else max(self.request_timeout, 600)
        self.extra_headers = dict(extra_headers or {})
        self.session = requests.Session()
        # Per-turn chain-of-thought, drained by the runner (see module docstring).
        self._reasoning_steps: List[Dict[str, Any]] = []

    def _post(self, payload: Dict[str, Any]) -> requests.Response:
        """POST the chat request with a hard wall-clock cap (``self.total_timeout``).

        Runs the (blocking) request in a daemon thread and joins with a deadline; if it hasn't
        returned in time the call is wedged, so we drop the session's pooled connections (to
        unblock the abandoned socket), start a fresh session, and raise a retryable Timeout.
        The per-read ``timeout`` is still passed for the normal fast-fail path.
        """
        holder: Dict[str, Any] = {}

        def _run() -> None:
            try:
                holder["resp"] = self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._build_headers(),
                    data=json.dumps(payload),
                    timeout=(self.connect_timeout, self.request_timeout),
                )
            except Exception as exc:  # noqa: BLE001 - propagated to the caller below
                holder["exc"] = exc

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(self.total_timeout)
        if th.is_alive():
            try:
                self.session.close()
            finally:
                self.session = requests.Session()
            raise requests.exceptions.Timeout(
                f"total timeout {self.total_timeout}s exceeded (call abandoned)"
            )
        if "exc" in holder:
            raise holder["exc"]
        return holder["resp"]

    # --------------------------------------------------------------- retries
    @staticmethod
    def _get_retry_config() -> Tuple[int, float, float]:
        max_retries = int(os.getenv("OPENROUTER_MAX_RETRIES", "6"))
        base_sleep_s = float(os.getenv("OPENROUTER_RETRY_BASE_SLEEP_S", "1.0"))
        max_sleep_s = float(os.getenv("OPENROUTER_RETRY_MAX_SLEEP_S", "30.0"))
        return max_retries, base_sleep_s, max_sleep_s

    @staticmethod
    def _get_retry_after_seconds(response: requests.Response) -> Optional[float]:
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return float(retry_after)
        except ValueError:
            return None

    # ------------------------------------------------------------ interface
    @staticmethod
    def init_context(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _extract_message_content(message: Dict[str, Any]) -> str:
        if isinstance(message, dict) and "choices" in message:
            choices = message.get("choices") or []
            message = choices[0].get("message") if choices else {}
        content = (message or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "".join(parts)
        return ""

    @staticmethod
    def get_usage(response: Dict[str, Any], current_usage: Dict[str, int]) -> Dict[str, int]:
        usage = (response or {}).get("usage") or {}
        current_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        current_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        if "total_tokens" in usage:
            current_usage["total_tokens"] += usage.get("total_tokens", 0)
        else:
            current_usage["total_tokens"] = (
                current_usage["prompt_tokens"] + current_usage["completion_tokens"]
            )
        return current_usage

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _capture_reasoning(self, message: Dict[str, Any]) -> None:
        """Record the message's chain-of-thought (OpenRouter -> ``reasoning``)."""
        try:
            self._reasoning_steps.append({
                "reasoning_content": message.get("reasoning")
                or message.get("reasoning_content"),
                "content": message.get("content"),
            })
        except Exception:
            pass

    def generate_response(
        self,
        input: List[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        payload: Dict[str, Any] = {
            "model": params.get("model"),
            "messages": input,
        }

        max_tokens = params.get("max_completion_tokens") or params.get("max_output_tokens")
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if params.get("temperature") is not None:
            payload["temperature"] = params["temperature"]
        # OpenRouter's unified reasoning knob (honoured by gpt-oss et al.).
        effort = params.get("reasoning_effort")
        if effort is not None:
            payload["reasoning"] = {"effort": effort}

        converted_tools = _convert_tools(params.get("tools"))
        if converted_tools:
            payload["tools"] = converted_tools
        if params.get("tool_choice") is not None:
            payload["tool_choice"] = params["tool_choice"]

        max_retries, base_sleep_s, max_sleep_s = self._get_retry_config()
        retryable_statuses = {429, 500, 502, 503, 504}
        last_error: Optional[str] = None
        response: Optional[requests.Response] = None

        for attempt in range(max_retries + 1):
            try:
                response = self._post(payload)
            except requests.exceptions.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                response = None
            else:
                if response.ok:
                    last_error = None
                    break
                last_error = f"HTTP {response.status_code}: {response.text}"

            status_code = response.status_code if response is not None else None
            can_retry = (status_code in retryable_statuses) or (response is None)
            if attempt >= max_retries or not can_retry:
                break

            retry_after_s = (
                self._get_retry_after_seconds(response) if response is not None else None
            )
            if retry_after_s is not None and retry_after_s > 0:
                sleep_s = min(retry_after_s, max_sleep_s)
            else:
                exp_sleep = base_sleep_s * (2**attempt)
                jitter = random.uniform(0.0, min(1.0, exp_sleep * 0.25))
                sleep_s = min(max_sleep_s, exp_sleep + jitter)
            time.sleep(sleep_s)

        if last_error is not None:
            raise RuntimeError(
                f"OpenRouter chat request failed after {max_retries + 1} attempts: {last_error}"
            )

        data = response.json()  # type: ignore[union-attr]
        choices = data.get("choices") or []
        first_message = choices[0]["message"] if choices else {"content": ""}
        self._capture_reasoning(first_message if isinstance(first_message, dict) else {})
        response_str = self._extract_message_content(first_message)
        return data, response_str

    async def process_tool_calls(
        self,
        response: Dict[str, Any],
        context: List[Dict[str, Any]],
        execute_tool_callback: Any,
    ) -> Tuple[int, List[Dict[str, Any]], List[str]]:
        choices = response.get("choices") or []
        if not choices:
            return 0, context, []

        message = choices[0].get("message") or {}
        context.append(message)
        tool_calls = message.get("tool_calls") or []
        tool_calls_executed = 0
        step_tools: List[str] = []

        for call in tool_calls:
            function_block = call.get("function") or {}
            tool_name = function_block.get("name", "unknown_tool")
            arguments_raw = function_block.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_raw)
            except json.JSONDecodeError:
                arguments = {}
            step_tools.append(f"{tool_name} -- {json.dumps(arguments)}")
            result = await execute_tool_callback(tool_name, arguments)
            context.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": tool_name,
                    "content": json.dumps(result),
                }
            )
            tool_calls_executed += 1

        return tool_calls_executed, context, step_tools
