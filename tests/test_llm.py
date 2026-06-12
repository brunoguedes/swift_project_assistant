import subprocess

import pytest

from swift_project_assistant import llm


def test_backend_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("SUMMARY_LLM", raising=False)
    assert llm.configured_backend() is None
    assert llm.generate_overview("# outline", "source") is None


def test_backend_disabled_when_none(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "none")
    assert llm.configured_backend() is None


def test_backend_parsing(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "ollama")
    assert llm.configured_backend() == ("ollama", llm.DEFAULT_OLLAMA_MODEL)
    monkeypatch.setenv("SUMMARY_LLM", "ollama:codestral")
    assert llm.configured_backend() == ("ollama", "codestral")
    monkeypatch.setenv("SUMMARY_LLM", "claude-cli")
    assert llm.configured_backend() == ("claude-cli", llm.DEFAULT_CLAUDE_MODEL)
    monkeypatch.setenv("SUMMARY_LLM", "claude-cli:sonnet")
    assert llm.configured_backend() == ("claude-cli", "sonnet")
    monkeypatch.setenv("SUMMARY_LLM", "Claude:opus")
    assert llm.configured_backend() == ("claude-cli", "opus")


def test_backend_parsing_unknown(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "gemini")
    with pytest.raises(ValueError, match="Unknown SUMMARY_LLM backend"):
        llm.configured_backend()


def test_ollama_backend(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "ollama:codestral")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": " A view model file. "}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    overview = llm.generate_overview("# Outline", "struct A {}")
    assert overview == "A view model file."
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["json"]["model"] == "codestral"
    assert "struct A {}" in captured["json"]["prompt"]
    assert captured["json"]["stream"] is False


def test_ollama_host_without_scheme(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    assert llm._ollama_host() == "http://0.0.0.0:11434"


def test_claude_cli_backend(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "claude-cli:haiku")
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        return subprocess.CompletedProcess(cmd, 0, stdout="Handles movie fetching.\n", stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    overview = llm.generate_overview("# Outline", "class B {}")
    assert overview == "Handles movie fetching."
    assert captured["cmd"] == ["claude", "-p", "--model", "haiku"]
    assert "class B {}" in captured["input"]


def test_claude_cli_failure_raises(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "claude-cli")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="not logged in"):
        llm.generate_overview("# Outline", "code")


def test_claude_cli_missing_binary(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "claude-cli")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Claude Code CLI"):
        llm.generate_overview("# Outline", "code")


def test_source_truncated(monkeypatch):
    monkeypatch.setenv("SUMMARY_LLM", "ollama")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "ok"}

    monkeypatch.setattr(
        llm.httpx, "post", lambda url, json, timeout: captured.update(json=json) or FakeResponse()
    )
    llm.generate_overview("# Outline", "x" * 50_000)
    assert len(captured["json"]["prompt"]) < 20_000
