from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request

from loom.config import LLMConfig
from loom.plugins import is_plugin_spec, load_plugin


class LLMTransportError(RuntimeError):
    """The LLM transport failed before returning a usable model reply.

    Raised for retryable conditions (network failures, timeouts, rate
    limits, 5xx). The namer defers clusters that hit this and retries them
    on a later run; other exceptions mark the cluster naming_failed.
    """


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"model reply did not contain a JSON object: {text[:400]!r}")
    return json.loads(text[start:end])


def _api_key(cfg: LLMConfig, default_env: str) -> str | None:
    return os.environ.get(cfg.api_key_env or default_env)


def _call_anthropic(cfg: LLMConfig, prompt: str) -> str:
    import anthropic

    client_kwargs: dict[str, Any] = {"timeout": float(cfg.timeout)}
    api_key = _api_key(cfg, "ANTHROPIC_API_KEY")
    if cfg.api_key_env and api_key:
        client_kwargs["api_key"] = api_key
    if cfg.base_url:
        client_kwargs["base_url"] = cfg.base_url
    client = anthropic.Anthropic(**client_kwargs)
    try:
        response = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
        raise LLMTransportError(f"anthropic transport failure: {exc}") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise LLMTransportError(f"anthropic server error {exc.status_code}") from exc
        raise
    if response.stop_reason == "refusal":
        raise ValueError("model refused the naming request")
    return "".join(block.text for block in response.content if block.type == "text")


def _call_openai_compatible(cfg: LLMConfig, prompt: str) -> str:
    base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
    body = json.dumps(
        {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = _api_key(cfg, "OPENAI_API_KEY")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise LLMTransportError(f"openai-compatible server error {exc.code}") from exc
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"openai-compatible request rejected ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMTransportError(f"openai-compatible transport failure: {exc}") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected chat completions payload: {payload!r}") from exc
    if not isinstance(content, str):
        raise ValueError(f"unexpected content type in reply: {type(content).__name__}")
    return content


def call_json(cfg: LLMConfig, *, session_key: str, prompt: str) -> dict[str, Any]:
    """Run one prompt and parse the single JSON object from the reply.

    session_key labels the call for logs/debugging; it is not sent to the
    provider.
    """
    if is_plugin_spec(cfg.provider):
        # e.g. provider = "plugin:my_llm:call_json" — the target is called with
        # the same signature as this function and owns the full exchange.
        return load_plugin(cfg.provider)(cfg, session_key=session_key, prompt=prompt)
    del session_key
    if cfg.provider == "anthropic":
        text = _call_anthropic(cfg, prompt)
    elif cfg.provider == "openai":
        text = _call_openai_compatible(cfg, prompt)
    else:
        raise LLMTransportError(f"unknown llm provider: {cfg.provider!r}")
    return _extract_json(text)
