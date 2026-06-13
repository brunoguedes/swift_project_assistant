# Swift Project Assistant

Swift Project Assistant is a tool designed to help developers analyze, summarize, and interact with Swift projects. It ships two things:

1. **An MCP server** that gives AI agents (Claude Code, Claude Desktop, or any MCP client) structural understanding of a Swift codebase — outlines, symbol search, targeted source extraction — so they can answer questions and make changes **without reading whole files**, saving a large amount of tokens.
2. **A Streamlit app** that generates documentation for Swift files with the LLM of your choice and answers questions about the project using RAG (Retrieval-Augmented Generation).

## MCP Server

The MCP server is powered by [SourceKitten](https://github.com/jpsim/SourceKitten) — the Swift compiler's own tooling — so outlines, type names, and method signatures (including parameter labels like `fetchMovies(for:)`) are compiler-accurate. It's designed to run on your development machine:

```bash
brew install sourcekitten
```

### Tools

| Tool | What it does |
|---|---|
| `list_swift_files` | Project layout: every Swift file with line counts |
| `get_project_map` | Every type declared in the project (kind, name, conformances), per file |
| `get_file_outline` | One file's structure: imports, types, property/method signatures, enum cases — no bodies (~10x fewer tokens than the source) |
| `get_public_interface` | Like `get_file_outline`, but filtered by access level — shows a file's intent and contract with the internals hidden (drops `private`/`fileprivate` by default; `min_access="public"` for the strict library API) |
| `find_symbol` | Locate where a type, method, property, or function is declared |
| `get_symbol_source` | Extract the source of a single type or method (e.g. `MovieViewModel.fetchMovies`) from a known file |
| `get_implementation` | Full source of a declaration by name, searched across the whole project — when you know the name but not the file |
| `get_file_summary` | Markdown summary of a file, cached so it's returned instantly (no SourceKitten run) while the file is unmodified |
| `get_file_dependencies` | A file's imports, declared types, and external conformances |

### How a summary is generated

`get_file_summary` builds its markdown in **two stages**. Only the second is an LLM, and it's optional — the structural part never involves a model.

**Stage 1 — structural summary (deterministic, always runs).**

1. Run `sourcekitten structure --file <path>`, which returns JSON describing the file's syntax tree (types, members, offsets, kinds).
2. Parse that JSON into a structured analysis — imports, types with their conformances, nested types, property and method signatures, enum cases, and top-level functions.
3. Render it to markdown (`# File.swift`, `**Imports:** …`, `## struct Foo: Identifiable`, `` - `member` `` lines). This is pure string formatting of SourceKitten's output — no model is involved, so the imports, type list, and signatures are exact, not inferred.

**Stage 2 — prose `## Overview` (LLM, only when `SUMMARY_LLM` is set).**

When a backend is configured, one LLM call adds a short prose paragraph that is inserted between the title and the structural sections. The model is given the Stage-1 outline plus the raw source (truncated to 12,000 characters) and asked only to describe the file — it never determines the structure. The exact prompt is:

```text
You are documenting a Swift source file for developers.

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
```

There is no system prompt — the whole thing is sent as a single user message. For the `claude-cli` backend this is piped to `claude -p --model <model>` over stdin; for `ollama` it's POSTed to the local `/api/generate` endpoint. The model's reply becomes the Overview text. If the call fails for any reason (CLI missing, timeout, backend unreachable), the error is caught and the Overview is skipped — you still get the full structural summary. See [LLM prose overviews](#llm-prose-overviews-optional) below to configure it.

Both stages run at most once per file edit: the result is cached (see below), so the model is not re-invoked on cache hits — only when the file changes or you pass `refresh=true`.

### Summary cache storage

`get_file_summary` caches its result so an unmodified file is summarized only once. Where the cache lives is controlled by the `SUMMARY_STORAGE` environment variable (set it in the MCP server's environment or a `.env` next to the project):

| Value | Behaviour |
|---|---|
| `same-file` / unset (default) | Store the summary as a comment block at the top of the `.swift` file |
| `standalone` | Store the summary in a sibling `<name>.md` file (e.g. `MovieViewModel.swift` → `MovieViewModel.md`); the `.swift` file is never modified |
| `off` | Never write anything; regenerate the summary on every call |

In **same-file** mode `get_file_summary` writes its result to the top of the Swift file:

```swift
/* swift-project-assistant:summary
Generated: 2026-06-12T22:29:27.358654+00:00

# MovieViewModel.swift

**Imports:** Foundation, SwiftUI

## class MovieViewModel: ObservableObject

- `func fetchMovies(for category: Category) -> [Movie]`
*/
import Foundation
...
```

If the `Generated` timestamp is equal to or later than the file's modification time, the cached summary is returned without re-running SourceKitten (the file's mtime is pinned to the generation time, so writing the cache doesn't invalidate it). Editing the file makes the cache stale, and the next call regenerates and rewrites the block. Pass `refresh=true` to force regeneration.

In **standalone** mode the summary is written to the sibling `.md` file instead, prefixed with an HTML-comment provenance line (invisible when rendered). That cache is current while the `.md` file's mtime is at or after the `.swift` file's; editing the source makes the source newer and triggers regeneration on the next call.

### LLM prose overviews (optional)

Set `SUMMARY_LLM` (in the MCP server's environment or a `.env` next to the project) to add an LLM-written `## Overview` section to regenerated summaries:

| Value | Backend | Cost |
|---|---|---|
| `ollama` or `ollama:codestral` | Local [Ollama](https://ollama.com) (default model `qwen2.5-coder`) | Free, fully local |
| `claude-cli` or `claude-cli:sonnet` | [Claude Code](https://claude.com/claude-code) headless mode (`claude -p`, default model `haiku`) | Your Claude Pro/Max **subscription** — no API key or API billing |
| `none` / unset | — | Structural summaries only |

Because overviews are cached in the file, the LLM runs once per file edit — not per question. If the backend is unreachable, summaries gracefully fall back to structural-only. After enabling `SUMMARY_LLM`, call `get_file_summary` with `refresh=true` to enrich already-cached files.

### Install & run

The MCP server's runtime dependencies are tiny (`mcp`, `python-dotenv`, `httpx` — SourceKitten and the optional `claude` CLI are external binaries it shells out to), so installing it for use by an agent is cheap. Pick whichever install style you prefer; all three expose the same `swift-project-mcp` stdio command.

**A. uv / pipx tool install (recommended).** Puts a clean, stable launcher on your PATH (`~/.local/bin/swift-project-mcp`) in an isolated environment — no hashed virtualenv path, trivial to upgrade. Add `--editable` (uv) / `-e` (pipx) to have it track source edits without reinstalling.

```bash
uv tool install --editable .       # or: pipx install -e .
```

**B. Standalone binary.** A single self-contained executable (no Python needed at runtime). Build it, then copy it wherever you like:

```bash
poetry install --with dev          # provides PyInstaller
poetry run bash packaging/build_binary.sh
cp dist/swift-project-mcp ~/.local/bin/   # ~34 MB, rebuild after code changes
```

**C. Run in place via Poetry.** No install step, but the command must include the project directory.

```bash
poetry install
```

Then add it to **Claude Code** — point `--command` / `command` at whichever you chose:

```bash
# A or B: a launcher already on PATH
claude mcp add swift-project-assistant -- ~/.local/bin/swift-project-mcp
# C: run in place
claude mcp add swift-project-assistant -- poetry run --directory /path/to/swift_project_assistant swift-project-mcp
```

Or to **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "swift-project-assistant": {
      "command": "/Users/you/.local/bin/swift-project-mcp",
      "env": { "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" }
    }
  }
}
```

> The `env.PATH` matters: the server shells out to `sourcekitten` (and `claude`, when `SUMMARY_LLM=claude-cli`), so their directories must be on the PATH the MCP client launches it with — regardless of which install style you use.

Then ask your agent things like *"What view models are in ~/Projects/BoxOfficeBuzz and what do they depend on?"* — it will use the project map and outlines instead of reading every file.

## Streamlit App

- **File Analysis**: Automatically analyzes Swift files in a specified project directory (uses [SourceKitten](https://github.com/jpsim/SourceKitten) — macOS).
- **Code Summarization**: Generates comprehensive summaries of Swift code files, including classes, structs, methods, and dependencies.[^1]
  ![](./screenshots/GenerateDocumentationForFile.png)
- **Interactive Q&A**: Uses a RAG system to answer questions about the project based on generated documentation.
  ![](./screenshots/AskQuestionsAboutTheCode.png)
- **Flexible LLM Integration**: Supports current local and remote models — Claude Opus 4.8 / Sonnet 4.6 / Haiku 4.5, GPT-5 / GPT-4.1, Groq Llama 3.3, and local models via Ollama (Llama 3.3, Qwen 2.5 Coder, Codestral, Mistral).
  ![](./screenshots/SelectLLM.png)
- **Folder Exclusion**: Option to exclude specific folders from analysis.
- **Project Structure Visualization**: Displays the folder structure of the analyzed project.
  ![](./screenshots/FolderStructure.png)

[^1]: Screenshots generated using the [BoxOfficeBuzz](https://github.com/brunoguedes/BoxOfficeBuzz) project.

### Usage

1. Install the app's extra dependencies (Streamlit + LangChain stack — kept out of the base install so the MCP server stays lean):

```bash
poetry install --extras app
```

2. Copy `.env_example` to `.env` and fill in the API keys you plan to use.
3. Run the Streamlit app:

```bash
poetry run streamlit run src/app.py
```

3. In the web interface:
   - Select the LLM model you want to use.
   - Enter the file types you want to analyze.
   - Specify folders to exclude (if any).
   - Enter the base path of your Swift project.
   - Use the interface to generate summaries and interact with the Q&A system.

## Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org) for dependency management
- [SourceKitten](https://github.com/jpsim/SourceKitten) (`brew install sourcekitten`) — used by both the MCP server and the Streamlit app's file analysis
- Optional: local HuggingFace embeddings via `poetry install --extras huggingface`

## Project Structure

- `src/swift_project_assistant/` — installable package
  - `analyzer.py` — SourceKitten-backed structure analysis
  - `mcp_server.py` — the MCP server (`swift-project-mcp` entry point)
- `src/app.py` — Streamlit application
- `src/llm_runner.py` — LLM interactions for code summarization
- `src/swift_dependency_analysis.py` — SourceKitten-based analysis used by the Streamlit app
- `tests/` — parser test suite (`poetry run pytest`)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).
