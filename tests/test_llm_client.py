from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import anthropic
import pytest

from loom.config import LLMConfig
from loom.llm.client import LLMTransportError, call_json


def _openai_cfg(**overrides) -> LLMConfig:
    defaults = dict(provider="openai", model="test-model", timeout=5)
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _chat_completion(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_openai_path_extracts_json_from_noisy_reply(monkeypatch):
    reply = 'Sure! Here you go:\n{"title": "Drip irrigation", "coherent": true}'
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(_chat_completion(reply))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = call_json(_openai_cfg(base_url="http://localhost:11434/v1"),
                       session_key="test", prompt="name it")
    assert result == {"title": "Drip irrigation", "coherent": True}
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["body"]["model"] == "test-model"


def test_openai_server_errors_are_transport_errors(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(LLMTransportError):
        call_json(_openai_cfg(), session_key="test", prompt="p")


def test_openai_client_errors_are_not_deferrable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b"nope"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        call_json(_openai_cfg(), session_key="test", prompt="p")
    assert not isinstance(excinfo.value, LLMTransportError)


def test_openai_network_failure_is_transport_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(LLMTransportError):
        call_json(_openai_cfg(), session_key="test", prompt="p")


def test_reply_without_json_object_raises_value_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(_chat_completion("no json here"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ValueError):
        call_json(_openai_cfg(), session_key="test", prompt="p")


def test_unknown_provider_is_transport_error():
    with pytest.raises(LLMTransportError):
        call_json(LLMConfig(provider="mystery"), session_key="test", prompt="p")


class _FakeBlock:
    def __init__(self, type_: str, text: str):
        self.type = type_
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [_FakeBlock("text", text)]
        self.stop_reason = stop_reason


def _fake_anthropic_client(monkeypatch, message: _FakeMessage):
    class FakeMessages:
        def create(self, **kwargs):
            return message

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_anthropic_path_parses_text_blocks(monkeypatch):
    _fake_anthropic_client(monkeypatch, _FakeMessage('{"title": "Watering cadence"}'))
    result = call_json(LLMConfig(provider="anthropic"), session_key="test", prompt="p")
    assert result == {"title": "Watering cadence"}


def test_anthropic_refusal_is_not_deferrable(monkeypatch):
    _fake_anthropic_client(monkeypatch, _FakeMessage("", stop_reason="refusal"))
    with pytest.raises(ValueError):
        call_json(LLMConfig(provider="anthropic"), session_key="test", prompt="p")
