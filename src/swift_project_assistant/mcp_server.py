"""MCP server exposing token-frugal Swift code intelligence tools.

AI agents use these tools to understand a Swift codebase structurally —
project layout, type outlines, symbol locations — and to pull in only the
specific source they need, instead of reading whole files into context.

Analysis is powered by SourceKitten (the Swift compiler's tooling library),
so it must run on a machine with SourceKitten installed:
`brew install sourcekitten`.

Run with:  swift-project-mcp  (stdio transport)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from swift_project_assistant.analyzer import (
    FileAnalysis,
    analyze_file,
    analyze_structure,
    find_symbol_source,
    outline_to_dict,
    referenced_types,
    run_sourcekitten,
)
from swift_project_assistant.summary import get_summary

DEFAULT_EXCLUDES = {".git", ".build", "Pods", "Carthage", "DerivedData", ".swiftpm"}

mcp = FastMCP("swift-project-assistant")


def _swift_files(project_path: str, exclude_folders: list[str] | None = None) -> list[Path]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {project_path}")
    excludes = DEFAULT_EXCLUDES | set(exclude_folders or [])
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]
        files.extend(Path(dirpath) / f for f in filenames if f.endswith(".swift"))
    return sorted(files)


def _resolve_file(file_path: str) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    return path


def _analyze(file_path: str) -> FileAnalysis:
    return analyze_file(str(_resolve_file(file_path)))


@mcp.tool()
def list_swift_files(project_path: str, exclude_folders: list[str] | None = None) -> str:
    """List every Swift file in a project, with line counts.

    Call this first when starting to work with an unfamiliar Swift project to
    learn its layout. Returns relative paths so follow-up calls can target
    specific files.
    """
    root = Path(project_path).expanduser().resolve()
    files = _swift_files(project_path, exclude_folders)
    entries = []
    for f in files:
        try:
            lines = f.read_bytes().count(b"\n") + 1
        except OSError:
            lines = 0
        entries.append({"path": str(f.relative_to(root)), "lines": lines})
    return json.dumps({"root": str(root), "file_count": len(entries), "files": entries}, indent=1)


@mcp.tool()
def get_project_map(project_path: str, exclude_folders: list[str] | None = None) -> str:
    """Get a compact map of every type declared in a Swift project.

    Call this to understand what a project contains and where, without reading
    any source. Returns, per file, the declared types (classes, structs, enums,
    protocols, actors, extensions) and what they inherit/conform to. Use
    get_file_outline or get_symbol_source afterwards to drill into specifics.
    """
    root = Path(project_path).expanduser().resolve()
    project: dict[str, dict] = {}
    for f in _swift_files(project_path, exclude_folders):
        try:
            analysis = analyze_file(str(f))
        except (OSError, RuntimeError) as exc:
            project[str(f.relative_to(root))] = {"error": str(exc)}
            continue
        decls = []

        def collect(types, prefix=""):
            for t in types:
                entry = {"kind": t.kind, "name": prefix + t.name}
                if t.inherits:
                    entry["inherits"] = t.inherits
                decls.append(entry)
                collect(t.nested, prefix + t.name + ".")

        collect(analysis.types)
        if decls or analysis.functions:
            entry: dict = {"types": decls}
            if analysis.functions:
                entry["functions"] = [m.name for m in analysis.functions]
            project[str(f.relative_to(root))] = entry
    return json.dumps(project, indent=1)


@mcp.tool()
def get_file_outline(file_path: str) -> str:
    """Get the structure of one Swift file as JSON with line numbers.

    Prefer get_file_summary when you just need to understand what a file
    contains; call this tool when you additionally need line numbers or
    machine-readable JSON (e.g. to target a follow-up get_symbol_source
    call). Returns imports, types, conformances, property and method
    signatures, enum cases, and nested types — no implementation bodies.
    Roughly 10x fewer tokens than the raw source.
    """
    return json.dumps(outline_to_dict(_analyze(file_path)), indent=1)


@mcp.tool()
def find_symbol(project_path: str, symbol: str, exclude_folders: list[str] | None = None) -> str:
    """Find where a type, function, property, or method is declared in a project.

    Call this when you know a symbol's name (e.g. "MovieViewModel" or
    "fetchMovies") but not which file defines it. Returns matching
    declarations with file path and line number.
    """
    root = Path(project_path).expanduser().resolve()
    matches = []
    for f in _swift_files(project_path, exclude_folders):
        try:
            analysis = analyze_file(str(f))
        except (OSError, RuntimeError):
            continue
        rel = str(f.relative_to(root))

        def walk(types, prefix=""):
            for t in types:
                if t.name == symbol:
                    matches.append(
                        {"file": rel, "line": analysis.line_of(t.offset),
                         "kind": t.kind, "name": prefix + t.name}
                    )
                for m in t.members:
                    if m.name == symbol:
                        matches.append(
                            {"file": rel, "kind": m.kind,
                             "name": f"{prefix}{t.name}.{m.name}", "declaration": m.declaration}
                        )
                walk(t.nested, prefix + t.name + ".")

        walk(analysis.types)
        for m in analysis.functions + analysis.globals:
            if m.name == symbol:
                matches.append({"file": rel, "kind": m.kind, "name": m.name, "declaration": m.declaration})
    return json.dumps({"symbol": symbol, "matches": matches}, indent=1)


@mcp.tool()
def get_symbol_source(file_path: str, symbol: str) -> str:
    """Get the full source code of a single declaration from a Swift file.

    Call this when you need the actual implementation of one type or method
    rather than the whole file. `symbol` accepts a type name ("MovieViewModel"),
    a qualified member ("MovieViewModel.fetchMovies"), or a top-level function
    name. Combine with find_symbol to locate the file first.
    """
    result = find_symbol_source(_analyze(file_path), symbol)
    if result is None:
        return f"Symbol '{symbol}' not found in {file_path}. Use get_file_outline to see available symbols."
    return result


@mcp.tool()
def get_file_summary(file_path: str, refresh: bool = False) -> str:
    """Get a markdown summary of a Swift file: imports, types, member signatures.

    This is the primary tool for understanding a Swift file — call it whenever
    you need to know what a file contains, before reaching for get_file_outline
    or reading the source. Summaries are cached: if the cache is at or newer
    than the file's last modification, it is returned instantly without
    re-running SourceKitten. Otherwise the summary is regenerated. Set
    refresh=true to force regeneration (e.g. after making significant edits).

    Where the cache lives is set by the SUMMARY_STORAGE environment variable:
      - same-file (default): a comment block at the top of the .swift file;
        regenerating rewrites the block in place, leaving the code untouched.
      - standalone: a sibling <name>.md file (e.g. Foo.swift -> Foo.md); the
        .swift file is never modified.
      - off: nothing is written; the summary is regenerated on every call.

    If the server is configured with SUMMARY_LLM (ollama[:model] or
    claude-cli[:model]), regenerated summaries also include an LLM-written
    prose Overview section.
    """
    return get_summary(_resolve_file(file_path), refresh=refresh)


@mcp.tool()
def get_file_dependencies(file_path: str) -> str:
    """Get the imports of a Swift file plus the external type names it references.

    Call this to understand what a file depends on before changing it —
    which modules it imports and which types declared elsewhere it uses.
    """
    path = _resolve_file(file_path)
    structure = run_sourcekitten(str(path))
    analysis = analyze_structure(path.read_bytes(), structure)
    declared: set[str] = set()

    def collect(types):
        for t in types:
            declared.add(t.name)
            collect(t.nested)

    collect(analysis.types)
    return json.dumps(
        {
            "imports": analysis.imports,
            "declares": sorted(declared),
            "references": referenced_types(structure, declared),
        },
        indent=1,
    )


def main() -> None:
    load_dotenv()  # pick up SUMMARY_LLM etc. from a .env in the working directory
    mcp.run()


if __name__ == "__main__":
    main()
