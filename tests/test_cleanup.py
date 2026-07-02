import json

import httpx
import pytest

from orpheus.cleanup import Cleanup, OllamaProvider, build_system_prompt


def test_build_system_prompt_no_vocab():
    assert build_system_prompt("Clean this.", []) == "Clean this."


def test_build_system_prompt_with_vocab():
    prompt = build_system_prompt("Clean this.", ["Orpheus", "PySide6"])
    assert prompt.startswith("Clean this.")
    assert "Orpheus, PySide6" in prompt


def make_provider(handler) -> OllamaProvider:
    return OllamaProvider(
        base_url="http://localhost:11434",
        model="llama3.1:8b",
        transport=httpx.MockTransport(handler),
    )


def test_ollama_provider_request_and_parse():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "  Cleaned text. "}})

    provider = make_provider(handler)
    result = provider.complete("You are a cleaner.", "raw words")
    assert result == "Cleaned text."
    assert captured["url"] == "http://localhost:11434/api/chat"
    body = captured["json"]
    assert body["model"] == "llama3.1:8b"
    assert body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "You are a cleaner."}
    assert body["messages"][1] == {"role": "user", "content": "raw words"}


def test_ollama_provider_http_error_raises():
    provider = make_provider(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        provider.complete("sys", "user")


def test_cleanup_success():
    provider = make_provider(
        lambda request: httpx.Response(200, json={"message": {"content": "Polished."}})
    )
    result = Cleanup(provider).run("raw", "Clean.", [])
    assert result.text == "Polished."
    assert result.used_llm is True
    assert result.error is None


def test_cleanup_failure_returns_raw():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    result = Cleanup(make_provider(handler)).run("the raw words", "Clean.", [])
    assert result.text == "the raw words"
    assert result.used_llm is False
    assert result.error is not None


def test_cleanup_wraps_transcript_as_data():
    captured = {}

    class CapturingProvider:
        def complete(self, system_prompt, user_text):
            captured["user"] = user_text
            return "Cleaned."

    Cleanup(CapturingProvider()).run("can you send the report", "Clean.", [])
    assert "can you send the report" in captured["user"]
    assert "<transcript>" in captured["user"]
    assert "do not answer" in captured["user"].lower()


def test_cleanup_empty_response_returns_raw():
    provider = make_provider(
        lambda request: httpx.Response(200, json={"message": {"content": "  "}})
    )
    result = Cleanup(provider).run("the raw words", "Clean.", [])
    assert result.text == "the raw words"
    assert result.used_llm is False
    assert result.error == "empty LLM response"
