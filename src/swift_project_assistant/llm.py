"""Optional LLM backends for prose file overviews.

Configured with the SUMMARY_LLM environment variable:

    SUMMARY_LLM=ollama                # local Ollama, default model
    SUMMARY_LLM=ollama:codestral      # local Ollama, specific model
    SUMMARY_LLM=claude-cli            # Claude Code headless mode (`claude -p`),
                                      # billed to your Claude subscription
    SUMMARY_LLM=claude-cli:sonnet     # specific Claude model alias
    SUMMARY_LLM=none                  # (or unset) structural summaries only

The Ollama backend talks to the local Ollama HTTP API (OLLAMA_HOST,
default http://localhost:11434). The claude-cli backend shells out to the
`claude` binary with `-p`, which authenticates via your Claude Pro/Max
subscription login rather than an API key.
"""

from __future__ import annotations

import os
import subprocess

import httpx

DEFAULT_OLLAMA_MODEL = "qwen2.5-coder"
DEFAULT_CLAUDE_MODEL = "haiku"

_MAX_SOURCE_CHARS = 12_000

_PROMPT_TEMPLATE = """You are documenting a Swift source file for developers.

Below are the file's structural outline and its source code. Write a short
overview (2-4 sentences, plain prose, no headings, no bullet points, no
preamble) explaining what this file is responsible for, how its main types
are meant to be used, and anything non-obvious about how it works.

<outline>
{outline}
</outline>

<source>
{source}
</source>
"""


def configured_backend() -> tuple[str, str] | None:
    """Parse SUMMARY_LLM into (backend, model), or None when disabled."""
    raw = os.getenv("SUMMARY_LLM", "").strip()
    if not raw or raw.lower() == "none":
        return None
    name, _, model = raw.partition(":")
    name = name.lower().strip()
    model = model.strip()
    if name == "ollama":
        return ("ollama", model or DEFAULT_OLLAMA_MODEL)
    if name in ("claude-cli", "claude"):
        return ("claude-cli", model or DEFAULT_CLAUDE_MODEL)
    raise ValueError(
        f"Unknown SUMMARY_LLM backend {raw!r}. "
        "Use 'ollama[:model]', 'claude-cli[:model]', or 'none'."
    )


def _ollama_host() -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


def _generate_ollama(model: str, prompt: str) -> str:
    response = httpx.post(
        f"{_ollama_host()}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=180.0,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def _generate_claude_cli(model: str, prompt: str) -> str:
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "SUMMARY_LLM=claude-cli requires the Claude Code CLI (`claude`) on PATH."
        ) from None
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def generate_overview(outline_markdown: str, source: str) -> str | None:
    """Prose overview of a file via the configured backend, or None if disabled.

    Raises on backend failure — callers decide whether that is fatal.
    """
    backend = configured_backend()
    if backend is None:
        return None
    name, model = backend
    prompt = _PROMPT_TEMPLATE.format(
        outline=outline_markdown.strip(),
        source=source[:_MAX_SOURCE_CHARS],
    )
    if name == "ollama":
        text = _generate_ollama(model, prompt)
    else:
        text = _generate_claude_cli(model, prompt)
    return text or None
